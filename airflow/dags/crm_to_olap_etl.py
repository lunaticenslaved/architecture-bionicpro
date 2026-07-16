"""BionicPRO — ETL: CRM (PostgreSQL) → OLAP (ClickHouse) + витрина отчётов.

Задача (Task 2):
1. Извлечь данные о клиентах и их протезах из CRM-системы (PostgreSQL).
2. Загрузить их в OLAP-базу (ClickHouse, БД `olap`).
3. Построить витрину `olap.user_prosthesis_report_mart` — отдельную таблицу
   для сервиса отчётов (Reports API), в которой аналитика по телеметрии
   (`telemetry.events`, ClickHouse) сгруппирована в разрезе клиентов и
   объединена с данными о клиентах из CRM.

Структура витрины спроектирована под основной паттерн доступа Reports API:
«все отчётные строки одного пользователя за период».
  - ORDER BY (subject, prosthesis_serial, event_date) — ключ сортировки
    начинается с идентификатора пользователя (sub из Keycloak), поэтому
    выборка по конкретному пользователю читает минимум гранул;
  - PARTITION BY toYYYYMM(event_date) — отчёты запрашиваются за период,
    старые партиции легко удалять/архивировать (retention, требования
    законодательства к медицинским данным);
  - ReplacingMergeTree(loaded_at) — идемпотентность: повторный запуск DAG
    за тот же интервал не плодит дубликаты, при merge остаётся строка
    с максимальным loaded_at.

Расписание: @hourly. Компания собирает телеметрию в режиме реального
времени, поэтому витрина обновляется каждый час скользящим окном
(MART_WINDOW_DAYS дней назад от логической даты запуска) — свежие данные
доезжают быстро, а повторные пересчёты закрывают поздно приехавшие события.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta

import clickhouse_connect
import psycopg2
import psycopg2.extras
from airflow import DAG
from airflow.operators.python import PythonOperator

log = logging.getLogger(__name__)

# --- Подключения (передаются через environment в docker-compose) ----------
CRM_DSN = os.environ.get(
    "CRM_POSTGRES_DSN",
    "postgresql://crm_user:crm_password@crm_db:5432/crm_db",
)
CH_HOST = os.environ.get("CLICKHOUSE_HOST", "clickhouse")
CH_PORT = int(os.environ.get("CLICKHOUSE_PORT", "8123"))
CH_USER = os.environ.get("CLICKHOUSE_USER", "etl_user")
CH_PASSWORD = os.environ.get("CLICKHOUSE_PASSWORD", "etl_password")

# Скользящее окно пересчёта витрины: захватываем "поздние" события,
# приехавшие после прошлого запуска (протез мог быть офлайн).
MART_WINDOW_DAYS = int(os.environ.get("MART_WINDOW_DAYS", "2"))


def _ch_client():
    return clickhouse_connect.get_client(
        host=CH_HOST, port=CH_PORT, username=CH_USER, password=CH_PASSWORD
    )


# --------------------------------------------------------------------------
# 1. DDL: схема OLAP-базы (идемпотентно, IF NOT EXISTS)
# --------------------------------------------------------------------------
_OLAP_DDL = [
    "CREATE DATABASE IF NOT EXISTS olap",
    # Измерение: клиенты из CRM. ReplacingMergeTree — upsert по subject.
    """
    CREATE TABLE IF NOT EXISTS olap.dim_user
    (
        subject       String,      -- sub из Keycloak (сквозной ID клиента)
        crm_user_id   Int64,
        username      String,
        first_name    String,
        last_name     String,
        display_name  String,
        email         String,
        crm_updated_at DateTime,
        loaded_at     DateTime DEFAULT now()
    )
    ENGINE = ReplacingMergeTree(loaded_at)
    ORDER BY subject
    """,
    # Измерение: протезы клиентов из CRM.
    """
    CREATE TABLE IF NOT EXISTS olap.dim_prosthesis
    (
        prosthesis_serial String,  -- серийный номер устройства
        subject           String,  -- владелец (sub из Keycloak)
        model             String,
        firmware_version  String,
        purchased_at      DateTime,
        loaded_at         DateTime DEFAULT now()
    )
    ENGINE = ReplacingMergeTree(loaded_at)
    ORDER BY prosthesis_serial
    """,
    # Витрина для Reports API: телеметрия, агрегированная по дням
    # в разрезе клиента и протеза + атрибуты клиента из CRM.
    """
    CREATE TABLE IF NOT EXISTS olap.user_prosthesis_report_mart
    (
        subject             String,    -- ключ доступа: пользователь видит только свои строки
        prosthesis_serial   String,
        event_date          Date,
        -- атрибуты клиента (денормализованы из CRM для быстрого отчёта)
        display_name        String,
        email               String,
        model               String,
        firmware_version    String,
        -- агрегаты телеметрии за день
        events_count        UInt64,
        avg_response_ms     Float32,
        p95_response_ms     Float32,
        max_response_ms     Float32,
        slow_events_count   UInt64,    -- события с реакцией > 100 мс
        avg_signal_quality  Float32,
        min_battery_level   UInt8,
        movements_count     UInt64,
        first_event_at      DateTime,
        last_event_at       DateTime,
        loaded_at           DateTime DEFAULT now()
    )
    ENGINE = ReplacingMergeTree(loaded_at)
    PARTITION BY toYYYYMM(event_date)
    ORDER BY (subject, prosthesis_serial, event_date)
    """,
    # Водяной знак ETL: до какого момента данные гарантированно обработаны.
    # Reports API читает его, чтобы не отдавать отчёт за период,
    # который Airflow ещё не загрузил в витрину.
    """
    CREATE TABLE IF NOT EXISTS olap.etl_watermark
    (
        process_name     String,    -- 'user_prosthesis_report_mart'
        processed_up_to  DateTime,  -- верхняя граница обработанного периода
        updated_at       DateTime DEFAULT now()
    )
    ENGINE = ReplacingMergeTree(updated_at)
    ORDER BY process_name
    """,
]


def init_olap_schema(**_context) -> None:
    """Создаёт БД `olap` и таблицы витрины (идемпотентно)."""
    client = _ch_client()
    for ddl in _OLAP_DDL:
        client.command(ddl)
    log.info("OLAP schema is up to date")


# --------------------------------------------------------------------------
# 2. Extract + Load: клиенты из CRM → olap.dim_user
# --------------------------------------------------------------------------
def load_crm_users(**_context) -> None:
    """Извлекает клиентов из CRM (PostgreSQL) и грузит в ClickHouse."""
    with psycopg2.connect(CRM_DSN) as pg:
        with pg.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, subject,
                       COALESCE(username, '')     AS username,
                       COALESCE(first_name, '')   AS first_name,
                       COALESCE(last_name, '')    AS last_name,
                       COALESCE(display_name, '') AS display_name,
                       COALESCE(email, '')        AS email,
                       updated_at
                FROM crm.user_profile
                """
            )
            rows = cur.fetchall()

    if not rows:
        log.warning("CRM has no user profiles yet — nothing to load")
        return

    data = [
        [
            r["subject"],
            int(r["id"]),
            r["username"],
            r["first_name"],
            r["last_name"],
            r["display_name"],
            r["email"],
            r["updated_at"].replace(tzinfo=None),
        ]
        for r in rows
    ]
    client = _ch_client()
    client.insert(
        "olap.dim_user",
        data,
        column_names=[
            "subject", "crm_user_id", "username", "first_name",
            "last_name", "display_name", "email", "crm_updated_at",
        ],
    )
    # Схлопываем дубликаты версий сразу, не дожидаясь фонового merge.
    client.command("OPTIMIZE TABLE olap.dim_user FINAL")
    log.info("Loaded %d CRM users into olap.dim_user", len(data))


# --------------------------------------------------------------------------
# 3. Extract + Load: протезы клиентов из CRM → olap.dim_prosthesis
# --------------------------------------------------------------------------
def load_crm_prostheses(**_context) -> None:
    """Извлекает реестр протезов из CRM и грузит в ClickHouse."""
    with psycopg2.connect(CRM_DSN) as pg:
        with pg.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT serial_number, subject,
                       COALESCE(model, '')            AS model,
                       COALESCE(firmware_version, '') AS firmware_version,
                       purchased_at
                FROM crm.prosthesis
                """
            )
            rows = cur.fetchall()

    if not rows:
        log.warning("CRM has no prostheses yet — nothing to load")
        return

    data = [
        [
            r["serial_number"],
            r["subject"],
            r["model"],
            r["firmware_version"],
            r["purchased_at"].replace(tzinfo=None),
        ]
        for r in rows
    ]
    client = _ch_client()
    client.insert(
        "olap.dim_prosthesis",
        data,
        column_names=[
            "prosthesis_serial", "subject", "model",
            "firmware_version", "purchased_at",
        ],
    )
    client.command("OPTIMIZE TABLE olap.dim_prosthesis FINAL")
    log.info("Loaded %d prostheses into olap.dim_prosthesis", len(data))


# --------------------------------------------------------------------------
# 4. Transform: витрина отчётов (телеметрия × клиенты CRM)
# --------------------------------------------------------------------------
def build_report_mart(**context) -> None:
    """Пересобирает витрину за скользящее окно [ds - N дней; ds].

    Телеметрия агрегируется по дням в разрезе (subject, prosthesis_serial),
    затем денормализуются атрибуты клиента и протеза из CRM-измерений.
    ReplacingMergeTree(loaded_at) заменит прежние версии строк окна.
    """
    logical_date: datetime = context["logical_date"]
    date_to = logical_date.date() + timedelta(days=1)   # включительно "сегодня"
    date_from = logical_date.date() - timedelta(days=MART_WINDOW_DAYS)

    client = _ch_client()
    client.command(
        """
        INSERT INTO olap.user_prosthesis_report_mart
        (
            subject, prosthesis_serial, event_date,
            display_name, email, model, firmware_version,
            events_count, avg_response_ms, p95_response_ms, max_response_ms,
            slow_events_count, avg_signal_quality, min_battery_level,
            movements_count, first_event_at, last_event_at
        )
        SELECT
            t.subject,
            t.prosthesis_serial,
            t.event_date,
            coalesce(u.display_name, '')     AS display_name,
            coalesce(u.email, '')            AS email,
            coalesce(p.model, '')            AS model,
            coalesce(p.firmware_version, '') AS firmware_version,
            t.events_count,
            t.avg_response_ms,
            t.p95_response_ms,
            t.max_response_ms,
            t.slow_events_count,
            t.avg_signal_quality,
            t.min_battery_level,
            t.movements_count,
            t.first_event_at,
            t.last_event_at
        FROM
        (
            SELECT
                subject,
                prosthesis_serial,
                toDate(event_time)                          AS event_date,
                count()                                     AS events_count,
                avg(response_time_ms)                       AS avg_response_ms,
                quantile(0.95)(response_time_ms)            AS p95_response_ms,
                max(response_time_ms)                       AS max_response_ms,
                countIf(response_time_ms > 100)             AS slow_events_count,
                avg(signal_quality)                         AS avg_signal_quality,
                min(battery_level)                          AS min_battery_level,
                countIf(event_type = 'movement')            AS movements_count,
                min(event_time)                             AS first_event_at,
                max(event_time)                             AS last_event_at
            FROM telemetry.events
            WHERE toDate(event_time) >= {date_from:Date}
              AND toDate(event_time) <  {date_to:Date}
            GROUP BY subject, prosthesis_serial, event_date
        ) AS t
        LEFT JOIN (SELECT * FROM olap.dim_user FINAL) AS u
               ON u.subject = t.subject
        LEFT JOIN (SELECT * FROM olap.dim_prosthesis FINAL) AS p
               ON p.prosthesis_serial = t.prosthesis_serial
        """,
        parameters={"date_from": date_from, "date_to": date_to},
    )
    client.command("OPTIMIZE TABLE olap.user_prosthesis_report_mart FINAL")

    # Фиксируем водяной знак: витрина консистентна до logical_date.
    # Reports API не отдаёт данные за период после этой отметки.
    client.command(
        """
        INSERT INTO olap.etl_watermark (process_name, processed_up_to)
        VALUES ('user_prosthesis_report_mart', {watermark:DateTime})
        """,
        parameters={"watermark": logical_date.replace(tzinfo=None)},
    )
    client.command("OPTIMIZE TABLE olap.etl_watermark FINAL")

    total = client.command(
        "SELECT count() FROM olap.user_prosthesis_report_mart FINAL"
    )
    log.info(
        "Report mart rebuilt for window [%s .. %s), watermark=%s, total rows: %s",
        date_from, date_to, logical_date, total,
    )


# --------------------------------------------------------------------------
# DAG
# --------------------------------------------------------------------------
default_args = {
    "owner": "bionicpro-data",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id="crm_to_olap_etl",
    description=(
        "ETL: клиенты из CRM (PostgreSQL) → ClickHouse; "
        "витрина отчётов по телеметрии в разрезе клиентов для Reports API"
    ),
    # Каждый час: телеметрия собирается в реальном времени,
    # отчёты должны быть максимально свежими.
    schedule="@hourly",
    start_date=datetime(2026, 7, 1),
    catchup=False,
    max_active_runs=1,
    default_args=default_args,
    tags=["bionicpro", "etl", "reports"],
) as dag:
    init_schema = PythonOperator(
        task_id="init_olap_schema",
        python_callable=init_olap_schema,
    )

    extract_load_users = PythonOperator(
        task_id="load_crm_users",
        python_callable=load_crm_users,
    )

    extract_load_prostheses = PythonOperator(
        task_id="load_crm_prostheses",
        python_callable=load_crm_prostheses,
    )

    build_mart = PythonOperator(
        task_id="build_report_mart",
        python_callable=build_report_mart,
    )

    init_schema >> [extract_load_users, extract_load_prostheses] >> build_mart

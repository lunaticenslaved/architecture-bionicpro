# Задание 2. Airflow DAG: ETL из CRM в OLAP и витрина для сервиса отчётов

## Задача

Пользователи хотят получать данные о работе своего протеза в виде отчёта.
Для этого нужен отдельный сервис отчётов (Reports API), который берёт данные
из **двух источников** — CRM и DB (телеметрия). Чтобы Reports API не ходил в
операционные базы, данные заранее собираются в OLAP-базу (ClickHouse):

1. Реализовать ETL-процесс на Airflow: извлечь данные из CRM-системы и
   записать их в OLAP-базу.
2. Подготовить **витрину** — отдельную таблицу для сервиса отчётов,
   объединяющую аналитику по телеметрии (в разрезе клиентов) с данными
   о клиентах из CRM. Структура витрины должна обеспечивать быстрый доступ
   к данным по пользователям.
3. Настроить расписание сбора данных и подготовки витрины.

Изменения в архитектуре — в [`C4_Containers_to-be.drawio`](C4_Containers_to-be.drawio)
(новые контейнеры: **Reports API**, **OLAP Data Import** (ETL, Airflow DAG),
**OLAP DB** (ClickHouse)).

---

## Архитектура ETL

```mermaid
graph LR
    subgraph Источники
        CRM[(CRM DB\nPostgreSQL\ncrm.user_profile\ncrm.prosthesis)]
        TEL[(Telemetry DB\nClickHouse\ntelemetry.events)]
    end

    subgraph Airflow[Airflow DAG: crm_to_olap_etl — @hourly]
        T1[load_crm_users]
        T2[load_crm_prostheses]
        T3[build_report_mart]
    end

    subgraph OLAP[OLAP DB — ClickHouse]
        DU[(olap.dim_user)]
        DP[(olap.dim_prosthesis)]
        MART[(olap.user_prosthesis_report_mart)]
    end

    CRM --> T1 --> DU
    CRM --> T2 --> DP
    TEL --> T3
    DU --> T3
    DP --> T3
    T3 --> MART
    MART --> R[Reports API]
```

Граф задач DAG:

```
init_olap_schema ──► load_crm_users ──────────┐
                 └─► load_crm_prostheses ─────┴─► build_report_mart
```

| Задача | Что делает |
|---|---|
| `init_olap_schema` | Идемпотентно создаёт БД `olap` и таблицы (DDL `IF NOT EXISTS`) |
| `load_crm_users` | **E**xtract из PostgreSQL (`crm.user_profile`) → **L**oad в `olap.dim_user` |
| `load_crm_prostheses` | Extract из PostgreSQL (`crm.prosthesis`) → Load в `olap.dim_prosthesis` |
| `build_report_mart` | **T**ransform: агрегирует `telemetry.events` по дням в разрезе (клиент, протез) и JOIN-ит с CRM-измерениями → витрина |

Код DAG: [`airflow/dags/crm_to_olap_etl.py`](../airflow/dags/crm_to_olap_etl.py).

---

## Витрина `olap.user_prosthesis_report_mart`

Одна строка = один день работы одного протеза одного клиента.

| Колонка | Тип | Смысл |
|---|---|---|
| `subject` | String | `sub` пользователя из Keycloak — **ключ доступа** Reports API |
| `prosthesis_serial` | String | серийный номер протеза |
| `event_date` | Date | день агрегации |
| `display_name`, `email` | String | атрибуты клиента (денормализованы из CRM) |
| `model`, `firmware_version` | String | атрибуты протеза из CRM |
| `events_count` | UInt64 | всего событий телеметрии за день |
| `avg_response_ms`, `p95_response_ms`, `max_response_ms` | Float32 | скорость реагирования протеза (цель < 100 мс) |
| `slow_events_count` | UInt64 | событий с реакцией > 100 мс |
| `avg_signal_quality` | Float32 | среднее качество миосигнала (0..1) |
| `min_battery_level` | UInt8 | минимальный заряд батареи за день, % |
| `movements_count` | UInt64 | распознанных движений |
| `first_event_at`, `last_event_at` | DateTime | границы активности за день |
| `loaded_at` | DateTime | версия строки (для ReplacingMergeTree) |

### Почему такая структура — быстрый доступ по пользователям

```sql
ENGINE = ReplacingMergeTree(loaded_at)
PARTITION BY toYYYYMM(event_date)
ORDER BY (subject, prosthesis_serial, event_date)
```

- **`ORDER BY (subject, …)`** — первичный ключ начинается с идентификатора
  пользователя. Основной запрос Reports API
  (`WHERE subject = :sub AND event_date BETWEEN …`) читает только гранулы
  одного пользователя, а не всю таблицу. Это же обеспечивает требование
  безопасности: сервис фильтрует по `sub` из JWT, и данные других
  пользователей физически не попадают в выборку.
- **Денормализация** атрибутов клиента и протеза — отчёт строится одним
  запросом к одной таблице, без JOIN-ов в момент чтения.
- **Дневная агрегация** — витрина на порядки меньше сырой телеметрии
  (~10⁴ событий/день/устройство → 1 строка), отчёт за месяц = ≤31 строка
  на протез.
- **`PARTITION BY` месяц** — отчёты запрашиваются за период; старые партиции
  дёшево удалять/архивировать (retention для медицинских данных).
- **`ReplacingMergeTree(loaded_at)`** — идемпотентность ETL: повторная
  загрузка окна не создаёт дубликатов, выживает строка с максимальным
  `loaded_at`.

---

## Расписание

| Что | Как настроено |
|---|---|
| Сбор данных из CRM + подготовка витрины | `schedule="@hourly"` в DAG `crm_to_olap_etl` |
| Окно пересчёта витрины | скользящее, `MART_WINDOW_DAYS=2` дня назад от логической даты |
| Защита от параллельных пересчётов | `max_active_runs=1`, `catchup=False` |
| Надёжность | `retries=2`, `retry_delay=5m` |

Почему `@hourly`: компания перешла на сбор телеметрии **в режиме реального
времени**, пользователи ждут свежих отчётов — суточная выгрузка устарела.
Час — компромисс между свежестью и нагрузкой на CRM/ClickHouse.
Скользящее окно в 2 дня закрывает «поздние» события (протез был офлайн
и отправил телеметрию позже) — они попадут в витрину при следующих запусках.

Сбор данных и подготовка витрины объединены в один DAG с явной зависимостью
`load_* → build_report_mart`: витрина никогда не строится по устаревшим
измерениям CRM.

---

## Новые компоненты в docker-compose

| Сервис | Порт | Назначение |
|---|---|---|
| `clickhouse` | 8123 / 9000 | БД `telemetry` (источник, сеется демо-данными) + БД `olap` (витрина) |
| `airflow_db` | — | PostgreSQL с метаданными Airflow |
| `airflow-init` | — | одноразовая инициализация (миграции + admin/admin) |
| `airflow-scheduler` | — | планировщик (LocalExecutor) |
| `airflow-webserver` | 8082 | UI Airflow — <http://localhost:8082> (admin/admin) |

Также к `crm_db` подключён init-скрипт
[`crm-db/init/01_crm_seed.sql`](../crm-db/init/01_crm_seed.sql):
таблица `crm.prosthesis` (реестр протезов) + демо-клиенты. Телеметрия сеется
скриптом [`clickhouse/init/01_telemetry_schema_and_seed.sql`](../clickhouse/init/01_telemetry_schema_and_seed.sql)
(3 протеза × 7 дней событий). `subject`-ы в обоих сидах совпадают.

> ⚠️ Init-скрипты Postgres/ClickHouse выполняются только при **первом** старте
> (пустые тома). Если стек уже запускался: `docker-compose down -v` (данные
> Keycloak в `./postgres-keycloak-data` при этом тоже потребуют переимпорта —
> см. корневой README).

---

## Как проверить

```bash
# 1. Поднять аналитический контур
docker-compose up -d clickhouse crm_db airflow_db airflow-init \
                     airflow-scheduler airflow-webserver

# 2. Дождаться, пока webserver ответит (1–2 мин: pip-зависимости + миграции)
curl -sf http://localhost:8082/health

# 3. Включить и запустить DAG (или через UI http://localhost:8082, admin/admin)
docker exec bionicpro-airflow-scheduler airflow dags unpause crm_to_olap_etl
docker exec bionicpro-airflow-scheduler airflow dags trigger crm_to_olap_etl

# 4. Статус запусков
docker exec bionicpro-airflow-scheduler airflow dags list-runs -d crm_to_olap_etl

# 5. Проверить измерения CRM в OLAP
docker exec bionicpro-clickhouse clickhouse-client \
  -u etl_user --password etl_password \
  -q "SELECT subject, display_name, email FROM olap.dim_user FINAL"

# 6. Проверить витрину: агрегаты телеметрии в разрезе клиентов
docker exec bionicpro-clickhouse clickhouse-client \
  -u etl_user --password etl_password \
  -q "SELECT subject, prosthesis_serial, event_date, events_count,
             round(avg_response_ms, 1) AS avg_ms, slow_events_count,
             display_name, model
      FROM olap.user_prosthesis_report_mart FINAL
      ORDER BY subject, event_date
      FORMAT PrettyCompact"

# 7. Запрос «как Reports API» — данные только одного пользователя
docker exec bionicpro-clickhouse clickhouse-client \
  -u etl_user --password etl_password \
  -q "SELECT event_date, events_count, round(p95_response_ms,1) AS p95_ms,
             min_battery_level
      FROM olap.user_prosthesis_report_mart FINAL
      WHERE subject = '11111111-1111-1111-1111-111111111111'
      ORDER BY event_date FORMAT PrettyCompact"
```

✅ Ожидание: DAG `crm_to_olap_etl` виден в UI с расписанием `@hourly`,
запуск проходит успешно (4 задачи), в `olap.dim_user` — 3 клиента из CRM,
в витрине — дневные агрегаты телеметрии по каждому клиенту, запрос по
конкретному `subject` возвращает только его строки.

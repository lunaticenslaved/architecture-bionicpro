# Задание 2. ETL (Airflow) + Reports API

## Задача

Пользователи хотят отчёты о работе протеза. Reports API не должен ходить в
операционные базы — данные заранее собираются в OLAP (ClickHouse) ETL-процессом
на Airflow, который также готовит **витрину** для быстрого доступа по пользователям.

Архитектура: [`C4_Containers_to-be.drawio`](C4_Containers_to-be.drawio)
(новые контейнеры: Reports API, OLAP Data Import (Airflow DAG), OLAP DB).

## ETL: DAG `crm_to_olap_etl`

Код: [`airflow/dags/crm_to_olap_etl.py`](../airflow/dags/crm_to_olap_etl.py).

```
init_olap_schema ──► load_crm_users ──────────┐
                 └─► load_crm_prostheses ─────┴─► build_report_mart
```

| Задача | Что делает |
|---|---|
| `init_olap_schema` | Идемпотентный DDL (`IF NOT EXISTS`) |
| `load_crm_users` | PostgreSQL `crm.user_profile` → `olap.dim_user` |
| `load_crm_prostheses` | PostgreSQL `crm.prosthesis` → `olap.dim_prosthesis` |
| `build_report_mart` | Агрегация `telemetry.events` по дням + JOIN с CRM → витрина |

**Расписание**: `@hourly` (телеметрия собирается в realtime, суточная выгрузка
устарела), скользящее окно 2 дня (закрывает «поздние» события офлайн-протезов),
`max_active_runs=1`, `retries=2`.

## Витрина `olap.user_prosthesis_report_mart`

Одна строка = день работы одного протеза одного клиента: `subject`,
`prosthesis_serial`, `event_date`, денормализованные атрибуты клиента/протеза
из CRM, дневные агрегаты телеметрии (events_count, avg/p95/max response,
slow_events, signal_quality, battery, movements).

```sql
ENGINE = ReplacingMergeTree(loaded_at)
PARTITION BY toYYYYMM(event_date)
ORDER BY (subject, prosthesis_serial, event_date)
```

- `ORDER BY (subject, …)` — запрос Reports API читает только гранулы одного
  пользователя; фильтрация по `sub` из JWT физически ограничивает выборку.
- Денормализация — отчёт одним запросом без JOIN-ов на чтении.
- Дневная агрегация — витрина на порядки меньше сырой телеметрии.
- `ReplacingMergeTree(loaded_at)` — идемпотентность повторных загрузок.

## Reports API

Сервис [`reports-api`](../reports-api/) (FastAPI, `localhost:8091`) — upstream
для Auth Proxy (`/api/*`). `GET /reports?days=30&format=csv|json`.

Требования безопасности и их выполнение:

1. **Без вычислений на лету** — читает готовые строки витрины по первичному ключу.
2. **Только аутентифицированные** — JWKS-валидация JWT (RS256, exp, iss);
   без токена `401`.
3. **Только свой отчёт** — `subject` берётся из claim `sub` JWT, не из запроса.
   Нужна роль `prothetic_user`; `?subject=<чужой>` разрешён только роли
   `administrator` (поддержка клиентов), иначе `403`.
4. **Только обработанный Airflow период** — DAG пишет водяной знак в
   `olap.etl_watermark`; если знака нет → `409` («отчёт не готов»), иначе
   `date_to = min(today, watermark)`. Актуальность — в заголовке
   `X-Report-Processed-Up-To`.

UI: кнопка **Download Report** в
[`ReportPage.tsx`](../frontend/src/components/ReportPage.tsx) — запрос через
Auth Proxy, обработка 401/403/409.

## Как проверить

```bash
docker-compose up -d --build   # дождаться Keycloak и Airflow

# 1. Прогнать ETL
docker exec bionicpro-airflow-scheduler airflow dags unpause crm_to_olap_etl
docker exec bionicpro-airflow-scheduler airflow dags trigger crm_to_olap_etl
docker exec bionicpro-airflow-scheduler airflow dags list-runs -d crm_to_olap_etl

# 2. Проверить витрину
docker exec bionicpro-clickhouse clickhouse-client -u etl_user --password etl_password \
  -q "SELECT subject, event_date, events_count, display_name, model
      FROM olap.user_prosthesis_report_mart FINAL
      ORDER BY subject, event_date FORMAT PrettyCompact"

# 3. Reports API без токена → 401
curl -i http://localhost:8091/reports

# 4. Через UI: http://localhost:3000 → Login (prothetic1/prothetic123 + OTP)
#    → Download Report → CSV только с данными текущего пользователя
#    user1/password123 (без роли prothetic_user) → 403
```

> Демо-сиды CRM/телеметрии используют фиксированные `subject` (`1111…`, `2222…`,
> `3333…`); для непустого отчёта под реальным пользователем добавьте телеметрию
> с его `sub`.

> ⚠️ Init-скрипты Postgres/ClickHouse выполняются только при первом старте
> (пустые тома): при необходимости `docker-compose down -v`.

-- BionicPRO — CDC-приёмник (Debezium → Kafka → ClickHouse).
-- Task 4: потоковая доставка изменений CRM (user_profile, prosthesis)
-- через WAL PostgreSQL → Debezium → Kafka → ClickHouse.
--
-- Скрипт выполняется одноразовым сервисом clickhouse-cdc-init
-- после готовности Kafka и ClickHouse.

-- ===================================================================
-- 1. База данных CDC
-- ===================================================================
CREATE DATABASE IF NOT EXISTS cdc;

-- ===================================================================
-- 2. KafkaEngine-таблицы — потребители топиков Debezium
-- ===================================================================

-- Таблица-потребитель топика crm.crm.user_profile
CREATE TABLE IF NOT EXISTS cdc.kafka_user_profile
(
    id              Int32,
    subject         String,
    provider        String,
    external_id     String,
    username        String,
    first_name      String,
    last_name       String,
    display_name    String,
    email           String,
    created_at      String,   -- ISO-8601 от Debezium, парсим в MV
    updated_at      String,
    __op            String,   -- 'c'=create, 'u'=update, 'd'=delete
    __ts_ms         Int64,    -- миллисекунды эпохи (версия строки)
    __deleted       Boolean   -- true для DELETE (rewrite mode)
)
ENGINE = Kafka(
    'kafka:9092',
    'crm.crm.user_profile',
    'clickhouse-cdc-user-profile',
    'JSONEachRow'
)
SETTINGS kafka_thread_per_consumer = 1,
         kafka_num_consumers = 1;

-- Таблица-потребитель топика crm.crm.prosthesis
CREATE TABLE IF NOT EXISTS cdc.kafka_prosthesis
(
    id                Int32,
    serial_number     String,
    subject           String,
    model             String,
    firmware_version  String,
    purchased_at      String,   -- ISO-8601 от Debezium, парсим в MV
    __op              String,
    __ts_ms           Int64,
    __deleted         Boolean
)
ENGINE = Kafka(
    'kafka:9092',
    'crm.crm.prosthesis',
    'clickhouse-cdc-prosthesis',
    'JSONEachRow'
)
SETTINGS kafka_thread_per_consumer = 1,
         kafka_num_consumers = 1;

-- ===================================================================
-- 3. Целевые ReplacingMergeTree-таблицы (версионированные по __ts_ms)
-- ===================================================================

-- Измерение: профили пользователей
CREATE TABLE IF NOT EXISTS cdc.user_profile
(
    id              Int32,
    subject         String,
    provider        String,
    external_id     String,
    username        String,
    first_name      String,
    last_name       String,
    display_name    String,
    email           String,
    created_at      DateTime,
    updated_at      DateTime,
    __ts_ms         Int64,      -- версия строки (монотонно растёт)
    is_deleted      UInt8       -- 1 = строка удалена в CRM
)
ENGINE = ReplacingMergeTree(__ts_ms)
ORDER BY (subject)
SETTINGS index_granularity = 8192;

-- Измерение: протезы
CREATE TABLE IF NOT EXISTS cdc.prosthesis
(
    id                Int32,
    serial_number     String,
    subject           String,
    model             String,
    firmware_version  String,
    purchased_at      DateTime,
    __ts_ms           Int64,
    is_deleted        UInt8
)
ENGINE = ReplacingMergeTree(__ts_ms)
ORDER BY (serial_number)
SETTINGS index_granularity = 8192;

-- ===================================================================
-- 4. MaterializedView: Kafka → целевые таблицы
-- ===================================================================

-- MV: user_profile из Kafka в ReplacingMergeTree
CREATE MATERIALIZED VIEW IF NOT EXISTS cdc.mv_user_profile
TO cdc.user_profile
AS SELECT
    id,
    subject,
    provider,
    external_id,
    username,
    first_name,
    last_name,
    display_name,
    email,
    parseDateTimeBestEffortOrZero(created_at) AS created_at,
    parseDateTimeBestEffortOrZero(updated_at) AS updated_at,
    __ts_ms,
    if(__deleted, 1, 0) AS is_deleted
FROM cdc.kafka_user_profile
WHERE __ts_ms > 0;

-- MV: prosthesis из Kafka в ReplacingMergeTree
CREATE MATERIALIZED VIEW IF NOT EXISTS cdc.mv_prosthesis
TO cdc.prosthesis
AS SELECT
    id,
    serial_number,
    subject,
    model,
    firmware_version,
    parseDateTimeBestEffortOrZero(purchased_at) AS purchased_at,
    __ts_ms,
    if(__deleted, 1, 0) AS is_deleted
FROM cdc.kafka_prosthesis
WHERE __ts_ms > 0;

-- ===================================================================
-- 5. Агрегаты телеметрии (olap.telemetry_daily_agg)
-- ===================================================================

-- Агрегирующая таблица: суточные метрики по (subject, prosthesis_serial)
CREATE TABLE IF NOT EXISTS olap.telemetry_daily_agg
(
    subject             String,
    prosthesis_serial   String,
    event_date          Date,
    events_count        AggregateFunction(count, UInt64),
    avg_response_ms     AggregateFunction(avg, Float64),
    p95_response_ms     AggregateFunction(quantile(0.95), Float64),
    max_response_ms     AggregateFunction(max, UInt16),
    slow_events_count   AggregateFunction(countIf, UInt8),
    avg_signal_quality  AggregateFunction(avg, Float64),
    min_battery_level   AggregateFunction(min, UInt8),
    movements_count     AggregateFunction(countIf, UInt8),
    first_event_at      AggregateFunction(min, DateTime),
    last_event_at       AggregateFunction(max, DateTime)
)
ENGINE = AggregatingMergeTree
ORDER BY (subject, prosthesis_serial, event_date)
SETTINGS index_granularity = 8192;

-- MV: telemetry.events → olap.telemetry_daily_agg (новые вставки)
CREATE MATERIALIZED VIEW IF NOT EXISTS olap.mv_telemetry_daily_agg
TO olap.telemetry_daily_agg
AS SELECT
    subject,
    prosthesis_serial,
    toDate(event_time) AS event_date,
    countState(toUInt64(1))                                          AS events_count,
    avgState(toFloat64(response_time_ms))                            AS avg_response_ms,
    quantileState(0.95)(toFloat64(response_time_ms))                 AS p95_response_ms,
    maxState(response_time_ms)                                       AS max_response_ms,
    countIfState(response_time_ms > 100)                             AS slow_events_count,
    avgState(toFloat64(signal_quality))                              AS avg_signal_quality,
    minState(battery_level)                                          AS min_battery_level,
    countIfState(event_type = 'movement')                            AS movements_count,
    minState(event_time)                                             AS first_event_at,
    maxState(event_time)                                             AS last_event_at
FROM telemetry.events
GROUP BY subject, prosthesis_serial, event_date;

-- Backfill: агрегация существующих событий (одноразово, идемпотентно)
INSERT INTO olap.telemetry_daily_agg
SELECT
    subject,
    prosthesis_serial,
    toDate(event_time) AS event_date,
    countState(toUInt64(1)),
    avgState(toFloat64(response_time_ms)),
    quantileState(0.95)(toFloat64(response_time_ms)),
    maxState(response_time_ms),
    countIfState(response_time_ms > 100),
    avgState(toFloat64(signal_quality)),
    minState(battery_level),
    countIfState(event_type = 'movement'),
    minState(event_time),
    maxState(event_time)
FROM telemetry.events
GROUP BY subject, prosthesis_serial, event_date;

-- ===================================================================
-- 6. Витрина отчётов: olap.user_prosthesis_report_mart_v2
-- ===================================================================

-- Финализированная витрина: объединяет CDC-измерения с агрегатами телеметрии.
-- VIEW уже финализирован — запросы без FINAL.
CREATE OR REPLACE VIEW olap.user_prosthesis_report_mart_v2
AS SELECT
    a.event_date,
    a.prosthesis_serial,
    p.model,
    p.firmware_version,
    u.display_name,
    u.email,
    a.subject,
    countMerge(a.events_count)          AS events_count,
    avgMerge(a.avg_response_ms)         AS avg_response_ms,
    quantileMerge(a.p95_response_ms)    AS p95_response_ms,
    maxMerge(a.max_response_ms)         AS max_response_ms,
    countIfMerge(a.slow_events_count)   AS slow_events_count,
    avgMerge(a.avg_signal_quality)      AS avg_signal_quality,
    minMerge(a.min_battery_level)       AS min_battery_level,
    countIfMerge(a.movements_count)     AS movements_count,
    minMerge(a.first_event_at)          AS first_event_at,
    maxMerge(a.last_event_at)           AS last_event_at
FROM olap.telemetry_daily_agg AS a
LEFT JOIN cdc.user_profile FINAL AS u
    ON a.subject = u.subject AND u.is_deleted = 0
LEFT JOIN cdc.prosthesis FINAL AS p
    ON a.prosthesis_serial = p.serial_number AND p.is_deleted = 0
GROUP BY
    a.event_date,
    a.prosthesis_serial,
    p.model,
    p.firmware_version,
    u.display_name,
    u.email,
    a.subject
ORDER BY a.subject, a.prosthesis_serial, a.event_date;

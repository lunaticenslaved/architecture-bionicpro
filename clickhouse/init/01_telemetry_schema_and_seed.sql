-- BionicPRO — Telemetry DB (ClickHouse).
-- Источник «DB» из задания: сырая телеметрия протезов, которую чип
-- отправляет через 4G-модуль в режиме реального времени (Telemetry API).
-- Скрипт выполняется однократно при первом старте контейнера
-- (пустой том clickhouse_data).

CREATE DATABASE IF NOT EXISTS telemetry;

-- Сырые события телеметрии. Партиционирование по месяцам, сортировка
-- по устройству и времени — типовые запросы ETL идут диапазоном по времени.
CREATE TABLE IF NOT EXISTS telemetry.events
(
    subject            String,      -- sub пользователя из Keycloak (владелец протеза)
    prosthesis_serial  String,      -- серийный номер протеза
    event_time         DateTime,
    event_type         LowCardinality(String),  -- movement / calibration / heartbeat
    response_time_ms   UInt16,      -- скорость реагирования (цель < 100 мс)
    signal_quality     Float32,     -- качество миосигнала 0..1
    battery_level      UInt8        -- заряд батареи, %
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(event_time)
ORDER BY (prosthesis_serial, event_time);

-- Демо-данные: 3 протеза, событие каждые 90 секунд за последние 7 дней
-- (6720 событий на устройство). Subjects совпадают с сидом CRM.
INSERT INTO telemetry.events
SELECT
    pair.1                                                        AS subject,
    pair.2                                                        AS prosthesis_serial,
    now() - INTERVAL 7 DAY + toIntervalSecond(number * 90)        AS event_time,
    ['movement', 'movement', 'heartbeat', 'calibration'][(number % 4) + 1] AS event_type,
    toUInt16(40 + (cityHash64(pair.2, number) % 120))             AS response_time_ms,
    toFloat32(0.5 + (cityHash64(pair.2, number, 1) % 50) / 100)   AS signal_quality,
    toUInt8(20 + (cityHash64(pair.2, number, 2) % 80))            AS battery_level
FROM numbers(6720)
ARRAY JOIN
    [
        ('11111111-1111-1111-1111-111111111111', 'BP-ARM-0001'),
        ('22222222-2222-2222-2222-222222222222', 'BP-ARM-0002'),
        ('33333333-3333-3333-3333-333333333333', 'BP-LEG-0001')
    ] AS pair;

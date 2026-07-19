-- BionicPRO — сид CRM DB (PostgreSQL) для демонстрации ETL (Task 2).
-- Выполняется однократно при первом старте контейнера crm_db
-- (пустой том crm_db_data). Схема совпадает с crm-api/app/db.py.

CREATE SCHEMA IF NOT EXISTS crm;

-- REPLICA IDENTITY FULL нужен для Debezium CDC:
-- при DEFAULT PostgreSQL пишет в WAL для DELETE только PK (id),
-- все остальные поля (subject, display_name и т.д.) будут NULL.
-- FULL заставляет PostgreSQL включать ВСЕ столбцы в before-image.

CREATE TABLE IF NOT EXISTS crm.user_profile (
    id            SERIAL PRIMARY KEY,
    subject       TEXT NOT NULL UNIQUE,      -- sub из access_token Keycloak
    provider      TEXT NOT NULL,             -- 'yandex'
    external_id   TEXT,
    username      TEXT,
    first_name    TEXT,
    last_name     TEXT,
    display_name  TEXT,
    email         TEXT,
    raw_profile   JSONB,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE crm.user_profile REPLICA IDENTITY FULL;

CREATE TABLE IF NOT EXISTS crm.user_consent (
    id            SERIAL PRIMARY KEY,
    subject       TEXT NOT NULL,
    provider      TEXT NOT NULL,
    scope         TEXT NOT NULL,
    granted       BOOLEAN NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_user_consent_subject
    ON crm.user_consent (subject);

-- Реестр протезов: какой протез принадлежит какому клиенту.
-- Источник данных для olap.dim_prosthesis.
CREATE TABLE IF NOT EXISTS crm.prosthesis (
    id                SERIAL PRIMARY KEY,
    serial_number     TEXT NOT NULL UNIQUE,
    subject           TEXT NOT NULL REFERENCES crm.user_profile (subject),
    model             TEXT,
    firmware_version  TEXT,
    purchased_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE crm.prosthesis REPLICA IDENTITY FULL;

CREATE INDEX IF NOT EXISTS idx_prosthesis_subject
    ON crm.prosthesis (subject);

-- --- Демо-клиенты (subjects совпадают с сидом телеметрии в ClickHouse) ---
INSERT INTO crm.user_profile
    (subject, provider, external_id, username, first_name, last_name, display_name, email)
VALUES
    ('11111111-1111-1111-1111-111111111111', 'keycloak', '1001',
     'john.doe',  'John',  'Doe',     'John Doe',      'john.doe@example.com'),
    ('22222222-2222-2222-2222-222222222222', 'keycloak', '1002',
     'jane.smith', 'Jane', 'Smith',   'Jane Smith',    'jane.smith@example.com'),
    ('33333333-3333-3333-3333-333333333333', 'keycloak', '1003',
     'alex.johnson', 'Alex', 'Johnson', 'Alex Johnson', 'alex.johnson@example.com')
ON CONFLICT (subject) DO NOTHING;

INSERT INTO crm.prosthesis
    (serial_number, subject, model, firmware_version, purchased_at)
VALUES
    ('BP-ARM-0001', '11111111-1111-1111-1111-111111111111',
     'BionicARM Pro X1', '2.4.1', now() - INTERVAL '90 days'),
    ('BP-ARM-0002', '22222222-2222-2222-2222-222222222222',
     'BionicARM Pro X1', '2.3.7', now() - INTERVAL '180 days'),
    ('BP-LEG-0001', '33333333-3333-3333-3333-333333333333',
     'BionicLEG Ultra',  '1.9.0', now() - INTERVAL '45 days')
ON CONFLICT (serial_number) DO NOTHING;

#!/bin/bash
# BionicPRO — Docker entrypoint wrapper for Debezium Connect.
# Запускает Kafka Connect в фоне, дожидается готовности,
# инициализирует коннекторы, затем переключается на процесс Connect.

set -euo pipefail

# Запускаем стандартный entrypoint Debezium Connect в фоне
# Оригинальный entrypoint — /docker-entrypoint.sh start
# Он запускает connect-distributed и ждёт.
# Мы запускаем его в фоне, чтобы иметь возможность выполнить init.
echo "[wrapper] Starting Kafka Connect via original entrypoint..."
/docker-entrypoint.sh start &
CONNECT_PID=$!

echo "[wrapper] Kafka Connect started with PID=${CONNECT_PID}"

# Запускаем инициализацию коннекторов
/usr/local/bin/init-connector.sh

echo "[wrapper] Connector initialization complete. Waiting for Kafka Connect process..."

# Переключаемся на процесс Connect, чтобы контейнер жил с ним
wait ${CONNECT_PID}

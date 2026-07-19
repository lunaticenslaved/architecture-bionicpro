#!/bin/bash
# BionicPRO — Debezium Connect entrypoint.
# Запускает Kafka Connect в фоне, ждёт готовности REST API,
# регистрирует коннектор, затем переключается на процесс Connect.

set -euo pipefail

CONNECT_URL="http://localhost:8083"
CONNECTOR_CONFIG="/crm-connector.json"
CONNECTOR_NAME="crm-connector"

# Стандартный entrypoint Debezium Connect — запускает Connect в foreground.
# Нам нужно запустить его в фоне, чтобы иметь возможность зарегистрировать коннектор.
# Используем ту же команду, что и оригинальный entrypoint.
echo "Starting Kafka Connect in background..."
/opt/kafka/bin/connect-distributed.sh /opt/kafka/config/connect-distributed.properties &
CONNECT_PID=$!

echo "Waiting for Kafka Connect REST API at ${CONNECT_URL}..."

# Ждём, пока Connect API не начнёт отвечать
for i in $(seq 1 30); do
  if curl -sf "${CONNECT_URL}/connectors" > /dev/null 2>&1; then
    echo "Kafka Connect is ready (attempt $i)."
    break
  fi
  echo "  Not ready yet (attempt $i/30), retrying in 3s..."
  sleep 3
done

# Регистрируем коннектор (PUT — идемпотентно, создаёт или обновляет)
echo "Registering connector '${CONNECTOR_NAME}'..."
HTTP_CODE=$(curl -s -o /tmp/connector_response.json -w "%{http_code}" \
  -X PUT \
  -H "Content-Type: application/json" \
  --data-binary "@${CONNECTOR_CONFIG}" \
  "${CONNECT_URL}/connectors/${CONNECTOR_NAME}/config"
)

echo "HTTP response code: ${HTTP_CODE}"
cat /tmp/connector_response.json
echo ""

if [ "${HTTP_CODE}" -ge 200 ] && [ "${HTTP_CODE}" -lt 300 ]; then
  echo "Connector '${CONNECTOR_NAME}' registered successfully."
else
  echo "WARNING: Failed to register connector '${CONNECTOR_NAME}' (will retry on restart)."
fi

# Проверяем статус коннектора
echo "Checking connector status..."
sleep 2
curl -sf "${CONNECT_URL}/connectors/${CONNECTOR_NAME}/status" | python3 -m json.tool 2>/dev/null || \
  curl -sf "${CONNECT_URL}/connectors/${CONNECTOR_NAME}/status"

echo ""
echo "Init complete. Switching to Kafka Connect process (PID=${CONNECT_PID})..."

# Переключаемся на процесс Connect, чтобы контейнер жил с ним
wait ${CONNECT_PID}

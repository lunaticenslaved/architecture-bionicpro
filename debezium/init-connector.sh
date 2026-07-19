#!/bin/bash
# BionicPRO — Debezium connector init script.
# Регистрирует все коннекторы из массива CONNECTORS через REST API Kafka Connect.
# Запускается после того, как Kafka Connect полностью загрузился.

set -euo pipefail

CONNECT_URL="http://localhost:8083"

# Массив коннекторов: имя_коннектора:путь_к_конфигу
CONNECTORS=(
  "crm-connector:/tmp/crm-connector-config.json"
)

echo "[init-connector] Waiting for Kafka Connect REST API at ${CONNECT_URL}..."

# Ждём готовности Connect API
for i in $(seq 1 30); do
  if curl -sf "${CONNECT_URL}/connectors" > /dev/null 2>&1; then
    echo "[init-connector] Kafka Connect is ready (attempt $i)."
    break
  fi
  echo "[init-connector] Not ready yet (attempt $i/30), retrying in 3s..."
  sleep 3
done

# Регистрируем каждый коннектор из массива
for connector_entry in "${CONNECTORS[@]}"; do
  IFS=':' read -r CONNECTOR_NAME CONNECTOR_CONFIG <<< "${connector_entry}"

  echo "[init-connector] Registering connector '${CONNECTOR_NAME}' from ${CONNECTOR_CONFIG}..."

  HTTP_CODE=$(curl -s -o /tmp/connector_response.json -w "%{http_code}" \
    -X PUT \
    -H "Content-Type: application/json" \
    --data-binary "@${CONNECTOR_CONFIG}" \
    "${CONNECT_URL}/connectors/${CONNECTOR_NAME}/config"
  )

  echo "[init-connector] HTTP response code: ${HTTP_CODE}"
  cat /tmp/connector_response.json
  echo ""

  if [ "${HTTP_CODE}" -ge 200 ] && [ "${HTTP_CODE}" -lt 300 ]; then
    echo "[init-connector] Connector '${CONNECTOR_NAME}' registered successfully."
  else
    echo "[init-connector] WARNING: Failed to register connector '${CONNECTOR_NAME}'."
  fi
done

# Проверяем статус всех коннекторов
echo ""
echo "[init-connector] Checking all connectors status..."
sleep 2
curl -sf "${CONNECT_URL}/connectors?expand=status" | jq . 2>/dev/null || \
  curl -sf "${CONNECT_URL}/connectors?expand=status"

echo ""
echo "[init-connector] Init complete."

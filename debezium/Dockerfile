FROM debezium/connect:2.7.3.Final

USER root

# Устанавливаем curl для healthcheck и python3 для форматирования JSON
RUN microdnf install -y curl python3 && microdnf clean all

# Копируем скрипт инициализации и конфиг коннектора
COPY init.sh /init.sh
COPY crm-connector.json /crm-connector.json

RUN chmod +x /init.sh

USER kafka

ENTRYPOINT ["/init.sh"]

HEALTHCHECK --interval=10s --timeout=5s --retries=20 --start-period=30s \
  CMD curl -f http://localhost:8083/connectors || exit 1

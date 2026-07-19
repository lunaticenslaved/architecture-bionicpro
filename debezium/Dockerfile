FROM debezium/connect:2.7.3.Final

USER root
RUN microdnf install -y jq curl && microdnf clean all
USER kafka

COPY --chown=kafka:kafka crm-connector-config.json /tmp/crm-connector-config.json
COPY --chown=kafka:kafka telemetry-connector-config.json /tmp/telemetry-connector-config.json

COPY --chown=kafka:kafka init-connector.sh /usr/local/bin/init-connector.sh
RUN chmod +x /usr/local/bin/init-connector.sh

COPY --chown=kafka:kafka docker-entrypoint-wrapper.sh /usr/local/bin/docker-entrypoint-wrapper.sh
RUN chmod +x /usr/local/bin/docker-entrypoint-wrapper.sh

ENTRYPOINT ["/usr/local/bin/docker-entrypoint-wrapper.sh"]
CMD ["start"]

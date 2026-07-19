FROM minio/minio:latest

RUN curl -L https://dl.min.io/client/mc/release/linux-amd64/mc -o /usr/local/bin/mc && \
    chmod +x /usr/local/bin/mc

COPY init.sh /usr/local/bin/init-minio.sh
RUN chmod +x /usr/local/bin/init-minio.sh

COPY reports-policy.json /etc/minio/reports-policy.json

ENV MINIO_ROOT_USER=minio_user
ENV MINIO_ROOT_PASSWORD=minio_password

ENTRYPOINT ["/usr/local/bin/init-minio.sh"]

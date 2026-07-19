#!/bin/bash


echo "Запуск MinIO сервера..."

minio server --console-address ":9001" /data &
MINIO_PID=$!

echo "Ожидание запуска MinIO..."
sleep 30

echo "Ожидание полной инициализации MinIO..."
for i in {1..30}; do
  if mc admin info local > /dev/null 2>&1; then
    echo "✓ MinIO полностью инициализирован (попытка $i)"
    break
  fi
  echo "Ожидание инициализации MinIO... (попытка $i/30)"
  sleep 2
done

echo "Настройка MinIO Client..."
mc alias set local http://localhost:9000 ${MINIO_ROOT_USER} ${MINIO_ROOT_PASSWORD}

echo "Создание бакета bionicpro-reports..."
mc mb local/bionicpro-reports --ignore-existing

echo "Настройка публичного доступа к бакету bionicpro-reports..."
mc anonymous set download local/bionicpro-reports

echo "Настройка TTL (7 дней) для бакета bionicpro-reports..."
cat > /tmp/lifecycle-reports.json <<EOF
{
  "Rules": [
    {
      "ID": "ExpireOldReports",
      "Status": "Enabled",
      "Expiration": {
        "Days": 7
      }
    }
  ]
}
EOF

mc ilm import local/bionicpro-reports < /tmp/lifecycle-reports.json
echo "✓ TTL настроен: файлы старше 7 дней будут автоматически удаляться"

echo "MinIO инициализация завершена"

wait $MINIO_PID

# Задание 3. Кэширование отчётов в S3 + CDN

## Проблема

Пользователи часто запрашивают отчёты, но данные обновляются ETL по расписанию —
повторные запросы возвращают одинаковый результат и зря нагружают ClickHouse.

## Решение

Сформированные отчёты кэшируются в S3 (Minio) и раздаются через CDN
(Nginx с кэшированием):

1. Клиент запрашивает отчёт: фронтенд → `bionicpro-auth` → `reports-api`.
2. `reports-api` проверяет S3 по ключу `{subject}/{date_from}_{date_to}.{format}`:
   - есть — сразу HTTP 302 на CDN URL;
   - нет — генерирует из ClickHouse, кладёт в S3, затем 302 на CDN.
3. Клиент идёт по редиректу на Nginx: **HIT** — из локального кэша,
   **MISS** — забирает из Minio и кэширует.

Структура ключей S3 (`bionicpro-reports/{subject}/{period}.{format}`) даёт
быстрый префиксный поиск по пользователю, уникальность по периоду/формату и
простую инвалидацию.

## Инвалидация кэша

- **Автоматическая**: `Cache-Control: max-age=3600` на объектах S3 +
  `proxy_cache_valid 200 1h` в Nginx — обновление через час.
- **Ручная** (после ETL): `DELETE /reports/cache?subject={sub}[&date_from=...&date_to=...]` —
  удаляет отчёты из S3; следующий запрос сгенерирует свежий.
- **Production**: CDN с purge API (CloudFront/Cloudflare) либо versioning в ключах.

## Развёртывание

| Сервис | Порт | Описание |
|--------|------|----------|
| `minio` | 9002 (API), 9003 (Console) | S3-хранилище |
| `nginx-cdn` | 8083 | Reverse proxy с кэшем |

Изменения: `reports-api` (boto3, логика кэша и 302), `bionicpro-auth`
(проброс Location при редиректах), [`nginx-cdn.conf`](../nginx-cdn.conf).

## Проверка

```bash
docker-compose up -d minio nginx-cdn reports-api bionicpro-auth

curl http://localhost:8083/health                 # Nginx CDN
curl http://localhost:9003/minio/health/live      # Minio

# Первый запрос отчёта — генерация + запись в S3, ответ 302 → CDN URL
# Повторный — тот же CDN URL; заголовок X-Cache-Status: HIT/MISS
curl -I http://localhost:8083/bionicpro-reports/...
```

## Безопасность

- Доступ контролирует `reports-api`: только свой отчёт (админ — любой).
- URL в CDN непредсказуемы (`subject` из JWT) и живут 1 час.
- Для production — presigned URLs с ограниченным сроком действия.

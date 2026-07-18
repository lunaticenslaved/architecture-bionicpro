"""Reports API — сервис отчётов о работе протезов.

GET /reports возвращает подготовленный отчёт по пользователю из витрины
`olap.user_prosthesis_report_mart_v2` (ClickHouse). Витрина наполняется
потоково через CDC (Debezium → Kafka → ClickHouse) — данные CRM
доставляются в реальном времени без массовых SELECT из PostgreSQL.
Агрегаты телеметрии обновляются через MaterializedView.

Безопасность:
- запросы приходят через Auth Proxy (BFF) с Bearer JWT из серверной сессии;
- подпись/exp/iss токена проверяются по JWKS Keycloak (см. auth.py);
- RBAC: нужна realm-роль `prothetic_user`;
- пользователь видит ТОЛЬКО свои данные: фильтр по `sub` из токена,
  идентификатор пользователя из запроса не принимается
  (administrator может указать ?subject= для поддержки клиентов).

Кэширование отчётов в S3:
- Структура хранения в S3: {bucket}/{subject}/{date_from}_{date_to}.{format}
  для быстрого доступа по пользователю и периоду.
- Инвалидация кеша: DELETE /reports/cache?subject=&date_from=&date_to=
  позволяет удалить устаревший отчёт из S3 и CDN purge через Cache-Control.
"""
import json
import logging
from datetime import date, datetime, timedelta
from typing import Literal, Optional

import boto3
import clickhouse_connect
from botocore.config import Config
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from .auth import Principal, require_reports_access
from .config import settings

logger = logging.getLogger(__name__)

app = FastAPI(title="BionicPRO Reports API")

# Колонки отчёта — соответствуют витрине olap.user_prosthesis_report_mart_v2.
_REPORT_COLUMNS = [
    "event_date",
    "prosthesis_serial",
    "model",
    "firmware_version",
    "display_name",
    "email",
    "events_count",
    "avg_response_ms",
    "p95_response_ms",
    "max_response_ms",
    "slow_events_count",
    "avg_signal_quality",
    "min_battery_level",
    "movements_count",
    "first_event_at",
    "last_event_at",
]


# --------------------------------------------------------------------------- #
#  S3 Client
# --------------------------------------------------------------------------- #
def _s3_client():
    """Создаёт клиент S3 для Minio (S3-совместимое хранилище)."""
    return boto3.client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT_URL,
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        region_name="us-east-1",
    )


def _ensure_bucket(s3):
    """Идемпотентно создаёт бакет для отчётов, если он ещё не существует."""
    try:
        s3.head_bucket(Bucket=settings.S3_BUCKET_NAME)
    except Exception:
        s3.create_bucket(Bucket=settings.S3_BUCKET_NAME)


def _report_key(subject: str, date_from: date, date_to: date, fmt: str) -> str:
    """Формирует ключ объекта в S3: {subject}/{date_from}_{date_to}.{fmt}.

    Структура позволяет быстро находить все отчёты пользователя
    и обеспечивает уникальность по периоду и формату.
    """
    return f"{subject}/{date_from}_{date_to}.{fmt}"


def _read_report_from_s3(subject: str, date_from: date, date_to: date, fmt: str) -> bytes:
    """Читает содержимое отчёта из S3."""
    s3 = _s3_client()
    key = _report_key(subject, date_from, date_to, fmt)
    response = s3.get_object(Bucket=settings.S3_BUCKET_NAME, Key=key)
    return response["Body"].read()


def _report_exists_in_s3(subject: str, date_from: date, date_to: date, fmt: str) -> bool:
    """Проверяет наличие отчёта в S3."""
    try:
        s3 = _s3_client()
        _ensure_bucket(s3)
        key = _report_key(subject, date_from, date_to, fmt)
        s3.head_object(Bucket=settings.S3_BUCKET_NAME, Key=key)
        return True
    except Exception as exc:
        logger.debug("Report not found in S3: %s", exc)
        return False


def _upload_report_to_s3(
    subject: str, date_from: date, date_to: date, fmt: str, content: bytes
):
    """Записывает сформированный отчёт в S3 и возвращает CDN URL."""
    s3 = _s3_client()
    _ensure_bucket(s3)
    key = _report_key(subject, date_from, date_to, fmt)
    s3.put_object(
        Bucket=settings.S3_BUCKET_NAME,
        Key=key,
        Body=content,
        ContentType="text/csv" if fmt == "csv" else "application/json",
        CacheControl="max-age=3600, public",  # Кэш в CDN на 1 час
    )
    logger.info("Uploaded report to S3: %s", key)


def _delete_report_from_s3(
    subject: str, date_from: date, date_to: date, fmt: str
) -> bool:
    """Удаляет отчёт из S3 (для инвалидации кеша)."""
    try:
        s3 = _s3_client()
        key = _report_key(subject, date_from, date_to, fmt)
        s3.delete_object(Bucket=settings.S3_BUCKET_NAME, Key=key)
        logger.info("Deleted report from S3: %s", key)
        return True
    except Exception as exc:
        logger.warning("Failed to delete report from S3: %s", exc)
        return False


# --------------------------------------------------------------------------- #
#  ClickHouse
# --------------------------------------------------------------------------- #
def _ch_client():
    return clickhouse_connect.get_client(
        host=settings.CLICKHOUSE_HOST,
        port=settings.CLICKHOUSE_PORT,
        username=settings.CLICKHOUSE_USER,
        password=settings.CLICKHOUSE_PASSWORD,
    )


def _fetch_watermark(client) -> Optional[datetime]:
    """Свежесть данных CDC-потока: максимальный last_event_at из агрегата.

    CDC-поток доставляет изменения почти в реальном времени —
    водяной знак определяется по последнему событию телеметрии
    в агрегате, а не по Airflow-маркеру.
    """
    result = client.query(
        """
        SELECT maxMerge(last_event_at)
        FROM olap.telemetry_daily_agg
        """,
    )
    rows = result.result_rows
    if not rows or rows[0][0] is None:
        return None
    wm = rows[0][0]
    # ClickHouse DateTime «нулевая» = 1970-01-01 → считаем, что данных нет.
    return wm if wm.year > 1970 else None



def _fetch_report_rows(client, subject: str, date_from: date, date_to: date) -> list[dict]:
    """Выборка готовых строк витрины v2 по пользователю.

    Витрина olap.user_prosthesis_report_mart_v2 — это VIEW,
    уже финализированный (содержит -Merge функции). FINAL не требуется.
    """
    result = client.query(
        f"""
        SELECT {", ".join(_REPORT_COLUMNS)}
        FROM olap.user_prosthesis_report_mart_v2
        WHERE subject = {{subject:String}}
          AND event_date >= {{date_from:Date}}
          AND event_date <= {{date_to:Date}}
        ORDER BY event_date, prosthesis_serial
        """,
        parameters={
            "subject": subject,
            "date_from": date_from,
            "date_to": date_to,
        },
    )
    return [dict(zip(result.column_names, row)) for row in result.result_rows]

# --------------------------------------------------------------------------- #
#  Endpoints
# --------------------------------------------------------------------------- #
@app.get("/reports")
async def get_report(
    user: Principal = Depends(require_reports_access),
    days: int = Query(30, ge=1, le=365, description="Период отчёта, дней"),
    subject: Optional[str] = Query(
        None,
        description="Только для роли administrator: отчёт другого пользователя",
    ),
):
    """Отчёт о работе протеза(-ов) текущего пользователя за период в формате JSON.

    Данные берутся из подготовленной витрины OLAP — без тяжёлых
    вычислений в реальном времени.

    Кэширование: если отчёт уже сформирован и лежит в S3,
    сервис читает его из S3 и возвращает JSON.
    """
    # Пользователь может смотреть ТОЛЬКО свой отчёт: subject берём из JWT.
    # Явный параметр ?subject= разрешён только администратору.
    target_subject = user.subject
    if subject is not None and subject != user.subject:
        if not user.is_admin:
            raise HTTPException(
                status_code=403,
                detail="You can only access your own prosthesis reports",
            )
        target_subject = subject

    try:
        client = _ch_client()
        watermark = _fetch_watermark(client)
    except Exception as exc:  # ClickHouse недоступен и т.п.
        raise HTTPException(status_code=503, detail=f"OLAP unavailable: {exc}")

    # Отчёт строится только за период, по которому есть данные в агрегате.
    # CDC-поток доставляет изменения почти в реальном времени — водяной знак
    # определяется по последнему событию телеметрии в olap.telemetry_daily_agg.
    if watermark is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "Report data is not ready yet: the CDC pipeline has not "
                "populated the OLAP mart. Try again later."
            ),
        )

    # Верхнюю границу отчёта прижимаем к водяному знаку: данные после
    # последнего события телеметрии ещё не поступили.
    date_to = min(date.today(), watermark.date())
    date_from = date_to - timedelta(days=days)

    fmt = "json"  # Только JSON формат

    # --------------------------------------------------------------- #
    #  Проверяем наличие отчёта в S3 — если есть, читаем и возвращаем
    # --------------------------------------------------------------- #
    if _report_exists_in_s3(target_subject, date_from, date_to, fmt):
        cached_content = _read_report_from_s3(target_subject, date_from, date_to, fmt)
        report_data = json.loads(cached_content.decode("utf-8"))
        report_data["source"] = "cache"
        logger.info("Serving cached report from S3 for subject=%s", target_subject)
        return JSONResponse(
            report_data,
            headers={
                "X-Report-Source": "s3-cache",
                "X-Report-Processed-Up-To": watermark.isoformat(),
            },
        )

    # --------------------------------------------------------------- #
    #  Отчёта в S3 нет — генерируем из ClickHouse, сохраняем в S3, возвращаем
    # --------------------------------------------------------------- #
    try:
        rows = _fetch_report_rows(client, target_subject, date_from, date_to)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"OLAP unavailable: {exc}")

    report_data = {
        "subject": target_subject,
        "username": user.username,
        "source": "generated",
        "period": {"from": str(date_from), "to": str(date_to)},
        "days": days,
        "processed_up_to": watermark.isoformat(),
        "rows": [
            {k: (str(v) if not isinstance(v, (int, float)) else v)
             for k, v in r.items()}
             for r in rows
        ],
        "total_rows": len(rows),
    }
    content = json.dumps(report_data, ensure_ascii=False, indent=2).encode("utf-8")
    _upload_report_to_s3(target_subject, date_from, date_to, fmt, content)
    logger.info("Generated and cached report in S3 for subject=%s", target_subject)
    return JSONResponse(
        report_data,
        headers={
            "X-Report-Source": "generated",
            "X-Report-Processed-Up-To": watermark.isoformat(),
        },
    )


@app.delete("/reports/cache")
async def invalidate_report_cache(
    user: Principal = Depends(require_reports_access),
    subject: Optional[str] = Query(
        None,
        description="Только для роли administrator: инвалидировать кэш другого пользователя",
    ),
    date_from: Optional[str] = Query(
        None, description="Начало периода (YYYY-MM-DD). Если не указан — удаляем все отчёты пользователя."
    ),
    date_to: Optional[str] = Query(
        None, description="Конец периода (YYYY-MM-DD)."
    ),
    format: Optional[Literal["csv", "json"]] = Query(
        None, description="Формат отчёта. Если не указан — удаляем все форматы."
    ),
):
    """Инвалидация кэша отчётов в S3.

    После ETL-обновления данных администратор может удалить устаревшие
    отчёты из S3, чтобы при следующем запросе они были сгенерированы заново.
    """
    target_subject = user.subject
    if subject is not None and subject != user.subject:
        if not user.is_admin:
            raise HTTPException(
                status_code=403,
                detail="Only administrators can invalidate other users' cache",
            )
        target_subject = subject

    s3 = _s3_client()
    _ensure_bucket(s3)
    prefix = f"{target_subject}/"

    # Получаем список всех ключей с указанным префиксом
    response = s3.list_objects_v2(
        Bucket=settings.S3_BUCKET_NAME,
        Prefix=prefix,
    )
    objects = response.get("Contents", [])

    deleted_count = 0
    for obj in objects:
        key = obj["Key"]
        # Если указаны date_from/date_to/format — фильтруем
        if date_from and date_to:
            expected_base = f"{date_from}_{date_to}"
            if expected_base not in key:
                continue
        if format and not key.endswith(f".{format}"):
            continue
        s3.delete_object(Bucket=settings.S3_BUCKET_NAME, Key=key)
        deleted_count += 1
        logger.info("Invalidated cached report: %s", key)

    return {"deleted": deleted_count, "subject": target_subject}


@app.get("/health")
async def health():
    return {"status": "ok"}

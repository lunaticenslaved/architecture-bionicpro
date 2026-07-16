"""Reports API — сервис отчётов о работе протезов (Task 3).

GET /reports возвращает подготовленный отчёт по пользователю из витрины
`olap.user_prosthesis_report_mart` (ClickHouse). Витрину заранее готовит
Airflow DAG `crm_to_olap_etl` (Task 2): телеметрия уже агрегирована по дням
в разрезе (клиент, протез) и обогащена данными CRM — сервису не нужны
сложные вычисления в реальном времени, только выборка готовых строк
по первичному ключу (subject, ...).

Безопасность:
- запросы приходят через Auth Proxy (BFF) с Bearer JWT из серверной сессии;
- подпись/exp/iss токена проверяются по JWKS Keycloak (см. auth.py);
- RBAC: нужна realm-роль `prothetic_user`;
- пользователь видит ТОЛЬКО свои данные: фильтр по `sub` из токена,
  идентификатор пользователя из запроса не принимается
  (administrator может указать ?subject= для поддержки клиентов).
"""
import csv
import io
from datetime import date, datetime, timedelta
from typing import Literal, Optional

import clickhouse_connect
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse

from .auth import Principal, require_reports_access
from .config import settings

_WATERMARK_PROCESS = "user_prosthesis_report_mart"

app = FastAPI(title="BionicPRO Reports API")

# Колонки отчёта — соответствуют витрине olap.user_prosthesis_report_mart.
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


def _ch_client():
    return clickhouse_connect.get_client(
        host=settings.CLICKHOUSE_HOST,
        port=settings.CLICKHOUSE_PORT,
        username=settings.CLICKHOUSE_USER,
        password=settings.CLICKHOUSE_PASSWORD,
    )


def _fetch_watermark(client) -> Optional[datetime]:
    """До какого момента данные обработаны Airflow (olap.etl_watermark).

    Пользователь может запросить данные, которых ещё нет в OLAP, —
    отчёт строится только за период, закрытый ETL-процессом.
    """
    result = client.query(
        """
        SELECT max(processed_up_to)
        FROM olap.etl_watermark FINAL
        WHERE process_name = {p:String}
        """,
        parameters={"p": _WATERMARK_PROCESS},
    )
    rows = result.result_rows
    if not rows or rows[0][0] is None:
        return None
    wm = rows[0][0]
    # ClickHouse DateTime «нулевая» = 1970-01-01 → считаем, что данных нет.
    return wm if wm.year > 1970 else None


def _fetch_report_rows(client, subject: str, date_from: date, date_to: date) -> list[dict]:
    """Выборка готовых строк витрины по пользователю.

    Витрина ORDER BY (subject, prosthesis_serial, event_date) — запрос
    по конкретному subject читает только его гранулы, БЕЗ агрегаций
    в реальном времени. FINAL схлопывает версии ReplacingMergeTree.
    """
    result = client.query(
        f"""
        SELECT {", ".join(_REPORT_COLUMNS)}
        FROM olap.user_prosthesis_report_mart FINAL
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


def _to_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_REPORT_COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


@app.get("/reports")
async def get_report(
    user: Principal = Depends(require_reports_access),
    days: int = Query(30, ge=1, le=365, description="Период отчёта, дней"),
    format: Literal["csv", "json"] = Query(
        "csv", description="Формат ответа: csv (файл) или json"
    ),
    subject: Optional[str] = Query(
        None,
        description="Только для роли administrator: отчёт другого пользователя",
    ),
):
    """Отчёт о работе протеза(-ов) текущего пользователя за период.

    Данные берутся из подготовленной витрины OLAP — без тяжёлых
    вычислений в реальном времени.
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

    # Отчёт строится только за период, уже обработанный Airflow.
    if watermark is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "Report data is not ready yet: the ETL process (Airflow) "
                "has not populated the OLAP mart. Try again later."
            ),
        )

    # Верхнюю границу отчёта прижимаем к водяному знаку ETL: данные после
    # него ещё не загружены в OLAP, отдавать их нельзя (будут неполными).
    date_to = min(date.today(), watermark.date())
    date_from = date_to - timedelta(days=days)

    try:
        rows = _fetch_report_rows(client, target_subject, date_from, date_to)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"OLAP unavailable: {exc}")

    if format == "json":
        return JSONResponse(
            {
                "subject": target_subject,
                "username": user.username,
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
        )

    filename = f"prosthesis-report-{date_from}-{date_to}.csv"
    return StreamingResponse(
        iter([_to_csv(rows)]),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            # Отметка ETL — фронтенд может показать актуальность данных.
            "X-Report-Processed-Up-To": watermark.isoformat(),
        },
    )


@app.get("/health")
async def health():
    return {"status": "ok"}

import logging
from datetime import datetime

import clickhouse_connect
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .config import settings

logger = logging.getLogger(__name__)

app = FastAPI(title="BionicPRO Telemetry API")


class TelemetryEvent(BaseModel):
    subject: str = Field(..., description="sub пользователя из Keycloak (владелец протеза)")
    prosthesis_serial: str = Field(..., description="Серийный номер протеза")
    event_type: str = Field(..., description="Тип события: movement / calibration / heartbeat")
    response_time_ms: int = Field(..., ge=0, description="Скорость реагирования, мс")
    signal_quality: float = Field(..., ge=0.0, le=1.0, description="Качество миосигнала 0..1")
    battery_level: int = Field(..., ge=0, le=100, description="Заряд батареи, %")


# --------------------------------------------------------------------------- #
#  ClickHouse client
# --------------------------------------------------------------------------- #
def _ch_client():
    return clickhouse_connect.get_client(
        host=settings.CLICKHOUSE_HOST,
        port=settings.CLICKHOUSE_PORT,
        username=settings.CLICKHOUSE_USER,
        password=settings.CLICKHOUSE_PASSWORD,
    )


# --------------------------------------------------------------------------- #
#  Endpoints
# --------------------------------------------------------------------------- #
@app.post("/telemetry/events")
async def post_telemetry_event(event: TelemetryEvent):
    """Записывает одно событие телеметрии с протеза в ClickHouse.

    Данные попадают в таблицу telemetry.events, откуда MaterializedView
    olap.mv_telemetry_daily_agg агрегирует их в olap.telemetry_daily_agg.
    """
    try:
        client = _ch_client()
        now = datetime.utcnow()
        client.query(
            """
            INSERT INTO telemetry.events (subject, prosthesis_serial, event_time, event_type, response_time_ms, signal_quality, battery_level)
            VALUES ({subject:String}, {prosthesis_serial:String}, {event_time:DateTime}, {event_type:String}, {response_time_ms:UInt16}, {signal_quality:Float32}, {battery_level:UInt8})
            """,
            parameters={
                "subject": event.subject,
                "prosthesis_serial": event.prosthesis_serial,
                "event_time": now,
                "event_type": event.event_type,
                "response_time_ms": event.response_time_ms,
                "signal_quality": event.signal_quality,
                "battery_level": event.battery_level,
            },
        )
        logger.info(
            "Telemetry event recorded: subject=%s prosthesis=%s type=%s",
            event.subject, event.prosthesis_serial, event.event_type,
        )
        return {"status": "ok", "recorded_at": now.isoformat()}
    except Exception as exc:
        logger.error("Failed to record telemetry event: %s", exc)
        raise HTTPException(status_code=503, detail=f"ClickHouse unavailable: {exc}")


@app.get("/health")
async def health():
    return {"status": "ok"}

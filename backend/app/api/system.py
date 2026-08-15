from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import admin_user, current_user
from app.core.config import settings
from app.core.health import TELEGRAM_HEARTBEAT_KEY
from app.core.time import display_time, london_day_bounds_utc
from app.database.session import get_db
from app.models.entities import AccountDailyStat, AuditLog, Signal, SystemSetting, TradingAccount, User


router = APIRouter(prefix="/system", tags=["System"])


@router.get("/health")
async def health(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    checks = {"backend": "CONNECTED", "database": "DISCONNECTED", "redis": "DISCONNECTED"}
    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = "CONNECTED"
    except Exception:
        checks["database"] = "DISCONNECTED"
    redis = Redis.from_url(settings.redis_url)
    try:
        checks["redis"] = "CONNECTED" if await redis.ping() else "DISCONNECTED"
        checks["telegram"] = "CONNECTED" if await redis.get(TELEGRAM_HEARTBEAT_KEY) else "DISCONNECTED"
    except Exception:
        checks["redis"] = "DISCONNECTED"
    finally:
        await redis.aclose()
    return {"status": "CONNECTED" if checks["database"] == checks["redis"] == checks.get("telegram") == "CONNECTED" else "DEGRADED", "timestamp": display_time(datetime.now(timezone.utc)), "timezone": settings.application_timezone, "checks": checks}


@router.get("/overview", dependencies=[Depends(current_user)])
async def overview(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    accounts = (await db.scalars(select(TradingAccount))).all()
    day_start, _ = london_day_bounds_utc()
    signals_today = await db.scalar(select(func.count()).select_from(Signal).where(Signal.signal_timestamp >= day_start))
    return {
        "accounts": {"total": len(accounts), "active": sum(a.status.value == "ACTIVE" for a in accounts), "evaluation": sum(a.stage.value == "EVALUATION" for a in accounts), "funded": sum(a.stage.value == "FUNDED" for a in accounts), "paused": sum(a.status.value == "PAUSED" for a in accounts)},
        "financials": {"starting_balance": sum((a.initial_balance for a in accounts), Decimal("0")), "balance": sum((a.current_balance for a in accounts), Decimal("0")), "equity": sum((a.current_equity for a in accounts), Decimal("0")), "floating_pnl": sum((a.current_equity - a.current_balance for a in accounts), Decimal("0"))},
        "signals_today": signals_today or 0,
    }


class EmergencyAction(BaseModel):
    stop_new_trades: bool = True
    close_all_positions: bool = False
    confirmation: str | None = None


@router.post("/emergency-stop")
async def emergency_stop(payload: EmergencyAction, db: AsyncSession = Depends(get_db), user: User = Depends(admin_user)) -> dict[str, str]:
    if payload.close_all_positions and payload.confirmation != "CLOSE ALL POSITIONS":
        raise HTTPException(400, "Exact close-all confirmation is required")
    setting = await db.scalar(select(SystemSetting).where(SystemSetting.key == "global_safety").with_for_update())
    value = {"global_trading_enabled": not payload.stop_new_trades, "close_all_requested": payload.close_all_positions, "changed_at": datetime.now(timezone.utc).isoformat()}
    if setting:
        setting.value = value
    else:
        db.add(SystemSetting(key="global_safety", value=value))
    db.add(AuditLog(timestamp=datetime.now(timezone.utc), user_id=user.id, action="EMERGENCY_STOP_TRIGGERED", entity="system", entity_id=None, before=None, after=value, metadata_json={}))
    await db.commit()
    return {"status": "STOPPED", "positions": "CLOSE_REQUESTED" if payload.close_all_positions else "UNCHANGED"}

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import admin_user, current_user
from app.core.enums import Decision, Direction, TradingMode
from app.database.session import get_db
from app.models.entities import AccountDailyStat, AccountSetting, AuditLog, Order, Position, PositionEvent, PositionTarget, PropRuleProfile, Signal, SignalAccountDecision, TelegramSource, Trade, TradingAccount, User
from app.notifications.service import queue_notification
from app.risk.drawdown import calculate_drawdown
from app.workers.pipeline import broker_registry


protected = [Depends(current_user)]
orders_router = APIRouter(prefix="/orders", tags=["Orders"], dependencies=protected)
positions_router = APIRouter(prefix="/positions", tags=["Positions"], dependencies=protected)
risk_router = APIRouter(prefix="/risk", tags=["Risk"], dependencies=protected)
analytics_router = APIRouter(prefix="/analytics", tags=["Analytics"], dependencies=protected)
settings_router = APIRouter(prefix="/settings", tags=["Settings"], dependencies=protected)


@orders_router.get("")
async def list_orders(limit: int = Query(100, ge=1, le=500), db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    rows = list((await db.scalars(select(Order).order_by(desc(Order.created_at)).limit(limit))).all())
    return [{"id": str(row.id), "execution_id": row.execution_id, "signal_id": str(row.signal_id), "account_id": str(row.account_id), "broker_order_id": row.broker_order_id, "status": row.status, "request": row.request, "response": row.response, "created_at": row.created_at} for row in rows]


@positions_router.get("")
async def list_positions(status: str | None = None, db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    query = select(Position).order_by(desc(Position.created_at))
    if status:
        query = query.where(Position.status == status.upper())
    rows = list((await db.scalars(query)).all())
    return [_position(row) for row in rows]


class ShadowPriceInput(BaseModel):
    account_id: UUID
    broker_symbol: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9._-]+$")
    bid: Decimal = Field(gt=0)
    ask: Decimal = Field(gt=0)


@positions_router.post("/shadow-price")
async def set_shadow_price(payload: ShadowPriceInput, db: AsyncSession = Depends(get_db), user: User = Depends(admin_user)) -> dict[str, Any]:
    account = await db.get(TradingAccount, payload.account_id)
    if account is None or account.trading_mode != TradingMode.SHADOW:
        raise HTTPException(409, "A SHADOW account is required")
    if payload.ask < payload.bid:
        raise HTTPException(422, "Ask must be greater than or equal to bid")
    broker = broker_registry.get(account)
    before_bid, before_ask = broker.get_bid(payload.broker_symbol), broker.get_ask(payload.broker_symbol)
    broker.set_price(payload.broker_symbol, payload.bid, payload.ask)
    db.add(AuditLog(timestamp=datetime.now(timezone.utc), correlation_id=f"shadow-price:{account.id}", user_id=user.id, action="SHADOW_PRICE_UPDATED", entity="trading_account", entity_id=str(account.id), before={"bid": str(before_bid), "ask": str(before_ask)}, after={"broker_symbol": payload.broker_symbol, "bid": str(payload.bid), "ask": str(payload.ask)}, metadata_json={"historical_replay": True}))
    await db.commit()
    from app.monitoring.service import ReconciliationService
    await ReconciliationService(db).run({"account_id": str(account.id)})
    return {"status": "MONITORED", "bid": payload.bid, "ask": payload.ask}


@positions_router.get("/{position_id}")
async def position_detail(position_id: UUID, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    row = await db.get(Position, position_id)
    if row is None:
        raise HTTPException(404, "Position not found")
    targets = list((await db.scalars(select(PositionTarget).where(PositionTarget.position_id == row.id).order_by(PositionTarget.sequence))).all())
    events = list((await db.scalars(select(PositionEvent).where(PositionEvent.position_id == row.id).order_by(PositionEvent.timestamp))).all())
    return _position(row) | {
        "targets": [{"sequence": target.sequence, "price": target.price, "requested_percentage": target.requested_percentage, "allocated_volume": target.allocated_volume, "status": target.status, "merged_into_sequence": target.merged_into_sequence, "executed_at": target.executed_at} for target in targets],
        "events": [{"timestamp": event.timestamp, "event": event.event, "payload": event.payload} for event in events],
    }


class PartialCloseInput(BaseModel):
    percentage: Decimal = Field(gt=0, lt=1)


class StopInput(BaseModel):
    stop_loss: Decimal = Field(gt=0)


@positions_router.post("/{position_id}/partial-close")
async def partial_close(position_id: UUID, payload: PartialCloseInput, db: AsyncSession = Depends(get_db), user: User = Depends(admin_user)) -> dict[str, Any]:
    row, account = await _mutable_position(db, position_id)
    broker = _restore_shadow_position(account, row)
    spec = broker.get_symbol_info(row.broker_symbol)
    assert spec is not None
    requested = Decimal(row.remaining_volume) * payload.percentage
    volume = (requested // spec.volume_step) * spec.volume_step
    if volume < spec.volume_min or volume >= Decimal(row.remaining_volume):
        raise HTTPException(409, "Requested partial is not executable under broker lot rules")
    result = broker.partial_close(row.broker_position_id, volume=volume)
    if not result.accepted:
        raise HTTPException(409, result.message)
    before = Decimal(row.remaining_volume)
    row.remaining_volume = before - volume
    row.size = row.remaining_volume
    await _record_action(db, user, row, "POSITION_PARTIAL_CLOSED", {"remaining_volume": str(before)}, {"remaining_volume": str(row.remaining_volume), "closed_volume": str(volume), "price": str(result.fill_price)})
    return {"status": result.code, "closed_volume": volume, "remaining_volume": row.remaining_volume, "fill_price": result.fill_price}


@positions_router.post("/{position_id}/close")
async def close_position(position_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(admin_user)) -> dict[str, Any]:
    row, account = await _mutable_position(db, position_id)
    broker = _restore_shadow_position(account, row)
    result = broker.close_position(row.broker_position_id)
    if not result.accepted:
        raise HTTPException(409, result.message)
    before = str(row.remaining_volume)
    row.remaining_volume = Decimal("0")
    row.size = Decimal("0")
    row.status = "CLOSED"
    await _record_action(db, user, row, "POSITION_CLOSED", {"remaining_volume": before}, {"remaining_volume": "0", "fill_price": str(result.fill_price)})
    return {"status": "CLOSED", "fill_price": result.fill_price}


@positions_router.post("/{position_id}/stop")
async def move_stop(position_id: UUID, payload: StopInput, db: AsyncSession = Depends(get_db), user: User = Depends(admin_user)) -> dict[str, Any]:
    row, account = await _mutable_position(db, position_id)
    current = Decimal(row.stop_loss or row.entry_price)
    worsens = payload.stop_loss < current if row.direction == Direction.BUY else payload.stop_loss > current
    if worsens:
        raise HTTPException(409, "Stop modification would increase risk")
    broker = _restore_shadow_position(account, row)
    result = broker.modify_stop_loss(row.broker_position_id, payload.stop_loss)
    if not result.accepted:
        raise HTTPException(409, result.message)
    row.stop_loss = payload.stop_loss
    await _record_action(db, user, row, "POSITION_STOP_MOVED", {"stop_loss": str(current)}, {"stop_loss": str(payload.stop_loss)})
    return {"status": "MODIFIED", "stop_loss": payload.stop_loss}


@risk_router.get("/accounts/{account_id}")
async def account_risk(account_id: UUID, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    account = await db.get(TradingAccount, account_id)
    if account is None:
        raise HTTPException(404, "Account not found")
    settings = await db.scalar(select(AccountSetting).where(AccountSetting.account_id == account.id))
    profile = await db.get(PropRuleProfile, account.rule_profile_id)
    if settings is None or profile is None:
        raise HTTPException(409, "Risk configuration is incomplete")
    drawdown = calculate_drawdown(profile.maximum_drawdown_type, account.initial_balance, account.current_balance, account.current_equity, account.high_water_balance, account.high_water_equity, profile.maximum_drawdown, settings.overall_drawdown_safety_buffer)
    stat = await db.scalar(select(AccountDailyStat).where(AccountDailyStat.account_id == account.id).order_by(desc(AccountDailyStat.trading_date)))
    daily_pnl = (stat.realized_pnl + stat.floating_pnl) if stat else Decimal("0")
    return {"account_id": str(account.id), "equity": account.current_equity, "daily_pnl": daily_pnl, "daily_buffer_remaining": settings.maximum_daily_loss_internal + daily_pnl, "overall_threshold": drawdown.safety_threshold, "overall_buffer_remaining": drawdown.remaining_buffer, "breached": drawdown.breached, "risk_per_trade": settings.risk_per_trade, "risk_multipliers": settings.risk_multipliers}


@analytics_router.get("/overview")
async def analytics_overview(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    accounts = (await db.execute(select(func.count(TradingAccount.id), func.coalesce(func.sum(TradingAccount.current_balance), 0), func.coalesce(func.sum(TradingAccount.current_equity), 0)))).one()
    signals = (await db.execute(select(func.count(func.distinct(Signal.id)), func.count(SignalAccountDecision.id).filter(SignalAccountDecision.decision == Decision.APPROVED)).select_from(Signal).outerjoin(SignalAccountDecision, SignalAccountDecision.signal_id == Signal.id))).one()
    trades = (await db.execute(select(func.count(Trade.id), func.coalesce(func.sum(Trade.pnl), 0), func.count(Trade.id).filter(Trade.pnl > 0)))).one()
    open_positions = await db.scalar(select(func.count(Position.id)).where(Position.status == "OPEN"))
    return {"accounts": accounts[0], "total_balance": accounts[1], "total_equity": accounts[2], "signals": signals[0], "approved_decisions": signals[1], "trades": trades[0], "realized_pnl": trades[1], "wins": trades[2], "open_positions": open_positions or 0}


@analytics_router.get("/sources")
async def source_analytics(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    query = select(TelegramSource.id, TelegramSource.name, func.count(Signal.id)).outerjoin(Signal, Signal.source_id == TelegramSource.id).group_by(TelegramSource.id, TelegramSource.name).order_by(TelegramSource.name)
    return [{"source_id": str(row.id), "name": row.name, "signals": row[2]} for row in (await db.execute(query)).all()]


class SettingsPatch(BaseModel):
    risk_per_trade: Decimal | None = Field(default=None, gt=0, le=Decimal("0.05"))
    maximum_risk_per_trade: Decimal | None = Field(default=None, gt=0)
    maximum_open_positions: int | None = Field(default=None, ge=1, le=100)
    maximum_positions_per_symbol: int | None = Field(default=None, ge=1, le=100)
    maximum_positions_per_direction: int | None = Field(default=None, ge=1, le=100)
    maximum_pending_orders: int | None = Field(default=None, ge=0, le=100)
    concurrent_limit_action: str | None = None
    risk_multipliers: dict[str, Decimal] | None = None
    tp_allocation_preset: str | None = None
    tp_custom_allocations: list[Decimal] | None = None
    break_even_offset_points: int | None = Field(default=None, ge=0, le=10000)
    symbol_mappings: dict[str, str] | None = None


@settings_router.get("/{account_id}")
async def get_settings(account_id: UUID, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    row = await db.scalar(select(AccountSetting).where(AccountSetting.account_id == account_id))
    if row is None:
        raise HTTPException(404, "Account settings not found")
    return {column.name: getattr(row, column.name) for column in AccountSetting.__table__.columns if column.name not in {"id", "account_id", "created_at", "updated_at"}}


@settings_router.get("")
async def list_settings(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    rows = list((await db.scalars(select(AccountSetting).order_by(AccountSetting.account_id))).all())
    return [{"account_id": str(row.account_id), **{column.name: getattr(row, column.name) for column in AccountSetting.__table__.columns if column.name not in {"id", "account_id", "created_at", "updated_at"}}} for row in rows]


@settings_router.patch("/{account_id}")
async def patch_settings(account_id: UUID, payload: SettingsPatch, db: AsyncSession = Depends(get_db), user: User = Depends(admin_user)) -> dict[str, str]:
    row = await db.scalar(select(AccountSetting).where(AccountSetting.account_id == account_id).with_for_update())
    if row is None:
        raise HTTPException(404, "Account settings not found")
    changes = payload.model_dump(exclude_none=True, mode="json")
    if changes.get("concurrent_limit_action") not in {None, "REJECT", "QUEUE"}:
        raise HTTPException(422, "concurrent_limit_action must be REJECT or QUEUE")
    if changes.get("tp_allocation_preset") not in {None, "EQUAL", "PROTECT", "RUNNER", "CUSTOM"}:
        raise HTTPException(422, "Unknown TP allocation preset")
    if "risk_multipliers" in changes:
        changes["risk_multipliers"] = {key: str(min(Decimal("1"), max(Decimal("0"), Decimal(str(value))))) for key, value in changes["risk_multipliers"].items()}
    before = {key: getattr(row, key) for key in changes}
    for key, value in changes.items():
        setattr(row, key, value)
    db.add(AuditLog(timestamp=datetime.now(timezone.utc), user_id=user.id, action="ACCOUNT_SETTINGS_CHANGED", entity="account_settings", entity_id=str(row.id), before={key: str(value) for key, value in before.items()}, after=changes, metadata_json={}))
    await db.commit()
    return {"status": "UPDATED"}


def _position(row: Position) -> dict[str, Any]:
    return {"id": str(row.id), "account_id": str(row.account_id), "signal_id": str(row.signal_id) if row.signal_id else None, "broker_position_id": row.broker_position_id, "symbol": row.symbol, "broker_symbol": row.broker_symbol, "direction": row.direction, "original_volume": row.original_volume, "remaining_volume": row.remaining_volume, "entry_price": row.entry_price, "stop_loss": row.stop_loss, "status": row.status, "is_virtual": row.is_virtual, "created_at": row.created_at}


async def _mutable_position(db: AsyncSession, position_id: UUID) -> tuple[Position, TradingAccount]:
    row = await db.get(Position, position_id, with_for_update=True)
    if row is None or row.status != "OPEN":
        raise HTTPException(404, "Open position not found")
    account = await db.get(TradingAccount, row.account_id)
    if account is None or account.trading_mode != TradingMode.SHADOW or not row.is_virtual:
        raise HTTPException(409, "This API is restricted to SHADOW positions")
    return row, account


def _restore_shadow_position(account: TradingAccount, row: Position):
    broker = broker_registry.get(account)
    if row.broker_symbol not in broker.prices:
        broker.prices[row.broker_symbol] = Decimal(row.entry_price)
        broker.spreads[row.broker_symbol] = Decimal("0")
    broker.positions.setdefault(row.broker_position_id, {"id": row.broker_position_id, "symbol": row.broker_symbol, "direction": row.direction.value, "size": Decimal(row.remaining_volume), "original_size": Decimal(row.original_volume), "entry_price": Decimal(row.entry_price), "stop_loss": Decimal(row.stop_loss or row.entry_price), "take_profit": None, "restored": True})
    return broker


async def _record_action(db: AsyncSession, user: User, row: Position, action: str, before: dict[str, Any], after: dict[str, Any]) -> None:
    db.add_all([PositionEvent(position_id=row.id, correlation_id=row.correlation_id, timestamp=datetime.now(timezone.utc), event=action, payload=after), AuditLog(timestamp=datetime.now(timezone.utc), correlation_id=row.correlation_id, user_id=user.id, action=action, entity="position", entity_id=str(row.id), before=before, after=after, metadata_json={"shadow": True})])
    details = ", ".join(f"{key}={value}" for key, value in after.items())
    queue_notification(
        db,
        "INFO",
        action.replace("_", " ").title(),
        f"{row.direction.value} {row.symbol}: {details}.",
        row.correlation_id,
    )
    await db.commit()

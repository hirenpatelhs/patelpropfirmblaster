from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import admin_user, current_user
from app.core.enums import AccountStage, AccountStatus, RiskMode, TradingMode
from app.database.session import get_db
from app.models.entities import AccountSetting, AuditLog, PropFirm, PropRuleProfile, Signal, SignalAccountDecision, TelegramSource, Trade, TradingAccount, User


protected = [Depends(current_user)]
accounts_router = APIRouter(prefix="/accounts", tags=["Accounts"], dependencies=protected)
firms_router = APIRouter(prefix="/prop-firms", tags=["Prop Firms"], dependencies=protected)
rules_router = APIRouter(prefix="/rule-profiles", tags=["Rule Profiles"], dependencies=protected)
sources_router = APIRouter(prefix="/telegram-sources", tags=["Telegram Sources"], dependencies=protected)
signals_router = APIRouter(prefix="/signals", tags=["Signals"], dependencies=protected)
trades_router = APIRouter(prefix="/trades", tags=["Trades"], dependencies=protected)
audit_router = APIRouter(prefix="/audit", tags=["Audit"], dependencies=protected)


class FirmInput(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    website: HttpUrl | None = None
    notes: str | None = Field(default=None, max_length=4000)
    enabled: bool = True


@firms_router.get("")
async def list_firms(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    firms = (await db.scalars(select(PropFirm).order_by(PropFirm.name))).all()
    return [{"id": str(f.id), "name": f.name, "website": f.website, "enabled": f.enabled, "last_rules_reviewed_at": f.last_rules_reviewed_at} for f in firms]


@firms_router.post("")
async def create_firm(payload: FirmInput, db: AsyncSession = Depends(get_db), user: User = Depends(admin_user)) -> dict[str, str]:
    firm = PropFirm(name=payload.name, website=str(payload.website) if payload.website else None, notes=payload.notes, enabled=payload.enabled)
    db.add(firm)
    await db.flush()
    db.add(AuditLog(timestamp=datetime.now(timezone.utc), user_id=user.id, action="PROP_FIRM_CREATED", entity="prop_firm", entity_id=str(firm.id), before=None, after=payload.model_dump(mode="json"), metadata_json={}))
    await db.commit()
    return {"id": str(firm.id)}


class RuleProfileInput(BaseModel):
    firm_id: UUID
    name: str = Field(min_length=2, max_length=180)
    starting_balance: Decimal = Field(gt=0)
    profit_target: Decimal | None = Field(default=None, gt=0)
    maximum_daily_loss: Decimal = Field(gt=0)
    maximum_drawdown: Decimal = Field(gt=0)
    maximum_drawdown_type: str = "STATIC"
    maximum_positions: int = Field(default=3, ge=1, le=100)
    ea_allowed: bool = False
    signal_copying_allowed: bool = False
    third_party_signal_allowed: bool = False
    hedging_allowed: bool = False
    news_trading_allowed: bool = False
    allowed_symbols: list[str] = Field(default_factory=list)
    restricted_symbols: list[str] = Field(default_factory=list)
    daily_reset_timezone: str = "UTC"
    rule_notes: str | None = None


@rules_router.get("")
async def list_profiles(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    rows = (await db.scalars(select(PropRuleProfile).order_by(PropRuleProfile.name))).all()
    return [{"id": str(r.id), "firm_id": str(r.firm_id), "name": r.name, "starting_balance": r.starting_balance, "maximum_daily_loss": r.maximum_daily_loss, "maximum_drawdown": r.maximum_drawdown, "automation_permitted": r.ea_allowed and r.signal_copying_allowed and r.third_party_signal_allowed} for r in rows]


@rules_router.post("")
async def create_profile(payload: RuleProfileInput, db: AsyncSession = Depends(get_db), user: User = Depends(admin_user)) -> dict[str, str]:
    data = payload.model_dump()
    profile = PropRuleProfile(**data, enabled=True, rules={})
    db.add(profile)
    await db.flush()
    db.add(AuditLog(timestamp=datetime.now(timezone.utc), user_id=user.id, action="RULE_PROFILE_CHANGED", entity="prop_rule_profile", entity_id=str(profile.id), before=None, after=payload.model_dump(mode="json"), metadata_json={"warning": "Verify current firm terms before live use"}))
    await db.commit()
    return {"id": str(profile.id)}


class AccountInput(BaseModel):
    name: str = Field(min_length=2, max_length=180)
    prop_firm_id: UUID
    rule_profile_id: UUID
    account_number: str = Field(min_length=2, max_length=80)
    platform: str = "MOCK"
    initial_balance: Decimal = Field(gt=0)
    stage: AccountStage = AccountStage.EVALUATION
    trading_mode: TradingMode = TradingMode.SHADOW
    risk_mode: RiskMode = RiskMode.EVALUATION


class ModeChange(BaseModel):
    trading_mode: TradingMode
    confirmation: str | None = None


@accounts_router.get("")
async def list_accounts(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    rows = (await db.scalars(select(TradingAccount).order_by(TradingAccount.name))).all()
    return [{"id": str(a.id), "name": a.name, "platform": a.platform, "balance": a.current_balance, "equity": a.current_equity, "stage": a.stage, "status": a.status, "trading_mode": a.trading_mode, "risk_mode": a.risk_mode} for a in rows]


@accounts_router.post("")
async def create_account(payload: AccountInput, db: AsyncSession = Depends(get_db), user: User = Depends(admin_user)) -> dict[str, str]:
    if payload.trading_mode == TradingMode.LIVE:
        raise HTTPException(409, "Create the account in SHADOW mode and complete a separate live-readiness review")
    profile = await db.get(PropRuleProfile, payload.rule_profile_id)
    if profile is None:
        raise HTTPException(422, "Rule profile not found")
    account = TradingAccount(**payload.model_dump(), account_currency="USD", current_balance=payload.initial_balance, current_equity=payload.initial_balance, high_water_balance=payload.initial_balance, high_water_equity=payload.initial_balance, status=AccountStatus.PAUSED)
    db.add(account)
    await db.flush()
    db.add(AccountSetting(account_id=account.id, maximum_daily_loss_internal=profile.maximum_daily_loss * Decimal("0.8")))
    db.add(AuditLog(timestamp=datetime.now(timezone.utc), user_id=user.id, action="ACCOUNT_CREATED", entity="trading_account", entity_id=str(account.id), before=None, after=payload.model_dump(mode="json"), metadata_json={}))
    await db.commit()
    return {"id": str(account.id)}


@accounts_router.post("/{account_id}/activate")
async def activate_account(account_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(admin_user)) -> dict[str, str]:
    account = await db.get(TradingAccount, account_id, with_for_update=True)
    if not account:
        raise HTTPException(404, "Account not found")
    configured = await db.scalar(select(AccountSetting.id).where(AccountSetting.account_id == account.id))
    if not configured or account.trading_mode != TradingMode.SHADOW:
        raise HTTPException(409, "Only a configured SHADOW account can be activated in this phase")
    before = account.status.value
    account.status = AccountStatus.ACTIVE
    db.add(AuditLog(timestamp=datetime.now(timezone.utc), user_id=user.id, action="ACCOUNT_ACTIVATED", entity="trading_account", entity_id=str(account.id), before={"status": before}, after={"status": "ACTIVE"}, metadata_json={"shadow_only": True}))
    await db.commit()
    return {"status": "ACTIVE"}


@accounts_router.post("/{account_id}/pause")
async def pause_account(account_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(admin_user)) -> dict[str, str]:
    account = await db.get(TradingAccount, account_id, with_for_update=True)
    if not account:
        raise HTTPException(404, "Account not found")
    before = account.status.value
    account.status = AccountStatus.PAUSED
    db.add(AuditLog(timestamp=datetime.now(timezone.utc), user_id=user.id, action="ACCOUNT_PAUSED", entity="trading_account", entity_id=str(account.id), before={"status": before}, after={"status": "PAUSED"}, metadata_json={}))
    await db.commit()
    return {"status": "PAUSED"}


@accounts_router.post("/{account_id}/mode")
async def change_account_mode(account_id: UUID, payload: ModeChange, db: AsyncSession = Depends(get_db), user: User = Depends(admin_user)) -> dict[str, str]:
    account = await db.get(TradingAccount, account_id, with_for_update=True)
    if not account:
        raise HTTPException(404, "Account not found")
    profile = await db.get(PropRuleProfile, account.rule_profile_id)
    if payload.trading_mode == TradingMode.LIVE:
        required = "I have reviewed the current prop-firm rules and confirm this account permits the configured trading method."
        blockers: list[str] = []
        if payload.confirmation != required:
            blockers.append("Exact prop-firm permission confirmation is required")
        if profile is None or not profile.enabled:
            blockers.append("An enabled rule profile is required")
        elif not (profile.ea_allowed and profile.signal_copying_allowed and profile.third_party_signal_allowed):
            blockers.append("The assigned profile does not permit this automation method")
        if account.settings is None:
            blockers.append("A valid risk configuration is required")
        if account.platform.upper() not in {"MT5", "METATRADER5"}:
            blockers.append("A supported live broker connection is required")
        if blockers:
            raise HTTPException(409, {"message": "Live mode protection rejected the change", "reasons": blockers})
        account.automation_permission_acknowledged_at = datetime.now(timezone.utc)
        account.firm_rules_confirmed_at = datetime.now(timezone.utc)
    before = account.trading_mode.value
    account.trading_mode = payload.trading_mode
    db.add(AuditLog(timestamp=datetime.now(timezone.utc), user_id=user.id, action="ACCOUNT_MODE_CHANGED", entity="trading_account", entity_id=str(account.id), before={"trading_mode": before}, after={"trading_mode": payload.trading_mode.value}, metadata_json={"confirmation_recorded": payload.trading_mode == TradingMode.LIVE}))
    await db.commit()
    return {"trading_mode": payload.trading_mode.value}


class SourceInput(BaseModel):
    name: str
    telegram_chat_id: str
    telegram_channel_name: str | None = None
    priority: int = 100
    allowed_account_ids: list[str] = Field(default_factory=list)
    notes: str | None = None


@sources_router.get("")
async def list_sources(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    rows = (await db.scalars(select(TelegramSource).order_by(TelegramSource.priority))).all()
    return [{"id": str(r.id), "name": r.name, "telegram_chat_id": r.telegram_chat_id, "enabled": r.enabled, "priority": r.priority, "allowed_account_ids": r.allowed_account_ids} for r in rows]


@sources_router.post("")
async def create_source(payload: SourceInput, db: AsyncSession = Depends(get_db), user: User = Depends(admin_user)) -> dict[str, str]:
    source = TelegramSource(**payload.model_dump(), enabled=True, parser_profile={})
    db.add(source)
    await db.flush()
    db.add(AuditLog(timestamp=datetime.now(timezone.utc), user_id=user.id, action="TELEGRAM_SOURCE_CREATED", entity="telegram_source", entity_id=str(source.id), before=None, after=payload.model_dump(mode="json"), metadata_json={}))
    await db.commit()
    return {"id": str(source.id)}


@signals_router.get("")
async def list_signals(limit: int = Query(50, ge=1, le=200), db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    rows = (await db.scalars(select(Signal).order_by(desc(Signal.signal_timestamp)).limit(limit))).all()
    return [{"id": str(s.id), "timestamp": s.signal_timestamp, "symbol": s.symbol, "direction": s.direction, "entry_min": s.entry_min, "entry_max": s.entry_max, "stop_loss": s.stop_loss, "take_profits": s.take_profits, "confidence": s.confidence, "status": s.status, "raw_text": s.raw_text} for s in rows]


@signals_router.get("/{signal_id}/decisions")
async def signal_decisions(signal_id: UUID, db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    rows = (await db.scalars(select(SignalAccountDecision).where(SignalAccountDecision.signal_id == signal_id))).all()
    return [{"account_id": str(r.account_id), "decision": r.decision, "reasons": r.reasons, "risk_amount": r.risk_amount, "risk_percent": r.risk_percent, "calculated_size": r.calculated_size} for r in rows]


@trades_router.get("")
async def list_trades(limit: int = Query(100, ge=1, le=500), db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    rows = (await db.scalars(select(Trade).order_by(desc(Trade.opened_at)).limit(limit))).all()
    return [{"id": str(r.id), "account_id": str(r.account_id), "symbol": r.symbol, "direction": r.direction, "entry_price": r.entry_price, "exit_price": r.exit_price, "pnl": r.pnl, "r_result": r.r_result, "opened_at": r.opened_at, "closed_at": r.closed_at} for r in rows]


@audit_router.get("")
async def list_audit(limit: int = Query(100, ge=1, le=500), db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    rows = (await db.scalars(select(AuditLog).order_by(desc(AuditLog.timestamp)).limit(limit))).all()
    return [{"id": str(r.id), "timestamp": r.timestamp, "action": r.action, "entity": r.entity, "entity_id": r.entity_id, "before": r.before, "after": r.after, "metadata": r.metadata_json} for r in rows]

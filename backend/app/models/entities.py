from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import AccountStage, AccountStatus, Confidence, Decision, Direction, DrawdownType, OrderType, RiskClassification, RiskMode, SignalStatus, TargetStatus, TradingMode
from app.database.base import Base, TimestampMixin, UUIDMixin


money = Numeric(18, 2)
price = Numeric(18, 8)


class User(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "users"
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="ADMIN")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class PropFirm(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "prop_firms"
    name: Mapped[str] = mapped_column(String(160), unique=True)
    website: Mapped[str | None] = mapped_column(String(500))
    notes: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_rules_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    profiles: Mapped[list[PropRuleProfile]] = relationship(back_populates="firm")


class PropRuleProfile(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "prop_rule_profiles"
    firm_id: Mapped[UUID] = mapped_column(ForeignKey("prop_firms.id"), index=True)
    name: Mapped[str] = mapped_column(String(180))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    starting_balance: Mapped[Decimal] = mapped_column(money)
    profit_target: Mapped[Decimal | None] = mapped_column(money)
    maximum_daily_loss: Mapped[Decimal] = mapped_column(money)
    maximum_drawdown: Mapped[Decimal] = mapped_column(money)
    maximum_drawdown_type: Mapped[DrawdownType] = mapped_column(Enum(DrawdownType), default=DrawdownType.STATIC)
    drawdown_floor: Mapped[Decimal | None] = mapped_column(money)
    maximum_positions: Mapped[int] = mapped_column(Integer, default=3)
    maximum_lots: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    consistency_rule_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    consistency_percentage: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))
    news_trading_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    weekend_holding_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    overnight_holding_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    ea_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    signal_copying_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    third_party_signal_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    hedging_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    allowed_symbols: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    restricted_symbols: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    daily_reset_timezone: Mapped[str] = mapped_column(String(80), default="UTC")
    rules: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    rule_notes: Mapped[str | None] = mapped_column(Text)
    firm: Mapped[PropFirm] = relationship(back_populates="profiles")
    __table_args__ = (UniqueConstraint("firm_id", "name"),)


class TradingAccount(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "trading_accounts"
    name: Mapped[str] = mapped_column(String(180))
    prop_firm_id: Mapped[UUID] = mapped_column(ForeignKey("prop_firms.id"), index=True)
    rule_profile_id: Mapped[UUID] = mapped_column(ForeignKey("prop_rule_profiles.id"), index=True)
    account_number: Mapped[str] = mapped_column(String(80))
    platform: Mapped[str] = mapped_column(String(40), default="MOCK")
    broker_server: Mapped[str | None] = mapped_column(String(160))
    account_currency: Mapped[str] = mapped_column(String(3), default="USD")
    initial_balance: Mapped[Decimal] = mapped_column(money)
    current_balance: Mapped[Decimal] = mapped_column(money)
    current_equity: Mapped[Decimal] = mapped_column(money)
    high_water_balance: Mapped[Decimal] = mapped_column(money)
    high_water_equity: Mapped[Decimal] = mapped_column(money)
    stage: Mapped[AccountStage] = mapped_column(Enum(AccountStage))
    status: Mapped[AccountStatus] = mapped_column(Enum(AccountStatus), default=AccountStatus.PAUSED)
    trading_mode: Mapped[TradingMode] = mapped_column(Enum(TradingMode), default=TradingMode.SHADOW)
    risk_mode: Mapped[RiskMode] = mapped_column(Enum(RiskMode), default=RiskMode.EVALUATION)
    credentials_encrypted: Mapped[str | None] = mapped_column(Text)
    automation_permission_acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    firm_rules_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    settings: Mapped[AccountSetting] = relationship(back_populates="account", uselist=False, cascade="all, delete-orphan")


class AccountSetting(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "account_settings"
    account_id: Mapped[UUID] = mapped_column(ForeignKey("trading_accounts.id"), unique=True)
    risk_per_trade: Mapped[Decimal] = mapped_column(Numeric(7, 5), default=Decimal("0.0035"))
    maximum_risk_per_trade: Mapped[Decimal] = mapped_column(money, default=Decimal("200"))
    maximum_daily_loss_internal: Mapped[Decimal] = mapped_column(money)
    maximum_daily_profit: Mapped[Decimal | None] = mapped_column(money)
    maximum_trades_per_day: Mapped[int] = mapped_column(Integer, default=4)
    maximum_consecutive_losses: Mapped[int] = mapped_column(Integer, default=2)
    maximum_open_positions: Mapped[int] = mapped_column(Integer, default=2)
    maximum_positions_per_symbol: Mapped[int] = mapped_column(Integer, default=1)
    maximum_positions_per_direction: Mapped[int] = mapped_column(Integer, default=2)
    maximum_pending_orders: Mapped[int] = mapped_column(Integer, default=2)
    concurrent_limit_action: Mapped[str] = mapped_column(String(20), default="REJECT")
    maximum_symbol_exposure: Mapped[Decimal] = mapped_column(money, default=Decimal("400"))
    maximum_total_exposure: Mapped[Decimal] = mapped_column(money, default=Decimal("800"))
    minimum_rr: Mapped[Decimal] = mapped_column(Numeric(6, 3), default=Decimal("1.0"))
    daily_drawdown_safety_buffer: Mapped[Decimal] = mapped_column(money, default=Decimal("250"))
    overall_drawdown_safety_buffer: Mapped[Decimal] = mapped_column(money, default=Decimal("500"))
    trailing_drawdown_safety_buffer: Mapped[Decimal] = mapped_column(money, default=Decimal("500"))
    enable_signal_execution: Mapped[bool] = mapped_column(Boolean, default=True)
    require_stop_loss: Mapped[bool] = mapped_column(Boolean, default=True)
    close_on_daily_lock: Mapped[bool] = mapped_column(Boolean, default=False)
    max_slippage_points: Mapped[int] = mapped_column(Integer, default=30)
    risk_multipliers: Mapped[dict[str, Any]] = mapped_column(JSONB, default=lambda: {"NORMAL": "1", "MEDIUM_RISK": "0.75", "HIGH_RISK": "0.5", "VERY_HIGH_RISK": "0"})
    tp_allocation_preset: Mapped[str] = mapped_column(String(20), default="PROTECT")
    tp_custom_allocations: Mapped[list[str]] = mapped_column(JSONB, default=list)
    break_even_offset_points: Mapped[int] = mapped_column(Integer, default=0)
    symbol_mappings: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict)
    options: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    account: Mapped[TradingAccount] = relationship(back_populates="settings")


class AccountDailyStat(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "account_daily_stats"
    account_id: Mapped[UUID] = mapped_column(ForeignKey("trading_accounts.id"), index=True)
    trading_date: Mapped[date] = mapped_column(Date)
    start_balance: Mapped[Decimal] = mapped_column(money)
    start_equity: Mapped[Decimal] = mapped_column(money)
    realized_pnl: Mapped[Decimal] = mapped_column(money, default=0)
    floating_pnl: Mapped[Decimal] = mapped_column(money, default=0)
    trades: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_losses: Mapped[int] = mapped_column(Integer, default=0)
    locked_reason: Mapped[str | None] = mapped_column(String(80))
    __table_args__ = (UniqueConstraint("account_id", "trading_date"),)


class TelegramSource(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "telegram_sources"
    name: Mapped[str] = mapped_column(String(160), unique=True)
    telegram_chat_id: Mapped[str] = mapped_column(String(80), unique=True)
    telegram_channel_name: Mapped[str | None] = mapped_column(String(160))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    parser_profile: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    allowed_account_ids: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    notes: Mapped[str | None] = mapped_column(Text)


class TelegramMessage(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "telegram_messages"
    source_id: Mapped[UUID] = mapped_column(ForeignKey("telegram_sources.id"), index=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True)
    telegram_message_id: Mapped[int] = mapped_column(Integer)
    sender: Mapped[str | None] = mapped_column(String(180))
    message_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    body: Mapped[str] = mapped_column(Text)
    original_content: Mapped[str | None] = mapped_column(Text)
    latest_content: Mapped[str | None] = mapped_column(Text)
    event_type: Mapped[str] = mapped_column(String(20), default="NEW")
    reply_to_message_id: Mapped[int | None] = mapped_column(Integer)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attachments: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    __table_args__ = (UniqueConstraint("source_id", "telegram_message_id"),)


class Signal(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "signals"
    source_id: Mapped[UUID] = mapped_column(ForeignKey("telegram_sources.id"), index=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True)
    telegram_message_id: Mapped[int] = mapped_column(Integer)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    direction: Mapped[Direction] = mapped_column(Enum(Direction))
    order_type: Mapped[OrderType] = mapped_column(Enum(OrderType), default=OrderType.MARKET)
    entry_price: Mapped[Decimal | None] = mapped_column(price)
    entry_min: Mapped[Decimal | None] = mapped_column(price)
    entry_max: Mapped[Decimal | None] = mapped_column(price)
    stop_loss: Mapped[Decimal | None] = mapped_column(price)
    take_profits: Mapped[list[float]] = mapped_column(JSONB, default=list)
    risk_hint: Mapped[str | None] = mapped_column(String(80))
    risk_classification: Mapped[RiskClassification] = mapped_column(Enum(RiskClassification), default=RiskClassification.NORMAL)
    signal_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[SignalStatus] = mapped_column(Enum(SignalStatus), index=True)
    confidence: Mapped[Confidence] = mapped_column(Enum(Confidence))
    raw_text: Mapped[str] = mapped_column(Text)
    parsed_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)


class SignalAccountDecision(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "signal_account_decisions"
    signal_id: Mapped[UUID] = mapped_column(ForeignKey("signals.id"), index=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True)
    account_id: Mapped[UUID] = mapped_column(ForeignKey("trading_accounts.id"), index=True)
    decision: Mapped[Decision] = mapped_column(Enum(Decision))
    reasons: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    risk_amount: Mapped[Decimal | None] = mapped_column(money)
    risk_percent: Mapped[Decimal | None] = mapped_column(Numeric(7, 5))
    calculated_size: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    rule_snapshot_id: Mapped[UUID | None] = mapped_column(ForeignKey("rule_snapshots.id"))
    risk_snapshot_id: Mapped[UUID | None] = mapped_column(ForeignKey("risk_snapshots.id"))
    execution_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    __table_args__ = (UniqueConstraint("signal_id", "account_id"),)


class SignalTarget(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "signal_targets"
    signal_id: Mapped[UUID] = mapped_column(ForeignKey("signals.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    price: Mapped[Decimal] = mapped_column(price)
    allocation_percent: Mapped[Decimal] = mapped_column(Numeric(6, 3))
    status: Mapped[str] = mapped_column(String(30), default="PENDING")


class SignalUpdate(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "signal_updates"
    signal_id: Mapped[UUID] = mapped_column(ForeignKey("signals.id"), index=True)
    telegram_message_id: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    confidence: Mapped[Confidence] = mapped_column(Enum(Confidence))
    status: Mapped[str] = mapped_column(String(30))


class SnapshotBase:
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


class RuleSnapshot(UUIDMixin, TimestampMixin, SnapshotBase, Base):
    __tablename__ = "rule_snapshots"


class RiskSnapshot(UUIDMixin, TimestampMixin, SnapshotBase, Base):
    __tablename__ = "risk_snapshots"


class Order(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "orders"
    execution_id: Mapped[str] = mapped_column(String(100), unique=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True)
    signal_id: Mapped[UUID] = mapped_column(ForeignKey("signals.id"), index=True)
    account_id: Mapped[UUID] = mapped_column(ForeignKey("trading_accounts.id"), index=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(40), index=True)
    request: Mapped[dict[str, Any]] = mapped_column(JSONB)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class BrokerConnection(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "broker_connections"
    account_id: Mapped[UUID] = mapped_column(ForeignKey("trading_accounts.id"), unique=True)
    adapter: Mapped[str] = mapped_column(String(50))
    terminal_path: Mapped[str | None] = mapped_column(String(500))
    magic_number: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default="DISCONNECTED")
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Position(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "positions"
    account_id: Mapped[UUID] = mapped_column(ForeignKey("trading_accounts.id"), index=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True)
    signal_id: Mapped[UUID | None] = mapped_column(ForeignKey("signals.id"), index=True)
    broker_position_id: Mapped[str] = mapped_column(String(120))
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    broker_symbol: Mapped[str] = mapped_column(String(40))
    direction: Mapped[Direction] = mapped_column(Enum(Direction))
    size: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    original_volume: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    remaining_volume: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    entry_price: Mapped[Decimal] = mapped_column(price)
    stop_loss: Mapped[Decimal | None] = mapped_column(price)
    take_profit: Mapped[Decimal | None] = mapped_column(price)
    status: Mapped[str] = mapped_column(String(40), index=True)
    is_virtual: Mapped[bool] = mapped_column(Boolean, default=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)


class PositionTarget(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "position_targets"
    position_id: Mapped[UUID] = mapped_column(ForeignKey("positions.id", ondelete="CASCADE"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    price: Mapped[Decimal] = mapped_column(price)
    requested_percentage: Mapped[Decimal] = mapped_column(Numeric(8, 6))
    allocated_volume: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    status: Mapped[TargetStatus] = mapped_column(Enum(TargetStatus), default=TargetStatus.WAITING)
    merged_into_sequence: Mapped[int | None] = mapped_column(Integer)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("position_id", "sequence"),)


class Trade(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "trades"
    account_id: Mapped[UUID] = mapped_column(ForeignKey("trading_accounts.id"), index=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True)
    signal_id: Mapped[UUID | None] = mapped_column(ForeignKey("signals.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    direction: Mapped[Direction] = mapped_column(Enum(Direction))
    entry_price: Mapped[Decimal] = mapped_column(price)
    exit_price: Mapped[Decimal | None] = mapped_column(price)
    size: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    pnl: Mapped[Decimal | None] = mapped_column(money)
    r_result: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    journal: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class PositionEvent(UUIDMixin, Base):
    __tablename__ = "position_events"
    position_id: Mapped[UUID] = mapped_column(ForeignKey("positions.id"), index=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    event: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class VirtualOrder(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "virtual_orders"
    account_id: Mapped[UUID] = mapped_column(ForeignKey("trading_accounts.id"), index=True)
    signal_id: Mapped[UUID] = mapped_column(ForeignKey("signals.id"), index=True)
    execution_id: Mapped[str] = mapped_column(String(100), unique=True)
    status: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


class VirtualPosition(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "virtual_positions"
    account_id: Mapped[UUID] = mapped_column(ForeignKey("trading_accounts.id"), index=True)
    signal_id: Mapped[UUID] = mapped_column(ForeignKey("signals.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    state: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(40))


class VirtualTrade(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "virtual_trades"
    account_id: Mapped[UUID] = mapped_column(ForeignKey("trading_accounts.id"), index=True)
    signal_id: Mapped[UUID] = mapped_column(ForeignKey("signals.id"), index=True)
    pnl: Mapped[Decimal | None] = mapped_column(money)
    r_result: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    journal: Mapped[dict[str, Any]] = mapped_column(JSONB)


class Notification(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "notifications"
    channel: Mapped[str] = mapped_column(String(30))
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True)
    severity: Mapped[str] = mapped_column(String(20))
    subject: Mapped[str] = mapped_column(String(180))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="QUEUED")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(UUIDMixin, Base):
    __tablename__ = "audit_logs"
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True)
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(100), index=True)
    entity: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[str | None] = mapped_column(String(80))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)


class SystemSetting(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "system_settings"
    key: Mapped[str] = mapped_column(String(120), unique=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB)


class SystemEvent(UUIDMixin, Base):
    __tablename__ = "system_events"
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    component: Mapped[str] = mapped_column(String(80), index=True)
    level: Mapped[str] = mapped_column(String(30))
    event: Mapped[str] = mapped_column(String(120))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class DailyPerformance(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "daily_performance"
    account_id: Mapped[UUID | None] = mapped_column(ForeignKey("trading_accounts.id"), index=True)
    performance_date: Mapped[date] = mapped_column(Date, index=True)
    pnl: Mapped[Decimal] = mapped_column(money, default=0)
    trades: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class SourcePerformance(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "source_performance"
    source_id: Mapped[UUID] = mapped_column(ForeignKey("telegram_sources.id"), index=True)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    signals: Mapped[int] = mapped_column(Integer, default=0)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


Index("ix_positions_account_status", Position.account_id, Position.status)
Index("ix_trades_account_opened", Trade.account_id, Trade.opened_at)

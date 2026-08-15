from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import structlog
from redis.asyncio import Redis
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.brokers.mock import MockBrokerAdapter
from app.brokers.registry import BrokerConfigurationError, demo_broker_registry
from app.core.config import settings as app_settings
from app.core.enums import AccountStatus, Decision, SignalStatus, TradingMode
from app.core.health import TELEGRAM_HEARTBEAT_KEY
from app.execution.demo import DemoAccount, DemoTradingEngine
from app.execution.shadow import ShadowAccount, ShadowTradingEngine
from app.models.entities import (
    AccountSetting,
    AuditLog,
    BrokerConnection,
    Order,
    Position,
    PositionEvent,
    PositionTarget,
    PropRuleProfile,
    RuleSnapshot,
    RiskSnapshot,
    Signal,
    SignalAccountDecision,
    SignalUpdate as SignalUpdateRow,
    SystemSetting,
    TelegramMessage,
    TelegramSource,
    Trade,
    TradingAccount,
    VirtualOrder,
    VirtualPosition,
)
from app.notifications.service import queue_notification
from app.monitoring.live_equity import persist_demo_live_equity
from app.positions.guard import ConcurrentLimits
from app.positions.manager import LifecycleRecorder, ManagedPosition, PositionManagementService
from app.positions.tp_allocation import TargetAllocation
from app.positions.updates import apply_position_update
from app.signal_engine.fingerprint import signal_fingerprint
from app.signal_parser.parser import DeterministicSignalParser, ManualReviewRequired, ParseError
from app.schemas.signal import NormalizedSignal
from app.prop_rules.engine import RuleContext, evaluate_rules
from app.risk.daily_guard import DailyGuardInput, evaluate_daily_guard
from app.risk.drawdown import calculate_drawdown
from app.risk.live_equity import LiveEquityUnavailable, capture_live_equity, remaining_daily_buffer
from app.risk.trading_day import trading_date
from app.models.entities import AccountDailyStat


logger = structlog.get_logger()


class ShadowBrokerRegistry:
    def __init__(self) -> None:
        self._brokers: dict[str, MockBrokerAdapter] = {}

    def get(self, account: TradingAccount) -> MockBrokerAdapter:
        key = str(account.id)
        broker = self._brokers.get(key)
        if broker is None:
            broker = MockBrokerAdapter(Decimal(account.current_balance))
            broker.connect()
            self._brokers[key] = broker
        return broker


broker_registry = ShadowBrokerRegistry()


def _timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc)
    return datetime.now(timezone.utc)


class TelegramPipeline:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.parser = DeterministicSignalParser()
        self.shadow = ShadowTradingEngine()
        self.demo = DemoTradingEngine()
        self.correlation_id: str | None = None

    async def process(self, payload: dict[str, object]) -> None:
        self.correlation_id = str(payload.get("correlation_id") or uuid4())
        chat_id = str(payload.get("chat_id", ""))
        source = await self.db.scalar(select(TelegramSource).where(TelegramSource.telegram_chat_id == chat_id, TelegramSource.enabled.is_(True)))
        if source is None:
            logger.info("telegram_message_ignored", chat_id=chat_id, reason="source_not_enabled", correlation_id=self.correlation_id)
            return
        message_id = int(payload["telegram_message_id"])
        body = str(payload.get("body", ""))
        existing = await self.db.scalar(select(TelegramMessage).where(TelegramMessage.source_id == source.id, TelegramMessage.telegram_message_id == message_id))
        edited = payload.get("edited_at") is not None
        if existing:
            existing.latest_content = body
            existing.body = body
            existing.edited_at = _timestamp(payload["edited_at"]) if edited else existing.edited_at
            existing.event_type = "EDIT" if edited else "DUPLICATE"
            executed_signal = await self.db.scalar(
                select(Signal)
                .join(SignalAccountDecision, SignalAccountDecision.signal_id == Signal.id)
                .where(Signal.source_id == source.id, Signal.telegram_message_id == message_id, SignalAccountDecision.decision == Decision.APPROVED)
            )
            disposition = existing_message_disposition(edited, executed_signal is not None)
            if disposition == "EXECUTED_EDIT":
                self._audit("EXECUTED_SIGNAL_EDITED", "signal", str(executed_signal.id), {"original": existing.original_content}, {"latest": body})
                self._notify("WARNING", "Executed Telegram signal was edited", f"Signal {executed_signal.id} was not re-entered; review the edit manually.")
                await self.db.commit()
                return
            if disposition == "IGNORE_DUPLICATE":
                await self.db.commit()
                return
        else:
            existing = TelegramMessage(
                source_id=source.id,
                correlation_id=self.correlation_id,
                telegram_message_id=message_id,
                sender=str(payload.get("sender_id")) if payload.get("sender_id") else None,
                message_timestamp=_timestamp(payload.get("timestamp")),
                body=body,
                original_content=str(payload.get("original_text") or body),
                latest_content=body,
                event_type="EDIT" if edited else "NEW",
                reply_to_message_id=int(payload["reply_to_message_id"]) if payload.get("reply_to_message_id") else None,
                edited_at=_timestamp(payload["edited_at"]) if edited else None,
                attachments=[{"type": "HISTORICAL_REPLAY", "original_timestamp": payload.get("historical_timestamp"), "original_edited_at": payload.get("historical_edited_at")}] if payload.get("historical_timestamp") else [],
            )
            self.db.add(existing)

        try:
            normalized = self.parser.parse(body, source.id, message_id, existing.message_timestamp)
        except ParseError:
            await self._record_non_signal(source, existing, body)
            await self.db.commit()
            return

        fingerprint = signal_fingerprint(normalized)
        prior_signal = await self.db.scalar(select(Signal).where(Signal.source_id == source.id, Signal.telegram_message_id == message_id))
        duplicate = await self.db.scalar(select(Signal).where(Signal.fingerprint == fingerprint))
        if duplicate and duplicate.id != getattr(prior_signal, "id", None):
            self._audit("DUPLICATE_SIGNAL_IGNORED", "telegram_message", str(existing.id), None, {"fingerprint": fingerprint})
            await self.db.commit()
            return
        values = {
            "correlation_id": self.correlation_id,
            "symbol": normalized.symbol, "direction": normalized.direction, "order_type": normalized.order_type,
            "entry_price": normalized.entry_price, "entry_min": normalized.entry_min, "entry_max": normalized.entry_max,
            "stop_loss": normalized.stop_loss, "take_profits": [str(value) for value in normalized.take_profits],
            "risk_hint": normalized.risk_hint, "risk_classification": normalized.risk_classification,
            "signal_timestamp": normalized.timestamp, "expires_at": normalized.timestamp + timedelta(seconds=120),
            "status": SignalStatus.VALIDATED, "confidence": normalized.confidence, "raw_text": body,
            "parsed_json": normalized.model_dump(mode="json"), "fingerprint": fingerprint,
        }
        if prior_signal and edited:
            await self.db.execute(delete(SignalAccountDecision).where(SignalAccountDecision.signal_id == prior_signal.id))
            for key, value in values.items():
                setattr(prior_signal, key, value)
            signal = prior_signal
            self._audit("UNEXECUTED_SIGNAL_REPARSED", "signal", str(signal.id), None, {"fingerprint": fingerprint})
        elif prior_signal:
            await self.db.commit()
            return
        else:
            signal = Signal(source_id=source.id, telegram_message_id=message_id, **values)
            self.db.add(signal)
        await self.db.flush()
        await self._route(source, signal, normalized)
        await self.db.commit()

    async def route_saved(self, signal_id: UUID) -> None:
        signal = await self.db.get(Signal, signal_id)
        if signal is None:
            raise ValueError("Signal not found")
        self.correlation_id = signal.correlation_id
        if await self.db.scalar(select(SignalAccountDecision.id).where(SignalAccountDecision.signal_id == signal.id).limit(1)):
            return
        source = await self.db.get(TelegramSource, signal.source_id)
        if source is None or not source.enabled:
            return
        normalized = NormalizedSignal.model_validate(signal.parsed_json)
        await self._route(source, signal, normalized)
        await self.db.commit()

    async def _record_non_signal(self, source: TelegramSource, message: TelegramMessage, body: str) -> None:
        try:
            update = self.parser.parse_update(body)
        except ManualReviewRequired as exc:
            reason = str(exc)
            self._audit("SIGNAL_UPDATE_MANUAL_REVIEW_REQUIRED", "telegram_message", str(message.id), None, {"reason": reason, "source_id": str(source.id)})
            self._notify("WARNING", "Trade update needs manual review", f"Message from {source.name}: {reason}. No position was changed.")
            return
        except ParseError as exc:
            self._audit("TELEGRAM_MESSAGE_UNRECOGNIZED", "telegram_message", str(message.id), None, {"source_id": str(source.id), "reason": str(exc)})
            return
        query = select(Signal).join(Position, Position.signal_id == Signal.id).where(Signal.source_id == source.id, Position.status == "OPEN").distinct()
        candidates: list[Signal]
        if message.reply_to_message_id is not None:
            replied = list((await self.db.scalars(query.where(Signal.telegram_message_id == message.reply_to_message_id))).all())
            candidates = replied if replied else []
        elif update.symbol:
            candidates = list((await self.db.scalars(query.where(Signal.symbol == update.symbol))).all())
        else:
            candidates = list((await self.db.scalars(query.order_by(Signal.signal_timestamp.desc()).limit(2))).all())
        if len(candidates) != 1:
            reason = "NO_ACTIVE_SIGNAL" if not candidates else "AMBIGUOUS_ACTIVE_SIGNALS"
            self._audit("SIGNAL_UPDATE_MANUAL_REVIEW_REQUIRED", "telegram_message", str(message.id), None, {"action": update.action, "reason": reason, "candidate_signal_ids": [str(item.id) for item in candidates]})
            self._notify("WARNING", "Trade update needs manual review", f"{update.action} from {source.name}: {reason}. No position was changed.")
            return
        signal = candidates[0]
        update_row = SignalUpdateRow(signal_id=signal.id, telegram_message_id=message.telegram_message_id, action=update.action, payload=update.model_dump(mode="json"), confidence=update.confidence, status="RESOLVED")
        self.db.add(update_row)
        positions = list((await self.db.scalars(select(Position).where(Position.signal_id == signal.id, Position.status == "OPEN"))).all())
        failures: list[str] = []
        for row in positions:
            account = await self.db.get(TradingAccount, row.account_id)
            if account is None or account.trading_mode != TradingMode.SHADOW:
                failures.append(f"{row.id}: not a SHADOW position")
                continue
            settings = await self.db.scalar(select(AccountSetting).where(AccountSetting.account_id == account.id))
            broker = broker_registry.get(account)
            if row.broker_symbol not in broker.prices:
                broker.prices[row.broker_symbol] = Decimal(row.entry_price)
                broker.spreads[row.broker_symbol] = Decimal("0")
            broker.positions.setdefault(row.broker_position_id, {"id": row.broker_position_id, "symbol": row.broker_symbol, "direction": row.direction.value, "size": Decimal(row.remaining_volume), "original_size": Decimal(row.original_volume), "entry_price": Decimal(row.entry_price), "stop_loss": Decimal(row.stop_loss or row.entry_price), "take_profit": None, "restored": True})
            target_rows = list((await self.db.scalars(select(PositionTarget).where(PositionTarget.position_id == row.id).order_by(PositionTarget.sequence))).all())
            metadata = dict(row.metadata_json or {})
            managed = ManagedPosition(row.broker_position_id, str(signal.id), row.broker_symbol, row.direction, Decimal(row.entry_price), Decimal(row.stop_loss or row.entry_price), Decimal(row.original_volume), Decimal(row.remaining_volume), [TargetAllocation(target.sequence, Decimal(target.price), Decimal(target.requested_percentage), Decimal(target.allocated_volume), target.status, target.merged_into_sequence) for target in target_rows], row.status, realized_pnl=Decimal(str(metadata.get("realized_pnl", "0"))), initial_risk=Decimal(str(metadata.get("initial_risk", "0"))))
            recorder = LifecycleRecorder()
            manager = PositionManagementService(broker, recorder)
            spec = broker.get_symbol_info(row.broker_symbol)
            offset = (spec.tick_size * Decimal(settings.break_even_offset_points)) if spec and settings else Decimal("0")
            application = apply_position_update(manager, managed, update, offset)
            result = application.results[-1] if application.results else None
            for action_result in application.results:
                if not action_result.accepted:
                    failures.append(f"{row.id}: {action_result.code}")
            row.remaining_volume, row.size, row.stop_loss, row.status = managed.remaining_volume, managed.remaining_volume, managed.stop_loss, managed.status
            metadata["realized_pnl"] = str(managed.realized_pnl)
            metadata["initial_risk"] = str(managed.initial_risk)
            metadata["journal"] = [*(metadata.get("journal") or []), *recorder.events]
            row.metadata_json = metadata
            for target_row, target in zip(target_rows, managed.targets, strict=True):
                target_row.status = target.status
                if target.status.value == "EXECUTED" and target_row.executed_at is None:
                    target_row.executed_at = datetime.now(timezone.utc)
            for event in recorder.events:
                self.db.add(PositionEvent(position_id=row.id, correlation_id=row.correlation_id or self.correlation_id, timestamp=datetime.now(timezone.utc), event=event["action"], payload=event))
                self._audit(event["action"], "position", str(row.id), None, event)
                self._notify("INFO", event["action"].replace("_", " ").title(), f"{account.name}: {signal.symbol} position {row.id}")
            trade = await self.db.scalar(select(Trade).where(Trade.signal_id == signal.id, Trade.account_id == account.id).order_by(Trade.opened_at.desc()))
            if trade:
                previous_pnl = Decimal(trade.pnl or 0)
                journal = dict(trade.journal or {})
                journal["events"] = [*(journal.get("events") or []), *recorder.events]
                trade.journal = journal
                trade.pnl = managed.realized_pnl
                trade.r_result = managed.realized_pnl / managed.initial_risk if managed.initial_risk > 0 else None
                account.current_balance = Decimal(account.current_balance) + managed.realized_pnl - previous_pnl
                if managed.status == "CLOSED":
                    trade.closed_at = datetime.now(timezone.utc)
                    trade.exit_price = result.fill_price if result else None
                    account.current_equity = account.current_balance
        update_row.status = "FAILED" if failures else "EXECUTED"
        if failures:
            update_row.payload = update_row.payload | {"failures": failures}
            self._notify("WARNING", "Trade update partially failed", "; ".join(failures))

    async def _route(self, source: TelegramSource, signal: Signal, normalized: Any) -> None:
        query = select(TradingAccount).options(selectinload(TradingAccount.settings)).where(TradingAccount.status == AccountStatus.ACTIVE)
        accounts = list((await self.db.scalars(query)).all())
        if source.allowed_account_ids:
            allowed = set(source.allowed_account_ids)
            accounts = [account for account in accounts if str(account.id) in allowed]
        if not accounts:
            signal.status = SignalStatus.REJECTED
            self._notify(
                "WARNING",
                f"Signal rejected: {normalized.symbol}",
                f"{source.name}: no active trading accounts are mapped to this source.",
            )
            return
        shadow_accounts: list[ShadowAccount] = []
        demo_accounts: list[DemoAccount] = []
        demo_connections: dict[str, tuple[BrokerConnection, Any]] = {}
        model_accounts: list[TradingAccount] = []
        automation_by_id: dict[str, bool] = {}
        profiles: dict[UUID, PropRuleProfile | None] = {}
        safety = await self.db.scalar(select(SystemSetting).where(SystemSetting.key == "global_safety"))
        globally_enabled = routing_enabled(safety.value if safety else None)
        telegram_healthy = await telegram_listener_healthy()
        for account in accounts:
            settings = account.settings
            if account.rule_profile_id not in profiles:
                profiles[account.rule_profile_id] = await self.db.get(PropRuleProfile, account.rule_profile_id)
            profile = profiles[account.rule_profile_id]
            if settings is None:
                await self._decision(signal, account, Decision.REJECTED, ["Account risk settings are missing"])
                continue
            if account.trading_mode not in {TradingMode.SHADOW, TradingMode.DEMO}:
                await self._decision(signal, account, Decision.REJECTED, ["Only SHADOW and explicitly acknowledged MT5 DEMO accounts are executed by this worker"])
                continue
            if account.trading_mode == TradingMode.DEMO and account.automation_permission_acknowledged_at is None:
                await self._decision(signal, account, Decision.REJECTED, ["MT5 DEMO automation acknowledgement is missing"])
                continue
            if account.trading_mode == TradingMode.DEMO and not telegram_healthy:
                await self._decision(signal, account, Decision.REJECTED, ["Telegram listener heartbeat is unavailable"])
                continue
            if profile is None:
                await self._decision(signal, account, Decision.REJECTED, ["Assigned rule profile is missing"])
                continue
            if not globally_enabled:
                await self._decision(signal, account, Decision.REJECTED, ["Global emergency stop blocks new trades"])
                continue
            demo_connection = None
            demo_broker = None
            if account.trading_mode == TradingMode.DEMO:
                demo_connection = await self.db.scalar(select(BrokerConnection).where(BrokerConnection.account_id == account.id))
                if demo_connection is None:
                    await self._decision(signal, account, Decision.REJECTED, ["MT5 DEMO broker connection is missing"])
                    continue
                try:
                    demo_broker = demo_broker_registry.get(account, demo_connection)
                    if not (demo_broker.health_check() or demo_broker.connect()):
                        raise LiveEquityUnavailable(demo_broker.last_error or "MT5 DEMO connection is unhealthy")
                    live_snapshot = capture_live_equity(demo_broker)
                    await persist_demo_live_equity(self.db, account, profile, live_snapshot)
                    await self.db.flush()
                except (BrokerConfigurationError, RuntimeError, ValueError, LiveEquityUnavailable) as exc:
                    demo_connection.status = "UNHEALTHY"
                    await self._decision(signal, account, Decision.REJECTED, [f"LIVE_EQUITY_UNAVAILABLE: {exc}"])
                    continue
                demo_connection.status = "CONNECTED"
                demo_connection.last_heartbeat_at = datetime.now(timezone.utc)
            permitted = bool(profile and profile.enabled and profile.ea_allowed and profile.signal_copying_allowed and profile.third_party_signal_allowed)
            try:
                current_trading_date = trading_date(datetime.now(timezone.utc), profile.daily_reset_timezone)
            except ValueError as exc:
                await self._decision(signal, account, Decision.REJECTED, [str(exc)])
                continue
            stat = await self.db.scalar(select(AccountDailyStat).where(AccountDailyStat.account_id == account.id, AccountDailyStat.trading_date == current_trading_date))
            daily = evaluate_daily_guard(DailyGuardInput(
                realized_pnl=Decimal(stat.realized_pnl) if stat else Decimal("0"), floating_pnl=Decimal(stat.floating_pnl) if stat else Decimal("0"),
                trades=stat.trades if stat else 0, consecutive_losses=stat.consecutive_losses if stat else 0,
                max_loss=Decimal(settings.maximum_daily_loss_internal), max_profit=Decimal(settings.maximum_daily_profit) if settings.maximum_daily_profit else None,
                max_trades=settings.maximum_trades_per_day, max_consecutive_losses=settings.maximum_consecutive_losses, manual_lock=bool(stat and stat.locked_reason),
            ))
            drawdown = calculate_drawdown(profile.maximum_drawdown_type, Decimal(account.initial_balance), Decimal(account.current_balance), Decimal(account.current_equity), Decimal(account.high_water_balance), Decimal(account.high_water_equity), Decimal(profile.maximum_drawdown), Decimal(settings.overall_drawdown_safety_buffer))
            open_rows = list((await self.db.scalars(select(Position).where(Position.account_id == account.id, Position.status == "OPEN"))).all())
            current_exposure = sum((abs(Decimal(row.entry_price) - Decimal(row.stop_loss or row.entry_price)) * Decimal(row.remaining_volume) for row in open_rows), Decimal("0"))
            proposed_risk = min(Decimal(account.current_equity) * Decimal(settings.risk_per_trade), Decimal(settings.maximum_risk_per_trade))
            daily_buffer = remaining_daily_buffer(
                Decimal(settings.maximum_daily_loss_internal),
                Decimal(stat.realized_pnl) if stat else Decimal("0"),
                Decimal(stat.floating_pnl) if stat else Decimal("0"),
            )
            rules = evaluate_rules(RuleContext(
                symbol=normalized.symbol, direction=normalized.direction, automation_requested=True, proposed_risk=proposed_risk,
                remaining_daily_buffer=daily_buffer,
                remaining_overall_buffer=max(Decimal("0"), drawdown.remaining_buffer), open_positions=len(open_rows), maximum_positions=profile.maximum_positions,
                allowed_symbols=set(profile.allowed_symbols), restricted_symbols=set(profile.restricted_symbols), ea_allowed=profile.ea_allowed,
                signal_copying_allowed=profile.signal_copying_allowed, third_party_signal_allowed=profile.third_party_signal_allowed,
                news_trading_allowed=profile.news_trading_allowed, hedging_allowed=profile.hedging_allowed,
                opposite_position_open=any(row.symbol == normalized.symbol and row.direction != normalized.direction for row in open_rows),
            ))
            blockers = [*daily.reasons, *rules.reasons]
            if drawdown.breached:
                blockers.append("OVERALL_DRAWDOWN_SAFETY_THRESHOLD")
            if blockers:
                await self._decision(signal, account, Decision.REJECTED, blockers)
                continue
            overall_buffer = max(Decimal("0.01"), drawdown.remaining_buffer)
            if account.trading_mode == TradingMode.SHADOW:
                shadow_accounts.append(self._shadow_account(account, settings, permitted, daily.risk_multiplier, overall_buffer, current_exposure))
            else:
                try:
                    configured_cap = Decimal(str((settings.options or {}).get("demo_max_volume", "0.05")))
                except (RuntimeError, ValueError) as exc:
                    demo_connection.status = "UNHEALTHY"
                    await self._decision(signal, account, Decision.REJECTED, [str(exc)])
                    continue
                profile_cap = Decimal(profile.maximum_lots) if profile.maximum_lots is not None else Decimal("0.01")
                hard_cap = min(configured_cap, profile_cap, Decimal("0.05"))
                demo_accounts.append(self._demo_account(account, settings, demo_broker, permitted, daily.risk_multiplier, daily_buffer, overall_buffer, current_exposure, hard_cap))
                demo_connections[str(account.id)] = (demo_connection, demo_broker)
            model_accounts.append(account)
            automation_by_id[str(account.id)] = permitted and settings.enable_signal_execution
        runs = [
            (self.shadow.route(normalized, shadow_accounts), TradingMode.SHADOW),
            (self.demo.route(normalized, demo_accounts), TradingMode.DEMO),
        ]
        for connection, broker in demo_connections.values():
            connection.status = "CONNECTED" if broker.health_check() else "UNHEALTHY"
            if connection.status == "CONNECTED":
                connection.last_heartbeat_at = datetime.now(timezone.utc)
        by_id = {str(account.id): account for account in model_accounts}
        all_decisions = []
        for run, mode in runs:
            all_decisions.extend(run.decisions)
            for decision in run.decisions:
                account = by_id[decision.account_id]
                rule_snapshot = RuleSnapshot(payload={"automation_permitted": automation_by_id[decision.account_id], "trading_mode": mode.value})
                risk_snapshot = RiskSnapshot(payload={"classification": normalized.risk_classification.value, "effective_risk_percent": str(decision.effective_risk_percent)})
                self.db.add_all([rule_snapshot, risk_snapshot])
                await self.db.flush()
                execution_id = None
                if decision.decision == Decision.APPROVED:
                    execution_id = f"{normalized.source_id}:{normalized.telegram_message_id}:{account.id}" if mode == TradingMode.DEMO else f"{signal.id}:{account.id}"
                row = SignalAccountDecision(signal_id=signal.id, correlation_id=signal.correlation_id or self.correlation_id, account_id=account.id, decision=decision.decision, reasons=decision.reasons, risk_amount=None, risk_percent=decision.effective_risk_percent, calculated_size=decision.calculated_size or None, rule_snapshot_id=rule_snapshot.id, risk_snapshot_id=risk_snapshot.id, execution_id=execution_id)
                self.db.add(row)
                self._notify(
                    "INFO" if decision.decision == Decision.APPROVED else "WARNING",
                    f"Signal {decision.decision.value}: {normalized.symbol}",
                    f"{account.name}: {normalized.direction.value} {normalized.symbol}; "
                    + (f"size {decision.calculated_size} lots ({mode.value})." if decision.decision == Decision.APPROVED else f"reasons: {'; '.join(decision.reasons)}"),
                )
                if decision.decision != Decision.APPROVED or not decision.position_id:
                    continue
                managed = run.positions[decision.position_id]
                is_virtual = mode == TradingMode.SHADOW
                position = Position(account_id=account.id, correlation_id=signal.correlation_id or self.correlation_id, signal_id=signal.id, broker_position_id=managed.position_id, symbol=normalized.symbol, broker_symbol=managed.symbol, direction=managed.direction, size=managed.original_volume, original_volume=managed.original_volume, remaining_volume=managed.remaining_volume, entry_price=managed.entry_price, stop_loss=managed.stop_loss, take_profit=managed.targets[-1].price if managed.targets else None, status="OPEN", is_virtual=is_virtual, metadata_json={"journal": managed.journal, "realized_pnl": "0", "initial_risk": str(managed.initial_risk), "trading_mode": mode.value})
                self.db.add(position)
                await self.db.flush()
                for target in managed.targets:
                    self.db.add(PositionTarget(position_id=position.id, sequence=target.sequence, price=target.price, requested_percentage=target.requested_percentage, allocated_volume=target.allocated_volume, status=target.status, merged_into_sequence=target.merged_into_sequence))
                order_payload = {"symbol": managed.symbol, "direction": managed.direction.value, "volume": str(managed.original_volume), "stop_loss": str(managed.stop_loss), "trading_mode": mode.value}
                durable_rows = [
                    Order(execution_id=execution_id, correlation_id=signal.correlation_id or self.correlation_id, signal_id=signal.id, account_id=account.id, broker_order_id=managed.position_id, status="FILLED", request=order_payload, response={"virtual": is_virtual, "fill_price": str(managed.entry_price)}),
                    Trade(account_id=account.id, correlation_id=signal.correlation_id or self.correlation_id, signal_id=signal.id, symbol=normalized.symbol, direction=managed.direction, entry_price=managed.entry_price, exit_price=None, size=managed.original_volume, pnl=None, r_result=None, opened_at=datetime.now(timezone.utc), closed_at=None, journal={"events": managed.journal}),
                ]
                if is_virtual:
                    durable_rows.extend([
                        VirtualOrder(account_id=account.id, signal_id=signal.id, execution_id=execution_id, status="FILLED", payload=order_payload),
                        VirtualPosition(account_id=account.id, signal_id=signal.id, symbol=normalized.symbol, state={"broker_position_id": managed.position_id, "remaining_volume": str(managed.remaining_volume), "targets": [target.__dict__ | {"status": target.status.value, "price": str(target.price), "requested_percentage": str(target.requested_percentage), "allocated_volume": str(target.allocated_volume)} for target in managed.targets]}, status="OPEN"),
                    ])
                self.db.add_all(durable_rows)
                self._audit(f"{mode.value}_POSITION_OPENED", "position", str(position.id), None, {"execution_id": execution_id, "classification": normalized.risk_classification.value})
                self._notify("INFO", f"{mode.value} position opened", f"{account.name}: {managed.direction.value} {normalized.symbol} {managed.original_volume} lots.")
        signal.status = SignalStatus.EXECUTED if any(item.decision == Decision.APPROVED for item in all_decisions) else SignalStatus.REJECTED

    @staticmethod
    def _shadow_account(account: TradingAccount, settings: AccountSetting, permitted: bool, daily_multiplier: Decimal, overall_buffer: Decimal, current_exposure: Decimal) -> ShadowAccount:
        return ShadowAccount(
            account_id=str(account.id), broker=broker_registry.get(account), equity=Decimal(account.current_equity), active=True,
            automation_permitted=permitted and settings.enable_signal_execution,
            base_risk_percent=Decimal(settings.risk_per_trade) * daily_multiplier, maximum_risk=Decimal(settings.maximum_risk_per_trade),
            daily_buffer=Decimal(settings.maximum_daily_loss_internal), overall_buffer=overall_buffer,
            maximum_total_exposure=Decimal(settings.maximum_total_exposure), current_total_exposure=current_exposure, risk_multipliers=settings.risk_multipliers,
            symbol_mappings=settings.symbol_mappings, tp_preset=settings.tp_allocation_preset,
            tp_custom=[Decimal(value) for value in settings.tp_custom_allocations] or None,
            limits=ConcurrentLimits(settings.maximum_open_positions, settings.maximum_positions_per_symbol, settings.maximum_positions_per_direction, settings.maximum_pending_orders, settings.concurrent_limit_action),
        )

    @staticmethod
    def _demo_account(account: TradingAccount, settings: AccountSetting, broker: Any, permitted: bool, daily_multiplier: Decimal, daily_buffer: Decimal, overall_buffer: Decimal, current_exposure: Decimal, hard_cap: Decimal) -> DemoAccount:
        return DemoAccount(
            account_id=str(account.id), broker=broker, equity=Decimal(account.current_equity),
            automation_permitted=permitted and settings.enable_signal_execution,
            base_risk_percent=Decimal(settings.risk_per_trade) * daily_multiplier, maximum_risk=Decimal(settings.maximum_risk_per_trade),
            daily_buffer=daily_buffer, overall_buffer=overall_buffer,
            maximum_total_exposure=Decimal(settings.maximum_total_exposure), current_total_exposure=current_exposure, max_volume=hard_cap,
            risk_multipliers=settings.risk_multipliers, symbol_mappings=settings.symbol_mappings, tp_preset=settings.tp_allocation_preset,
            tp_custom=[Decimal(value) for value in settings.tp_custom_allocations] or None,
            limits=ConcurrentLimits(settings.maximum_open_positions, settings.maximum_positions_per_symbol, settings.maximum_positions_per_direction, settings.maximum_pending_orders, settings.concurrent_limit_action),
        )

    async def _decision(self, signal: Signal, account: TradingAccount, decision: Decision, reasons: list[str]) -> None:
        self.db.add(SignalAccountDecision(signal_id=signal.id, correlation_id=signal.correlation_id or self.correlation_id, account_id=account.id, decision=decision, reasons=reasons, risk_amount=None, risk_percent=None, calculated_size=None, rule_snapshot_id=None, risk_snapshot_id=None, execution_id=None))
        self._notify(
            "WARNING" if decision == Decision.REJECTED else "INFO",
            f"Signal {decision.value}: {signal.symbol}",
            f"{account.name}: {'; '.join(reasons) if reasons else 'No additional reason supplied.'}",
        )

    def _audit(self, action: str, entity: str, entity_id: str | None, before: dict[str, Any] | None, after: dict[str, Any] | None) -> None:
        self.db.add(AuditLog(timestamp=datetime.now(timezone.utc), correlation_id=self.correlation_id, user_id=None, action=action, entity=entity, entity_id=entity_id, before=before, after=after, metadata_json={"worker": True}))

    def _notify(self, severity: str, subject: str, body: str) -> None:
        queue_notification(self.db, severity, subject, body, self.correlation_id)


def routing_enabled(global_safety: dict[str, object] | None) -> bool:
    return bool((global_safety or {}).get("global_trading_enabled", True))


async def telegram_listener_healthy() -> bool:
    redis = Redis.from_url(app_settings.redis_url, decode_responses=True)
    try:
        return bool(await redis.get(TELEGRAM_HEARTBEAT_KEY))
    except Exception:
        return False
    finally:
        await redis.aclose()


def existing_message_disposition(edited: bool, already_executed: bool) -> str:
    if edited and already_executed:
        return "EXECUTED_EDIT"
    if edited:
        return "REPARSE"
    return "IGNORE_DUPLICATE"

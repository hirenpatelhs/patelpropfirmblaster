from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.base import BrokerAdapter
from app.brokers.registry import BrokerConfigurationError, demo_broker_registry
from app.core.config import settings
from app.core.enums import Direction, TargetStatus, TradingMode
from app.models.entities import AccountDailyStat, AuditLog, BrokerConnection, DailyPerformance, Notification, Order, Position, PositionEvent, PositionTarget, PropRuleProfile, Trade, TradingAccount
from app.monitoring.recovery import reconcile_positions
from app.monitoring.live_equity import persist_demo_live_equity
from app.notifications.service import queue_notification
from app.positions.manager import LifecycleRecorder, ManagedPosition, PositionManagementService
from app.positions.tp_allocation import TargetAllocation
from app.risk.drawdown import update_high_water
from app.risk.live_equity import LiveEquityUnavailable, capture_live_equity
from app.risk.trading_day import trading_date
from app.workers.pipeline import broker_registry


class ReconciliationService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def run(self, payload: dict[str, object]) -> None:
        account_id = payload.get("account_id")
        query = select(TradingAccount)
        if account_id:
            query = query.where(TradingAccount.id == UUID(str(account_id)))
        accounts = list((await self.db.scalars(query)).all())
        for account in accounts:
            connection = None
            if account.trading_mode == TradingMode.SHADOW:
                broker = broker_registry.get(account)
            elif account.trading_mode == TradingMode.DEMO:
                connection = await self.db.scalar(select(BrokerConnection).where(BrokerConnection.account_id == account.id))
                if connection is None:
                    continue
                try:
                    broker = demo_broker_registry.get(account, connection)
                except (BrokerConfigurationError, RuntimeError, ValueError) as exc:
                    connection.status = "UNHEALTHY"
                    queue_notification(self.db, "ERROR", "MT5 demo monitor unavailable", f"{account.name}: {exc}", str(payload.get("correlation_id") or "reconciliation"))
                    continue
                if not (broker.health_check() or broker.connect()):
                    connection.status = "UNHEALTHY"
                    queue_notification(self.db, "ERROR", "MT5 demo disconnected", f"{account.name}: {broker.last_error or 'connection failed'}", str(payload.get("correlation_id") or "reconciliation"))
                    continue
                connection.status = "CONNECTED"
                connection.last_heartbeat_at = datetime.now(timezone.utc)
            else:
                continue
            rows = list((await self.db.scalars(select(Position).where(Position.account_id == account.id, Position.status == "OPEN").with_for_update(skip_locked=True))).all())
            # SHADOW state is restored from durable position rows before monitoring.
            if account.trading_mode == TradingMode.SHADOW:
                for row in rows:
                    if row.broker_position_id not in broker.positions:
                        if row.broker_symbol not in broker.prices:
                            broker.prices[row.broker_symbol] = Decimal(row.entry_price)
                            broker.spreads[row.broker_symbol] = Decimal("0")
                        broker.positions[row.broker_position_id] = {
                            "id": row.broker_position_id, "symbol": row.broker_symbol,
                            "direction": row.direction.value, "size": Decimal(row.remaining_volume),
                            "original_size": Decimal(row.original_volume), "entry_price": Decimal(row.entry_price),
                            "stop_loss": Decimal(row.stop_loss) if row.stop_loss is not None else None,
                            "take_profit": Decimal(row.take_profit) if row.take_profit is not None else None,
                            "restored": True,
                        }
            broker_positions = broker.get_open_positions()
            # A transport failure must never be interpreted as a flat broker position.
            if not broker.health_check():
                if connection is not None:
                    connection.status = "UNHEALTHY"
                queue_notification(self.db, "ERROR", "Broker reconciliation unavailable", f"{account.name}: broker health failed after position query; PPB positions were left unchanged.", str(payload.get("correlation_id") or "reconciliation"))
                continue
            if account.trading_mode == TradingMode.DEMO:
                try:
                    live_snapshot = capture_live_equity(broker)
                except LiveEquityUnavailable as exc:
                    connection.status = "UNHEALTHY"
                    queue_notification(self.db, "ERROR", "MT5 live equity unavailable", f"{account.name}: {exc}; new DEMO routing remains blocked.", str(payload.get("correlation_id") or "reconciliation"))
                    continue
                broker_by_id = {str(item.get("id") or item.get("ticket")): item for item in broker_positions}
                for row in rows:
                    broker_row = broker_by_id.get(row.broker_position_id)
                    if broker_row is not None:
                        order = await self.db.scalar(select(Order).where(Order.account_id == account.id, Order.broker_order_id == row.broker_position_id).order_by(Order.created_at.desc()))
                        expected_comment = broker.expected_position_comment(order.execution_id) if order else None
                        if expected_comment is not None and str(broker_row.get("comment", "")) != expected_comment:
                            queue_notification(self.db, "ERROR", "MT5 position ownership mismatch", f"{account.name}: ticket {row.broker_position_id} has an unexpected idempotency comment; PPB left the position OPEN.", row.correlation_id)
                        continue
                    if broker_row is None:
                        await self._reconcile_broker_closed(account, row, broker, payload)
                rows = [row for row in rows if row.status == "OPEN"]
            db_positions = [{"broker_position_id": row.broker_position_id, "remaining_volume": row.remaining_volume, "stop_loss": row.stop_loss, "take_profit": row.take_profit} for row in rows]
            for issue in reconcile_positions(db_positions, broker, broker_positions):
                matched = next((row for row in rows if row.broker_position_id == issue.broker_position_id), None)
                correlation_id = matched.correlation_id if matched else str(payload.get("correlation_id") or "reconciliation")
                self.db.add(AuditLog(timestamp=datetime.now(timezone.utc), correlation_id=correlation_id, user_id=None, action=f"RECONCILIATION_{issue.kind}", entity="position", entity_id=issue.broker_position_id, before=None, after={"detail": issue.detail}, metadata_json={"account_id": str(account.id)}))
                queue_notification(self.db, "WARNING", f"Reconciliation: {issue.kind}", f"Account {account.name}, position {issue.broker_position_id}: {issue.detail}", correlation_id)
            for row in rows:
                target_rows = list((await self.db.scalars(select(PositionTarget).where(PositionTarget.position_id == row.id).order_by(PositionTarget.sequence))).all())
                metadata = dict(row.metadata_json or {})
                managed = ManagedPosition(
                    row.broker_position_id, str(row.signal_id or ""), row.broker_symbol, row.direction,
                    Decimal(row.entry_price), Decimal(row.stop_loss or row.entry_price), Decimal(row.original_volume), Decimal(row.remaining_volume),
                    [TargetAllocation(target.sequence, Decimal(target.price), Decimal(target.requested_percentage), Decimal(target.allocated_volume), target.status, target.merged_into_sequence) for target in target_rows],
                    row.status, realized_pnl=Decimal(str(metadata.get("realized_pnl", "0"))), initial_risk=Decimal(str(metadata.get("initial_risk", "0"))),
                )
                recorder = LifecycleRecorder()
                PositionManagementService(broker, recorder).monitor(managed)
                row.remaining_volume = managed.remaining_volume
                row.size = managed.remaining_volume
                row.stop_loss = managed.stop_loss
                row.status = managed.status
                metadata["realized_pnl"] = str(managed.realized_pnl)
                metadata["journal"] = [*(metadata.get("journal") or []), *recorder.events]
                row.metadata_json = metadata
                for target_row, target in zip(target_rows, managed.targets, strict=True):
                    if target_row.status != target.status:
                        target_row.status = target.status
                        target_row.executed_at = datetime.now(timezone.utc) if target.status.value == "EXECUTED" else None
                for event in recorder.events:
                    self.db.add(PositionEvent(position_id=row.id, correlation_id=row.correlation_id, timestamp=datetime.now(timezone.utc), event=event["action"], payload=event))
                    self.db.add(AuditLog(timestamp=datetime.now(timezone.utc), correlation_id=row.correlation_id, user_id=None, action=event["action"], entity="position", entity_id=str(row.id), before=None, after=event, metadata_json={"reconciliation": True}))
                    details = ", ".join(f"{key}={value}" for key, value in event.items() if key != "action")
                    queue_notification(
                        self.db,
                        "INFO",
                        event["action"].replace("_", " ").title(),
                        f"{account.name}: {row.direction.value} {row.symbol}; {details or 'position lifecycle updated'}.",
                        row.correlation_id,
                    )
                if recorder.events:
                    trade = await self.db.scalar(select(Trade).where(Trade.signal_id == row.signal_id, Trade.account_id == account.id).order_by(Trade.opened_at.desc()))
                    if trade:
                        previous_pnl = Decimal(trade.pnl or 0)
                        trade.pnl = managed.realized_pnl
                        trade.r_result = managed.realized_pnl / managed.initial_risk if managed.initial_risk > 0 else None
                        journal = dict(trade.journal or {})
                        journal["events"] = [*(journal.get("events") or []), *recorder.events]
                        trade.journal = journal
                        account.current_balance = Decimal(account.current_balance) + managed.realized_pnl - previous_pnl
                        if managed.status == "CLOSED":
                            trade.closed_at = datetime.now(timezone.utc)
                            prices = [event.get("price") for event in recorder.events if event.get("price") not in {None, "None"}]
                            trade.exit_price = Decimal(str(prices[-1])) if prices else None
            floating = Decimal("0")
            for row in rows:
                if row.status != "OPEN":
                    continue
                exit_price = broker.get_exit_price(row.broker_symbol, row.direction)
                spec = broker.get_symbol_info(row.broker_symbol)
                if exit_price is not None and spec is not None:
                    movement = exit_price - Decimal(row.entry_price) if row.direction == Direction.BUY else Decimal(row.entry_price) - exit_price
                    floating += (movement / spec.tick_size) * spec.tick_value * Decimal(row.remaining_volume)
            if account.trading_mode == TradingMode.DEMO:
                try:
                    live_snapshot = capture_live_equity(broker)
                    profile = await self.db.get(PropRuleProfile, account.rule_profile_id)
                    if profile is None:
                        raise LiveEquityUnavailable("assigned rule profile is missing")
                    await persist_demo_live_equity(self.db, account, profile, live_snapshot)
                except LiveEquityUnavailable as exc:
                    connection.status = "UNHEALTHY"
                    queue_notification(self.db, "ERROR", "MT5 live equity unavailable", f"{account.name}: {exc}; state was not assumed safe.", str(payload.get("correlation_id") or "reconciliation"))
                    continue
            else:
                account.current_equity = Decimal(account.current_balance) + floating
            high_water = update_high_water(Decimal(account.high_water_balance), Decimal(account.high_water_equity), Decimal(account.current_balance), Decimal(account.current_equity))
            account.high_water_balance, account.high_water_equity = high_water.balance, high_water.equity
            if account.trading_mode == TradingMode.SHADOW:
                broker.balance, broker.equity = Decimal(account.current_balance), Decimal(account.current_equity)
        await self.db.commit()

    async def _reconcile_broker_closed(
        self,
        account: TradingAccount,
        row: Position,
        broker: BrokerAdapter,
        payload: dict[str, object],
    ) -> bool:
        metadata = dict(row.metadata_json or {})
        if row.status != "OPEN" or metadata.get("broker_closure_reconciled"):
            return False
        if not broker.health_check():
            queue_notification(self.db, "ERROR", "Broker reconciliation unavailable", f"{account.name}: broker is disconnected; position {row.broker_position_id} was left OPEN.", row.correlation_id)
            return False

        snapshot = broker.get_closed_position(row.broker_position_id) or {}
        exit_value = snapshot.get("price") or broker.get_exit_price(row.broker_symbol, row.direction)
        reason = str(snapshot.get("reason") or "BROKER").upper()
        if exit_value is None:
            exit_value = row.stop_loss if reason == "SL" else (row.take_profit or row.stop_loss)
        if exit_value is None:
            queue_notification(self.db, "ERROR", "Broker closure needs manual review", f"{account.name}: no closing fill, quote, TP, or SL was available for position {row.broker_position_id}; PPB left it OPEN.", row.correlation_id)
            return False
        exit_price = Decimal(str(exit_value))
        closed_at = snapshot.get("closed_at") if isinstance(snapshot.get("closed_at"), datetime) else datetime.now(timezone.utc)
        if reason == "BROKER":
            if row.take_profit is not None and ((row.direction == Direction.BUY and exit_price >= Decimal(row.take_profit)) or (row.direction == Direction.SELL and exit_price <= Decimal(row.take_profit))):
                reason = "TP"
            elif row.stop_loss is not None and ((row.direction == Direction.BUY and exit_price <= Decimal(row.stop_loss)) or (row.direction == Direction.SELL and exit_price >= Decimal(row.stop_loss))):
                reason = "SL"

        remaining = Decimal(row.remaining_volume)
        prior_realized = Decimal(str(metadata.get("realized_pnl", "0")))
        broker_profit = snapshot.get("profit")
        if broker_profit is not None:
            closing_pnl = Decimal(str(broker_profit))
        else:
            movement = exit_price - Decimal(row.entry_price) if row.direction == Direction.BUY else Decimal(row.entry_price) - exit_price
            spec = broker.get_symbol_info(row.broker_symbol)
            closing_pnl = movement * remaining if spec is None else (movement / spec.tick_size) * spec.tick_value * remaining
        total_pnl = prior_realized + closing_pnl

        target_rows = list((await self.db.scalars(select(PositionTarget).where(PositionTarget.position_id == row.id).order_by(PositionTarget.sequence))).all())
        for target in target_rows:
            crossed = exit_price >= Decimal(target.price) if row.direction == Direction.BUY else exit_price <= Decimal(target.price)
            if reason == "TP" and crossed and target.status in {TargetStatus.WAITING, TargetStatus.MERGED}:
                target.status = TargetStatus.EXECUTED
                target.executed_at = closed_at

        row.remaining_volume = Decimal("0")
        row.size = Decimal("0")
        row.status = "CLOSED"
        metadata.update({
            "realized_pnl": str(total_pnl),
            "broker_closure_reconciled": True,
            "broker_closure": {"reason": reason, "exit_price": str(exit_price), "closed_at": closed_at.isoformat(), "deal_ticket": snapshot.get("deal_ticket")},
        })
        event = {"action": "BROKER_POSITION_CLOSED", "position_id": row.broker_position_id, "reason": reason, "price": str(exit_price), "volume": str(remaining), "pnl": str(closing_pnl)}
        metadata["journal"] = [*(metadata.get("journal") or []), event]
        row.metadata_json = metadata

        trade = await self.db.scalar(select(Trade).where(Trade.signal_id == row.signal_id, Trade.account_id == account.id).order_by(Trade.opened_at.desc()))
        was_completed = bool(trade and trade.closed_at is not None)
        initial_risk = Decimal(str(metadata.get("initial_risk", "0")))
        if trade is None:
            trade = Trade(
                account_id=account.id, correlation_id=row.correlation_id, signal_id=row.signal_id,
                symbol=row.symbol, direction=row.direction, entry_price=row.entry_price, exit_price=exit_price,
                size=row.original_volume, pnl=total_pnl, r_result=total_pnl / initial_risk if initial_risk > 0 else None, opened_at=row.created_at or closed_at,
                closed_at=closed_at, journal={"events": [event]},
            )
            self.db.add(trade)
        else:
            trade.exit_price = exit_price
            trade.pnl = total_pnl
            trade.r_result = total_pnl / initial_risk if initial_risk > 0 else None
            trade.closed_at = closed_at
            journal = dict(trade.journal or {})
            journal["events"] = [*(journal.get("events") or []), event]
            trade.journal = journal

        profile = await self.db.get(PropRuleProfile, account.rule_profile_id)
        day = trading_date(closed_at, profile.daily_reset_timezone if profile else settings.application_timezone)
        daily = await self.db.scalar(select(AccountDailyStat).where(AccountDailyStat.account_id == account.id, AccountDailyStat.trading_date == day).with_for_update())
        if daily is None:
            daily = AccountDailyStat(account_id=account.id, trading_date=day, start_balance=account.current_balance, start_equity=account.current_equity, realized_pnl=Decimal("0"), floating_pnl=Decimal("0"), trades=0, wins=0, losses=0, consecutive_losses=0)
            self.db.add(daily)
        # AccountDailyStat is finalized on closure; partial lifecycle P&L is
        # held on Position/Trade metadata until the trade becomes complete.
        daily.realized_pnl = Decimal(daily.realized_pnl) + (Decimal("0") if was_completed else total_pnl)
        if not was_completed:
            daily.trades += 1
            if total_pnl > 0:
                daily.wins += 1
                daily.consecutive_losses = 0
            elif total_pnl < 0:
                daily.losses += 1
                daily.consecutive_losses += 1

        correlation_id = row.correlation_id or str(payload.get("correlation_id") or "reconciliation")
        self.db.add(PositionEvent(position_id=row.id, correlation_id=correlation_id, timestamp=closed_at, event="BROKER_POSITION_CLOSED", payload=event))
        self.db.add(AuditLog(timestamp=closed_at, correlation_id=correlation_id, user_id=None, action="BROKER_POSITION_CLOSED_RECONCILED", entity="position", entity_id=str(row.id), before={"status": "OPEN", "remaining_volume": str(remaining)}, after={"status": "CLOSED", "exit_price": str(exit_price), "pnl": str(total_pnl)}, metadata_json={"idempotent": True, "broker_reason": reason}))
        queue_notification(self.db, "INFO", "Position closed by broker", f"{account.name}: {row.direction.value} {row.symbol} closed at {exit_price}; reason={reason}; P&L={total_pnl}.", correlation_id)
        return True


class AggregateService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def run(self, payload: dict[str, object]) -> None:
        target_date = date.fromisoformat(str(payload["date"])) if payload.get("date") else trading_date(datetime.now(timezone.utc), settings.application_timezone)
        accounts = list((await self.db.scalars(select(TradingAccount))).all())
        for account in accounts:
            account_id = account.id
            query = select(
                func.coalesce(func.sum(Trade.pnl), 0),
                func.count(Trade.id),
                func.count(Trade.id).filter(Trade.pnl > 0),
                func.count(Trade.id).filter(Trade.pnl < 0),
            ).where(Trade.account_id == account_id, func.date(Trade.opened_at) == target_date)
            pnl, trades, wins, losses = (await self.db.execute(query)).one()
            row = await self.db.scalar(select(DailyPerformance).where(DailyPerformance.account_id == account_id, DailyPerformance.performance_date == target_date))
            if row is None:
                row = DailyPerformance(account_id=account_id, performance_date=target_date, pnl=Decimal(pnl), trades=trades, wins=wins, losses=losses, metrics={})
                self.db.add(row)
            else:
                row.pnl, row.trades, row.wins, row.losses = Decimal(pnl), trades, wins, losses
            if payload.get("notify_daily"):
                correlation_id = f"daily-report:{target_date.isoformat()}:{account_id}"
                existing = await self.db.scalar(
                    select(Notification.id).where(
                        Notification.correlation_id == correlation_id,
                        Notification.channel == "TELEGRAM",
                    ).limit(1)
                )
                if existing is None:
                    queue_notification(
                        self.db,
                        "INFO",
                        f"Daily SHADOW report — {target_date.isoformat()}",
                        f"{account.name}: trades={trades}, wins={wins}, losses={losses}, "
                        f"realized P&L={Decimal(pnl):.2f}, balance={Decimal(account.current_balance):.2f}, "
                        f"equity={Decimal(account.current_equity):.2f}.",
                        correlation_id,
                    )
        await self.db.commit()

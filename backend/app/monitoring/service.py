from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import Direction
from app.models.entities import AuditLog, DailyPerformance, Notification, Position, PositionEvent, PositionTarget, Trade, TradingAccount
from app.monitoring.recovery import reconcile_positions
from app.positions.manager import LifecycleRecorder, ManagedPosition, PositionManagementService
from app.positions.tp_allocation import TargetAllocation
from app.risk.drawdown import update_high_water
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
            rows = list((await self.db.scalars(select(Position).where(Position.account_id == account.id, Position.status == "OPEN").with_for_update(skip_locked=True))).all())
            broker = broker_registry.get(account)
            # SHADOW state is restored from durable position rows before monitoring.
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
            db_positions = [{"broker_position_id": row.broker_position_id, "remaining_volume": row.remaining_volume, "stop_loss": row.stop_loss, "take_profit": row.take_profit} for row in rows]
            for issue in reconcile_positions(db_positions, broker):
                matched = next((row for row in rows if row.broker_position_id == issue.broker_position_id), None)
                correlation_id = matched.correlation_id if matched else str(payload.get("correlation_id") or "reconciliation")
                self.db.add(AuditLog(timestamp=datetime.now(timezone.utc), correlation_id=correlation_id, user_id=None, action=f"RECONCILIATION_{issue.kind}", entity="position", entity_id=issue.broker_position_id, before=None, after={"detail": issue.detail}, metadata_json={"account_id": str(account.id)}))
                self.db.add(Notification(channel="DASHBOARD", correlation_id=correlation_id, severity="WARNING", subject=f"Reconciliation: {issue.kind}", body=f"Account {account.name}, position {issue.broker_position_id}: {issue.detail}", status="QUEUED"))
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
            account.current_equity = Decimal(account.current_balance) + floating
            high_water = update_high_water(Decimal(account.high_water_balance), Decimal(account.high_water_equity), Decimal(account.current_balance), Decimal(account.current_equity))
            account.high_water_balance, account.high_water_equity = high_water.balance, high_water.equity
            broker.balance, broker.equity = Decimal(account.current_balance), Decimal(account.current_equity)
        await self.db.commit()


class AggregateService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def run(self, payload: dict[str, object]) -> None:
        target_date = date.fromisoformat(str(payload["date"])) if payload.get("date") else datetime.now(timezone.utc).date()
        account_ids = list((await self.db.scalars(select(TradingAccount.id))).all())
        for account_id in account_ids:
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
        await self.db.commit()

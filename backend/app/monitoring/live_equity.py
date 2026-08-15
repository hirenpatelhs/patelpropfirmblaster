from datetime import datetime, time, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import AccountDailyStat, PropRuleProfile, Trade, TradingAccount
from app.risk.live_equity import LiveEquitySnapshot
from app.risk.trading_day import trading_date


def trading_day_bounds_utc(day, timezone_name: str) -> tuple[datetime, datetime]:
    zone = ZoneInfo(timezone_name)
    start = datetime.combine(day, time.min, tzinfo=zone)
    end = datetime.combine(day, time.max, tzinfo=zone)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


async def persist_demo_live_equity(
    db: AsyncSession,
    account: TradingAccount,
    profile: PropRuleProfile,
    snapshot: LiveEquitySnapshot,
    now: datetime | None = None,
) -> AccountDailyStat:
    now = now or datetime.now(timezone.utc)
    day = trading_date(now, profile.daily_reset_timezone)
    start, end = trading_day_bounds_utc(day, profile.daily_reset_timezone)
    realized, trades, wins, losses = (await db.execute(
        select(
            func.coalesce(func.sum(Trade.pnl), 0),
            func.count(Trade.id),
            func.count(Trade.id).filter(Trade.pnl > 0),
            func.count(Trade.id).filter(Trade.pnl < 0),
        ).where(Trade.account_id == account.id, Trade.closed_at >= start, Trade.closed_at <= end)
    )).one()
    row = await db.scalar(select(AccountDailyStat).where(
        AccountDailyStat.account_id == account.id,
        AccountDailyStat.trading_date == day,
    ).with_for_update())
    if row is None:
        row = AccountDailyStat(
            account_id=account.id, trading_date=day,
            start_balance=snapshot.balance, start_equity=snapshot.equity,
            realized_pnl=Decimal("0"), floating_pnl=Decimal("0"),
            trades=0, wins=0, losses=0, consecutive_losses=0,
        )
        db.add(row)
    row.realized_pnl = Decimal(realized)
    row.floating_pnl = snapshot.floating_pnl
    row.latest_balance = snapshot.balance
    row.latest_equity = snapshot.equity
    row.margin = snapshot.margin
    row.margin_level = snapshot.margin_level
    row.daily_loss = max(Decimal("0"), -(row.realized_pnl + row.floating_pnl))
    row.trades, row.wins, row.losses = int(trades), int(wins), int(losses)
    account.current_balance = snapshot.balance
    account.current_equity = snapshot.equity
    account.high_water_balance = max(Decimal(account.high_water_balance), snapshot.balance)
    account.high_water_equity = max(Decimal(account.high_water_equity), snapshot.equity)
    return row

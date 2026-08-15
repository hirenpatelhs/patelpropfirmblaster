import asyncio
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from app.core.enums import DrawdownType
from app.database.session import SessionLocal
from app.models.entities import PropFirm, PropRuleProfile


async def seed() -> None:
    async with SessionLocal() as db:
        if await db.scalar(select(PropFirm).where(PropFirm.name == "Example Prop Firm")):
            return
        firm = PropFirm(name="Example Prop Firm", website=None, notes="Demo only. Rules are not current and must be verified.", enabled=True, last_rules_reviewed_at=datetime.now(timezone.utc))
        db.add(firm)
        await db.flush()
        db.add(PropRuleProfile(firm_id=firm.id, name="50K Evaluation Demo — VERIFY BEFORE LIVE USE", enabled=True, starting_balance=Decimal("50000"), profit_target=Decimal("3000"), maximum_daily_loss=Decimal("2500"), maximum_drawdown=Decimal("3000"), maximum_drawdown_type=DrawdownType.STATIC, drawdown_floor=None, maximum_positions=2, maximum_lots=Decimal("2"), consistency_rule_enabled=False, consistency_percentage=None, news_trading_allowed=False, weekend_holding_allowed=False, overnight_holding_allowed=False, ea_allowed=False, signal_copying_allowed=False, third_party_signal_allowed=False, hedging_allowed=False, allowed_symbols=["XAUUSD"], restricted_symbols=[], daily_reset_timezone="UTC", rules={"demo": True}, rule_notes="DEMO RULES — VERIFY BEFORE LIVE USE"))
        await db.commit()


if __name__ == "__main__":
    asyncio.run(seed())

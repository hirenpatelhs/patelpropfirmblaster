import os
from decimal import Decimal
from urllib.parse import urlparse

import pytest
import pytest_asyncio
from redis.asyncio import Redis
from sqlalchemy import text


RUN_REAL = os.getenv("PPB_RUN_REAL_INTEGRATION") == "1"


def pytest_collection_modifyitems(items):
    if RUN_REAL:
        return
    marker = pytest.mark.skip(reason="set PPB_RUN_REAL_INTEGRATION=1 with isolated real test services")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(marker)


def assert_test_targets() -> None:
    database_url = os.getenv("DATABASE_URL", "")
    redis_url = os.getenv("REDIS_URL", "")
    database_name = urlparse(database_url.replace("postgresql+asyncpg", "postgresql")).path.lstrip("/").lower()
    redis_db = urlparse(redis_url).path.lstrip("/")
    if "test" not in database_name:
        raise RuntimeError("Integration DATABASE_URL must name a dedicated database containing 'test'")
    if not redis_db or redis_db == "0":
        raise RuntimeError("Integration REDIS_URL must use a nonzero dedicated Redis database")


@pytest.fixture(scope="session", autouse=True)
def guarded_test_targets():
    if RUN_REAL:
        assert_test_targets()


@pytest_asyncio.fixture
async def clean_infrastructure():
    if not RUN_REAL:
        yield
        return
    from app.database.base import Base
    from app.database.session import SessionLocal
    from app.core.config import settings
    table_names = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    async with SessionLocal() as db:
        await db.execute(text(f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE"))
        await db.commit()
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    await redis.flushdb()
    await redis.aclose()
    yield
    async with SessionLocal() as db:
        await db.execute(text(f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE"))
        await db.commit()
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    await redis.flushdb()
    await redis.aclose()


@pytest_asyncio.fixture
async def seeded_shadow(clean_infrastructure):
    from app.core.enums import AccountStage, AccountStatus, DrawdownType, RiskMode, TradingMode
    from app.database.session import SessionLocal
    from app.models.entities import AccountSetting, PropFirm, PropRuleProfile, TelegramSource, TradingAccount
    async with SessionLocal() as db:
        firm = PropFirm(name="INTEGRATION TEST FIRM", website=None, notes="TEST ONLY", enabled=True)
        db.add(firm)
        await db.flush()
        profile = PropRuleProfile(
            firm_id=firm.id, name="INTEGRATION SHADOW", enabled=True, starting_balance=Decimal("50000"), profit_target=Decimal("3000"),
            maximum_daily_loss=Decimal("2500"), maximum_drawdown=Decimal("3000"), maximum_drawdown_type=DrawdownType.STATIC,
            drawdown_floor=None, maximum_positions=3, maximum_lots=Decimal("5"), consistency_rule_enabled=False, consistency_percentage=None,
            news_trading_allowed=True, weekend_holding_allowed=True, overnight_holding_allowed=True, ea_allowed=True,
            signal_copying_allowed=True, third_party_signal_allowed=True, hedging_allowed=False, allowed_symbols=["XAUUSD"],
            restricted_symbols=[], daily_reset_timezone="UTC", rules={"integration": True}, rule_notes="TEST ONLY",
        )
        db.add(profile)
        await db.flush()
        account = TradingAccount(
            name="INTEGRATION SHADOW ACCOUNT", prop_firm_id=firm.id, rule_profile_id=profile.id, account_number="TEST-1", platform="MOCK",
            broker_server=None, account_currency="USD", initial_balance=Decimal("50000"), current_balance=Decimal("50000"),
            current_equity=Decimal("50000"), high_water_balance=Decimal("50000"), high_water_equity=Decimal("50000"),
            stage=AccountStage.SIMULATION, status=AccountStatus.ACTIVE, trading_mode=TradingMode.SHADOW, risk_mode=RiskMode.CUSTOM,
            credentials_encrypted=None,
        )
        db.add(account)
        await db.flush()
        db.add(AccountSetting(account_id=account.id, maximum_daily_loss_internal=Decimal("2000")))
        source = TelegramSource(name="INTEGRATION GURU", telegram_chat_id="-100999", telegram_channel_name=None, enabled=True, priority=1, parser_profile={}, allowed_account_ids=[str(account.id)], notes="TEST ONLY")
        db.add(source)
        await db.commit()
        return {"account_id": account.id, "source_id": source.id, "chat_id": source.telegram_chat_id}

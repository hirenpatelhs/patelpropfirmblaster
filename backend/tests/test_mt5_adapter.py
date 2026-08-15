from collections import namedtuple
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from app.brokers.base import OrderRequest
from app.brokers.mt5 import MetaTrader5Adapter
from app.core.enums import Confidence, Direction, OrderType
from app.execution.demo import DemoAccount, DemoTradingEngine
from app.schemas.signal import NormalizedSignal


Position = namedtuple("Position", "ticket symbol volume type")
Info = namedtuple("Info", "trade_tick_size trade_tick_value_loss trade_tick_value volume_min volume_max volume_step trade_contract_size")
Tick = namedtuple("Tick", "bid ask")
Result = namedtuple("Result", "retcode order price comment")


class FakeMT5:
    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_SLTP = 2
    ORDER_TYPE_BUY = 3
    ORDER_TYPE_SELL = 4
    POSITION_TYPE_BUY = 3
    ORDER_TIME_GTC = 5
    ORDER_FILLING_IOC = 6
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_PLACED = 10008
    ACCOUNT_TRADE_MODE_DEMO = 0

    def __init__(self) -> None:
        self.sent = None
        self.connected = True
        self.initialize_calls = 0
        self.initialize_options = None
        self.send_count = 0
        self.positions = [Position(42, "XAUUSD", 0.2, self.POSITION_TYPE_BUY)]

    def initialize(self, **kwargs): self.initialize_calls += 1; self.initialize_options = kwargs; self.connected = True; return True
    def shutdown(self): self.connected = False
    def terminal_info(self): return self.connected
    def account_info(self): return SimpleNamespace(login=1, server="server", trade_mode=self.ACCOUNT_TRADE_MODE_DEMO) if self.connected else None
    def positions_get(self, ticket=None): return [p for p in self.positions if ticket is None or p.ticket == ticket]
    def orders_get(self): return []
    def symbol_info(self, symbol): return Info(0.01, 1, 1, 0.01, 100, 0.01, 100)
    def symbol_info_tick(self, symbol): return Tick(100, 100.2)
    def order_check(self, payload): return Result(0, 0, 0, "ok")
    def order_send(self, payload): self.sent = payload; self.send_count += 1; return Result(self.TRADE_RETCODE_DONE, 42, payload["price"], "done")


def order_request(key: str = "same-key") -> OrderRequest:
    return OrderRequest(key, "XAUUSD", Direction.BUY, OrderType.MARKET, Decimal("0.01"), Decimal("99"), Decimal("102"))


def test_mt5_partial_close_sends_opposite_side_deal_for_exact_volume() -> None:
    adapter = MetaTrader5Adapter(Path("terminal"), 1, "server", "password", 99)
    adapter.mt5 = FakeMT5()
    result = adapter.partial_close("42", volume=Decimal("0.05"))
    assert result.accepted and result.fill_price == Decimal("100.0")
    assert adapter.mt5.sent["position"] == 42
    assert adapter.mt5.sent["type"] == adapter.mt5.ORDER_TYPE_SELL
    assert adapter.mt5.sent["volume"] == 0.05


def test_mt5_reconnects_before_placing_order() -> None:
    adapter = MetaTrader5Adapter(Path("terminal"), 1, "server", "password", 99, reconnect_attempts=2, reconnect_backoff_seconds=0)
    fake = FakeMT5()
    fake.connected = False
    fake.positions = []
    adapter.mt5 = fake
    result = adapter.place_order(order_request())
    assert result.accepted
    assert fake.initialize_calls == 1
    assert fake.send_count == 1
    assert adapter.healthy


def test_mt5_can_reuse_authenticated_terminal_session_without_password() -> None:
    adapter = MetaTrader5Adapter(Path("terminal"), 1, "server", None, 99, reconnect_attempts=1, reconnect_backoff_seconds=0, require_demo=True)
    fake = FakeMT5()
    fake.connected = False
    fake.positions = []
    adapter.mt5 = fake
    assert adapter._ensure_connected()
    assert fake.initialize_options == {"path": "terminal"}


def test_mt5_duplicate_comment_returns_existing_position_without_resend() -> None:
    adapter = MetaTrader5Adapter(Path("terminal"), 1, "server", "password", 99, reconnect_backoff_seconds=0)
    fake = FakeMT5()
    comment = adapter._execution_comment("same-key")
    fake.positions = [SimpleNamespace(ticket=77, symbol="XAUUSD", volume=0.01, type=fake.POSITION_TYPE_BUY, magic=99, comment=comment, price_open=100.2, sl=99, tp=102)]
    adapter.mt5 = fake
    result = adapter.place_order(order_request())
    assert result.accepted
    assert result.code == "ALREADY_ACCEPTED"
    assert result.broker_order_id == "77"
    assert fake.send_count == 0


def test_mt5_open_position_query_filters_magic_and_ppb_comment() -> None:
    adapter = MetaTrader5Adapter(Path("terminal"), 1, "server", "password", 99)
    fake = FakeMT5()
    fake.positions = [
        SimpleNamespace(ticket=1, symbol="XAUUSD", volume=0.05, type=fake.POSITION_TYPE_BUY, magic=99, comment="PPB:owned", sl=99, tp=104, _asdict=lambda: {}),
        SimpleNamespace(ticket=2, symbol="XAUUSD", volume=0.05, type=fake.POSITION_TYPE_BUY, magic=100, comment="PPB:other", sl=99, tp=104, _asdict=lambda: {}),
        SimpleNamespace(ticket=3, symbol="XAUUSD", volume=0.05, type=fake.POSITION_TYPE_BUY, magic=99, comment="manual", sl=99, tp=104, _asdict=lambda: {}),
    ]
    adapter.mt5 = fake
    assert [row["id"] for row in adapter.get_open_positions()] == ["1"]


def test_mt5_closed_position_uses_latest_exit_deal_history() -> None:
    adapter = MetaTrader5Adapter(Path("terminal"), 1, "server", "password", 99)
    fake = FakeMT5()
    fake.DEAL_ENTRY_OUT = 1
    fake.DEAL_ENTRY_OUT_BY = 2
    fake.DEAL_REASON_TP = 10
    older = SimpleNamespace(entry=1, magic=99, time_msc=1000, time=1, price=103, volume=0.02, profit=3, commission=-0.1, swap=0, fee=0, reason=10, ticket=50)
    latest = SimpleNamespace(entry=1, magic=99, time_msc=2000, time=2, price=104, volume=0.02, profit=4, commission=-0.1, swap=0, fee=0, reason=10, ticket=51)
    fake.history_deals_get = lambda position: [older, latest]
    adapter.mt5 = fake
    result = adapter.get_closed_position("42")
    assert result is not None
    assert result["price"] == Decimal("104") and result["profit"] == Decimal("3.9")
    assert result["reason"] == "TP" and result["deal_ticket"] == "51"


def test_demo_locked_adapter_rejects_non_demo_login() -> None:
    adapter = MetaTrader5Adapter(Path("terminal"), 1, "server", "password", 99, require_demo=True, reconnect_attempts=1, reconnect_backoff_seconds=0)
    fake = FakeMT5()
    fake.account_info = lambda: SimpleNamespace(login=1, server="server", trade_mode=1) if fake.connected else None
    adapter.mt5 = fake
    assert not adapter._ensure_connected()
    assert not adapter.healthy
    assert adapter.last_error == "Configured account is not an MT5 demo account"


def test_demo_engine_clamps_real_adapter_order_to_hard_volume_cap() -> None:
    adapter = MetaTrader5Adapter(Path("terminal"), 1, "server", "password", 99, require_demo=True, reconnect_backoff_seconds=0)
    fake = FakeMT5()
    fake.positions = []
    adapter.mt5 = fake
    signal = NormalizedSignal(
        source_id=uuid4(), telegram_message_id=10, symbol="XAUUSD", direction=Direction.BUY,
        stop_loss=Decimal("99"), take_profits=[Decimal("102")], timestamp=datetime.now(timezone.utc),
        confidence=Confidence.HIGH, raw_text="XAUUSD BUY SL 99 TP 102",
    )
    account = DemoAccount(
        account_id=str(uuid4()), broker=adapter, equity=Decimal("50000"), automation_permitted=True,
        base_risk_percent=Decimal("0.01"), maximum_risk=Decimal("500"), daily_buffer=Decimal("1000"),
        overall_buffer=Decimal("4000"), maximum_total_exposure=Decimal("1000"), current_total_exposure=Decimal("0"),
        max_volume=Decimal("0.01"),
    )
    run = DemoTradingEngine().route(signal, [account])
    assert run.decisions[0].decision.value == "APPROVED"
    assert run.decisions[0].calculated_size == Decimal("0.01")
    assert fake.sent["volume"] == 0.01

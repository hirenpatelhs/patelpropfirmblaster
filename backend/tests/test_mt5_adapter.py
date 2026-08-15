from collections import namedtuple
from decimal import Decimal
from pathlib import Path

from app.brokers.mt5 import MetaTrader5Adapter


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

    def __init__(self) -> None:
        self.sent = None

    def terminal_info(self): return True
    def account_info(self): return True
    def positions_get(self, ticket=None): return [Position(ticket or 42, "XAUUSD", 0.2, self.POSITION_TYPE_BUY)]
    def symbol_info(self, symbol): return Info(0.01, 1, 1, 0.01, 100, 0.01, 100)
    def symbol_info_tick(self, symbol): return Tick(100, 100.2)
    def order_check(self, payload): return Result(0, 0, 0, "ok")
    def order_send(self, payload): self.sent = payload; return Result(self.TRADE_RETCODE_DONE, 42, payload["price"], "done")


def test_mt5_partial_close_sends_opposite_side_deal_for_exact_volume() -> None:
    adapter = MetaTrader5Adapter(Path("terminal"), 1, "server", "password", 99)
    adapter.mt5 = FakeMT5()
    result = adapter.partial_close("42", volume=Decimal("0.05"))
    assert result.accepted and result.fill_price == Decimal("100.0")
    assert adapter.mt5.sent["position"] == 42
    assert adapter.mt5.sent["type"] == adapter.mt5.ORDER_TYPE_SELL
    assert adapter.mt5.sent["volume"] == 0.05

from decimal import Decimal
from threading import Lock
from typing import Any
from uuid import uuid4

from app.brokers.base import BrokerAdapter, BrokerResult, OrderRequest
from app.core.enums import Direction
from app.schemas.risk import InstrumentSpec


class MockBrokerAdapter(BrokerAdapter):
    def __init__(self, balance: Decimal = Decimal("50000")) -> None:
        self.connected = False
        self.balance = balance
        self.equity = balance
        self.prices = {"XAUUSD": Decimal("3344.00"), "NAS100": Decimal("21500.0"), "US30": Decimal("44000.0")}
        self.spreads = {"XAUUSD": Decimal("0.20"), "NAS100": Decimal("1.0"), "US30": Decimal("2.0")}
        self.positions: dict[str, dict[str, Any]] = {}
        self.executions: dict[str, BrokerResult] = {}
        self._lock = Lock()

    def connect(self) -> bool:
        self.connected = True
        return True

    def disconnect(self) -> None:
        self.connected = False

    def health_check(self) -> bool:
        return self.connected

    def get_account_info(self) -> dict[str, Any]:
        return {"balance": self.balance, "equity": self.equity, "currency": "USD", "connected": self.connected}

    def get_symbol_info(self, symbol: str) -> InstrumentSpec | None:
        if symbol not in self.prices:
            return None
        return InstrumentSpec(
            symbol=symbol,
            tick_size=Decimal("0.01"),
            tick_value=Decimal("1.00"),
            volume_min=Decimal("0.01"),
            volume_max=Decimal("100"),
            volume_step=Decimal("0.01"),
            contract_size=Decimal("100"),
        )

    def get_price(self, symbol: str) -> Decimal | None:
        return self.prices.get(symbol)

    def get_bid(self, symbol: str) -> Decimal | None:
        return self.prices.get(symbol)

    def get_ask(self, symbol: str) -> Decimal | None:
        bid = self.prices.get(symbol)
        return bid + self.spreads.get(symbol, Decimal("0")) if bid is not None else None

    def set_price(self, symbol: str, bid: Decimal, ask: Decimal | None = None) -> None:
        self.prices[symbol] = bid
        self.spreads[symbol] = (ask - bid) if ask is not None else self.spreads.get(symbol, Decimal("0"))

    def place_order(self, request: OrderRequest) -> BrokerResult:
        with self._lock:
            if request.idempotency_key in self.executions:
                return self.executions[request.idempotency_key]
            if not self.connected:
                return BrokerResult(False, None, None, "DISCONNECTED", "Mock broker is disconnected", {})
            market_price = self.get_execution_price(request.symbol, request.direction)
            if market_price is None:
                return BrokerResult(False, None, None, "UNKNOWN_SYMBOL", "Symbol is unavailable", {})
            position_id = str(uuid4())
            self.positions[position_id] = {
                "id": position_id,
                "symbol": request.symbol,
                "direction": request.direction.value,
                "size": request.size,
                "original_size": request.size,
                "entry_price": market_price,
                "stop_loss": request.stop_loss,
                "take_profit": request.take_profit,
                "idempotency_key": request.idempotency_key,
            }
            result = BrokerResult(True, position_id, market_price, "FILLED", "Simulated fill", {"virtual": True})
            self.executions[request.idempotency_key] = result
            return result

    def modify_stop_loss(self, position_id: str, stop_loss: Decimal) -> BrokerResult:
        position = self.positions.get(position_id)
        if not position:
            return BrokerResult(False, None, None, "NOT_FOUND", "Position not found", {})
        old = position["stop_loss"]
        is_buy = position["direction"] == "BUY"
        if (is_buy and stop_loss < old) or (not is_buy and stop_loss > old):
            return BrokerResult(False, position_id, None, "WORSENS_STOP", "Stop modification would increase risk", {})
        position["stop_loss"] = stop_loss
        return BrokerResult(True, position_id, None, "MODIFIED", "Stop modified", {})

    def partial_close(
        self,
        position_id: str,
        percentage: Decimal | None = None,
        *,
        volume: Decimal | None = None,
    ) -> BrokerResult:
        position = self.positions.get(position_id)
        if not position:
            return BrokerResult(False, position_id, None, "NOT_FOUND", "Position not found", {})
        close_volume = volume if volume is not None else position["size"] * (percentage or Decimal("0"))
        spec = self.get_symbol_info(position["symbol"])
        valid_step = bool(spec and close_volume % spec.volume_step == 0)
        if close_volume <= 0 or close_volume >= position["size"] or not valid_step:
            return BrokerResult(False, position_id, None, "INVALID_PARTIAL", "Invalid partial close", {})
        position["size"] -= close_volume
        direction = Direction(position["direction"])
        price = self.get_exit_price(position["symbol"], direction)
        return BrokerResult(True, position_id, price, "PARTIAL", "Partial close simulated", {"closed_volume": str(close_volume), "remaining_volume": str(position["size"])})

    def close_position(self, position_id: str) -> BrokerResult:
        position = self.positions.pop(position_id, None)
        if not position:
            return BrokerResult(False, position_id, None, "NOT_FOUND", "Position not found", {})
        direction = Direction(position["direction"])
        return BrokerResult(True, position_id, self.get_exit_price(position["symbol"], direction), "CLOSED", "Position closed", {"closed_volume": str(position["size"])})

    def get_open_positions(self) -> list[dict[str, Any]]:
        return list(self.positions.values())

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.core.enums import Direction, OrderType
from app.schemas.risk import InstrumentSpec


@dataclass(frozen=True)
class OrderRequest:
    idempotency_key: str
    symbol: str
    direction: Direction
    order_type: OrderType
    size: Decimal
    stop_loss: Decimal
    take_profit: Decimal | None
    entry_price: Decimal | None = None
    max_slippage_points: int = 30


@dataclass(frozen=True)
class BrokerResult:
    accepted: bool
    broker_order_id: str | None
    fill_price: Decimal | None
    code: str
    message: str
    raw: dict[str, Any]


class BrokerAdapter(ABC):
    @abstractmethod
    def connect(self) -> bool: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def health_check(self) -> bool: ...

    @abstractmethod
    def get_account_info(self) -> dict[str, Any]: ...

    @abstractmethod
    def get_symbol_info(self, symbol: str) -> InstrumentSpec | None: ...

    @abstractmethod
    def get_price(self, symbol: str) -> Decimal | None: ...

    def get_bid(self, symbol: str) -> Decimal | None:
        """Return the executable sell/long-exit price.

        Adapters that expose a spread should override this method. Keeping the
        midpoint-compatible fallback makes small test adapters source compatible.
        """
        return self.get_price(symbol)

    def get_ask(self, symbol: str) -> Decimal | None:
        """Return the executable buy/short-exit price."""
        return self.get_price(symbol)

    def get_execution_price(self, symbol: str, direction: Direction) -> Decimal | None:
        return self.get_ask(symbol) if direction == Direction.BUY else self.get_bid(symbol)

    def get_exit_price(self, symbol: str, direction: Direction) -> Decimal | None:
        return self.get_bid(symbol) if direction == Direction.BUY else self.get_ask(symbol)

    @abstractmethod
    def place_order(self, request: OrderRequest) -> BrokerResult: ...

    @abstractmethod
    def modify_stop_loss(self, position_id: str, stop_loss: Decimal) -> BrokerResult: ...

    @abstractmethod
    def partial_close(
        self,
        position_id: str,
        percentage: Decimal | None = None,
        *,
        volume: Decimal | None = None,
    ) -> BrokerResult: ...

    @abstractmethod
    def close_position(self, position_id: str) -> BrokerResult: ...

    @abstractmethod
    def get_open_positions(self) -> list[dict[str, Any]]: ...

    def get_account_positions(self) -> list[dict[str, Any]]:
        """Return every account position for equity risk, including non-PPB exposure."""
        return self.get_open_positions()

    def get_closed_position(self, position_id: str) -> dict[str, Any] | None:
        """Return the latest broker-side closing fill when the adapter supports history."""
        return None

    def expected_position_comment(self, idempotency_key: str) -> str | None:
        """Return the adapter ownership comment for an execution key, if used."""
        return None

    def get_pending_orders(self) -> list[dict[str, Any]]:
        return []

from decimal import Decimal
from pathlib import Path
from typing import Any

from app.brokers.base import BrokerAdapter, BrokerResult, OrderRequest
from app.core.enums import Direction, OrderType
from app.schemas.risk import InstrumentSpec


class MetaTrader5Adapter(BrokerAdapter):
    """Thin deterministic adapter. Import is delayed so non-Windows tests do not require MT5."""

    def __init__(self, terminal_path: Path, login: int, server: str, password: str, magic_number: int) -> None:
        self.terminal_path = terminal_path
        self.login = login
        self.server = server
        self.password = password
        self.magic_number = magic_number
        self.mt5: Any = None

    def connect(self) -> bool:
        try:
            import MetaTrader5 as mt5  # type: ignore[import-not-found]
        except ImportError:
            return False
        self.mt5 = mt5
        return bool(mt5.initialize(path=str(self.terminal_path), login=self.login, server=self.server, password=self.password))

    def disconnect(self) -> None:
        if self.mt5:
            self.mt5.shutdown()

    def health_check(self) -> bool:
        return bool(self.mt5 and self.mt5.terminal_info() and self.mt5.account_info())

    def get_account_info(self) -> dict[str, Any]:
        info = self.mt5.account_info() if self.mt5 else None
        return info._asdict() if info else {}

    def get_symbol_info(self, symbol: str) -> InstrumentSpec | None:
        info = self.mt5.symbol_info(symbol) if self.mt5 else None
        if not info:
            return None
        return InstrumentSpec(
            symbol=symbol,
            tick_size=Decimal(str(info.trade_tick_size)),
            tick_value=Decimal(str(info.trade_tick_value_loss or info.trade_tick_value)),
            volume_min=Decimal(str(info.volume_min)),
            volume_max=Decimal(str(info.volume_max)),
            volume_step=Decimal(str(info.volume_step)),
            contract_size=Decimal(str(info.trade_contract_size)),
        )

    def get_price(self, symbol: str) -> Decimal | None:
        return self.get_ask(symbol)

    def get_bid(self, symbol: str) -> Decimal | None:
        tick = self.mt5.symbol_info_tick(symbol) if self.mt5 else None
        return Decimal(str(tick.bid)) if tick else None

    def get_ask(self, symbol: str) -> Decimal | None:
        tick = self.mt5.symbol_info_tick(symbol) if self.mt5 else None
        return Decimal(str(tick.ask)) if tick else None

    def place_order(self, request: OrderRequest) -> BrokerResult:
        if not self.health_check():
            return BrokerResult(False, None, None, "DISCONNECTED", "MT5 is disconnected", {})
        tick = self.mt5.symbol_info_tick(request.symbol)
        if not tick:
            return BrokerResult(False, None, None, "NO_PRICE", "Current price unavailable", {})
        price = Decimal(str(tick.ask if request.direction == Direction.BUY else tick.bid))
        action = self.mt5.TRADE_ACTION_DEAL if request.order_type == OrderType.MARKET else self.mt5.TRADE_ACTION_PENDING
        order_type = self.mt5.ORDER_TYPE_BUY if request.direction == Direction.BUY else self.mt5.ORDER_TYPE_SELL
        payload = {
            "action": action,
            "symbol": request.symbol,
            "volume": float(request.size),
            "type": order_type,
            "price": float(price),
            "sl": float(request.stop_loss),
            "tp": float(request.take_profit or 0),
            "deviation": request.max_slippage_points,
            "magic": self.magic_number,
            "comment": f"PPB:{request.idempotency_key[:20]}",
            "type_time": self.mt5.ORDER_TIME_GTC,
            "type_filling": self.mt5.ORDER_FILLING_IOC,
        }
        check = self.mt5.order_check(payload)
        if not check or check.retcode != 0:
            raw = check._asdict() if check else {}
            return BrokerResult(False, None, None, str(raw.get("retcode", "CHECK_FAILED")), "MT5 order check failed", raw)
        result = self.mt5.order_send(payload)
        raw = result._asdict() if result else {}
        accepted = bool(result and result.retcode in {self.mt5.TRADE_RETCODE_DONE, self.mt5.TRADE_RETCODE_PLACED})
        return BrokerResult(accepted, str(result.order) if accepted else None, Decimal(str(result.price)) if accepted else None, str(raw.get("retcode")), raw.get("comment", ""), raw)

    def modify_stop_loss(self, position_id: str, stop_loss: Decimal) -> BrokerResult:
        position = self._get_position(position_id)
        if position is None:
            return BrokerResult(False, position_id, None, "NOT_FOUND", "Position not found", {})
        return self._position_request(position_id, self.mt5.TRADE_ACTION_SLTP, {"symbol": position.symbol, "sl": float(stop_loss), "tp": float(getattr(position, "tp", 0) or 0)})

    def partial_close(
        self,
        position_id: str,
        percentage: Decimal | None = None,
        *,
        volume: Decimal | None = None,
    ) -> BrokerResult:
        position = self._get_position(position_id)
        if position is None:
            return BrokerResult(False, position_id, None, "NOT_FOUND", "Position not found", {})
        current_volume = Decimal(str(position.volume))
        close_volume = volume if volume is not None else current_volume * (percentage or Decimal("0"))
        if close_volume <= 0 or close_volume >= current_volume:
            return BrokerResult(False, position_id, None, "INVALID_PARTIAL", "Partial volume must be less than remaining volume", {})
        return self._close_deal(position, close_volume)

    def close_position(self, position_id: str) -> BrokerResult:
        position = self._get_position(position_id)
        if position is None:
            return BrokerResult(False, position_id, None, "NOT_FOUND", "Position not found", {})
        return self._close_deal(position, Decimal(str(position.volume)))

    def get_open_positions(self) -> list[dict[str, Any]]:
        positions = self.mt5.positions_get() if self.mt5 else None
        return [p._asdict() for p in positions] if positions else []

    def get_pending_orders(self) -> list[dict[str, Any]]:
        orders = self.mt5.orders_get() if self.mt5 else None
        return [order._asdict() for order in orders] if orders else []

    def _get_position(self, position_id: str) -> Any | None:
        if not self.health_check():
            return None
        positions = self.mt5.positions_get(ticket=int(position_id))
        return positions[0] if positions else None

    def _close_deal(self, position: Any, volume: Decimal) -> BrokerResult:
        spec = self.get_symbol_info(position.symbol)
        if spec is None:
            return BrokerResult(False, str(position.ticket), None, "NO_SYMBOL_SPEC", "Symbol specification unavailable", {})
        units = volume / spec.volume_step
        if units != units.to_integral_value() or volume < spec.volume_min or volume > Decimal(str(position.volume)):
            return BrokerResult(False, str(position.ticket), None, "INVALID_VOLUME", "Close volume violates broker limits", {})
        is_buy = position.type == self.mt5.POSITION_TYPE_BUY
        price = self.get_bid(position.symbol) if is_buy else self.get_ask(position.symbol)
        if price is None:
            return BrokerResult(False, str(position.ticket), None, "NO_PRICE", "Current exit price unavailable", {})
        payload = {
            "action": self.mt5.TRADE_ACTION_DEAL,
            "position": int(position.ticket),
            "symbol": position.symbol,
            "volume": float(volume),
            "type": self.mt5.ORDER_TYPE_SELL if is_buy else self.mt5.ORDER_TYPE_BUY,
            "price": float(price),
            "deviation": 30,
            "magic": self.magic_number,
            "comment": "PPB:position-close",
            "type_time": self.mt5.ORDER_TIME_GTC,
            "type_filling": self.mt5.ORDER_FILLING_IOC,
        }
        check = self.mt5.order_check(payload)
        if not check or check.retcode != 0:
            raw = check._asdict() if check else {}
            return BrokerResult(False, str(position.ticket), None, str(raw.get("retcode", "CHECK_FAILED")), "MT5 close check failed", raw)
        result = self.mt5.order_send(payload)
        raw = result._asdict() if result else {}
        accepted_codes = {self.mt5.TRADE_RETCODE_DONE}
        if hasattr(self.mt5, "TRADE_RETCODE_DONE_PARTIAL"):
            accepted_codes.add(self.mt5.TRADE_RETCODE_DONE_PARTIAL)
        accepted = bool(result and result.retcode in accepted_codes)
        fill = Decimal(str(result.price)) if accepted else None
        return BrokerResult(accepted, str(position.ticket), fill, str(raw.get("retcode")), raw.get("comment", ""), raw)

    def _position_request(self, position_id: str, action: int, extra: dict[str, Any]) -> BrokerResult:
        if not self.health_check():
            return BrokerResult(False, position_id, None, "DISCONNECTED", "MT5 is disconnected", {})
        payload = {"action": action, "position": int(position_id), **extra}
        result = self.mt5.order_send(payload)
        raw = result._asdict() if result else {}
        accepted = bool(result and result.retcode == self.mt5.TRADE_RETCODE_DONE)
        return BrokerResult(accepted, position_id, None, str(raw.get("retcode")), raw.get("comment", ""), raw)

import hashlib
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from threading import RLock
from typing import Any

from app.brokers.base import BrokerAdapter, BrokerResult, OrderRequest
from app.core.enums import Direction, OrderType
from app.schemas.risk import InstrumentSpec


class MetaTrader5Adapter(BrokerAdapter):
    """Thin deterministic adapter. Import is delayed so non-Windows tests do not require MT5."""

    def __init__(
        self,
        terminal_path: Path,
        login: int,
        server: str,
        password: str | None,
        magic_number: int,
        *,
        reconnect_attempts: int = 3,
        reconnect_backoff_seconds: float = 0.25,
        require_demo: bool = False,
    ) -> None:
        self.terminal_path = terminal_path
        self.login = login
        self.server = server
        self.password = password
        self.magic_number = magic_number
        self.reconnect_attempts = max(1, reconnect_attempts)
        self.reconnect_backoff_seconds = max(0.0, reconnect_backoff_seconds)
        self.require_demo = require_demo
        self.mt5: Any = None
        self.healthy = False
        self.last_error: str | None = None
        self._order_lock = RLock()

    def connect(self) -> bool:
        try:
            import MetaTrader5 as mt5  # type: ignore[import-not-found]
        except ImportError:
            self.last_error = "MetaTrader5 package is unavailable"
            self.healthy = False
            return False
        self.mt5 = mt5
        return self._initialize_once()

    def disconnect(self) -> None:
        if self.mt5:
            self.mt5.shutdown()
        self.healthy = False

    def health_check(self) -> bool:
        try:
            connected = bool(self.mt5 and self.mt5.terminal_info() and self.mt5.account_info())
        except Exception:
            connected = False
        self.healthy = connected and self._account_mode_allowed()
        return self.healthy

    def _account_mode_allowed(self) -> bool:
        if not self.mt5:
            return False
        info = self.mt5.account_info()
        if info is None:
            self.last_error = "MT5 account information is unavailable"
            return False
        if int(getattr(info, "login", 0)) != self.login:
            self.last_error = "MT5 terminal is authenticated to a different account"
            return False
        if str(getattr(info, "server", "")) != self.server:
            self.last_error = "MT5 terminal is authenticated to a different broker server"
            return False
        if not self.require_demo:
            return True
        expected = getattr(self.mt5, "ACCOUNT_TRADE_MODE_DEMO", None)
        if expected is None or getattr(info, "trade_mode", None) != expected:
            self.last_error = "Configured account is not an MT5 demo account"
            return False
        return True

    def _initialize_once(self) -> bool:
        if not self.mt5:
            self.healthy = False
            return False
        try:
            initialize_options: dict[str, object] = {"path": str(self.terminal_path)}
            if self.password is not None:
                initialize_options.update({"login": self.login, "server": self.server, "password": self.password})
            initialized = bool(self.mt5.initialize(**initialize_options))
        except Exception as exc:
            self.last_error = f"MT5 initialize failed: {exc.__class__.__name__}"
            self.healthy = False
            return False
        if not initialized:
            self.last_error = "MT5 initialize returned false"
            self.healthy = False
            return False
        if not self._account_mode_allowed():
            self.healthy = False
            self.mt5.shutdown()
            return False
        self.last_error = None
        self.healthy = True
        return True

    def _ensure_connected(self) -> bool:
        if self.health_check():
            return True
        self.healthy = False
        for attempt in range(self.reconnect_attempts):
            if self.mt5 is None:
                try:
                    import MetaTrader5 as mt5  # type: ignore[import-not-found]
                except ImportError:
                    self.last_error = "MetaTrader5 package is unavailable"
                    return False
                self.mt5 = mt5
            else:
                try:
                    self.mt5.shutdown()
                except Exception:
                    pass
            if self._initialize_once():
                return True
            if attempt + 1 < self.reconnect_attempts and self.reconnect_backoff_seconds:
                time.sleep(self.reconnect_backoff_seconds * (attempt + 1))
        self.healthy = False
        return False

    def _ensure_symbol(self, symbol: str) -> bool:
        if not self._ensure_connected():
            return False
        info = self.mt5.symbol_info(symbol)
        if info is None:
            return False
        if not getattr(info, "visible", True) and hasattr(self.mt5, "symbol_select"):
            return bool(self.mt5.symbol_select(symbol, True))
        return True

    def get_account_info(self) -> dict[str, Any]:
        info = self.mt5.account_info() if self._ensure_connected() else None
        return info._asdict() if info else {}

    def get_symbol_info(self, symbol: str) -> InstrumentSpec | None:
        if not self._ensure_symbol(symbol):
            return None
        info = self.mt5.symbol_info(symbol)
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
        tick = self.mt5.symbol_info_tick(symbol) if self._ensure_symbol(symbol) else None
        return Decimal(str(tick.bid)) if tick else None

    def get_ask(self, symbol: str) -> Decimal | None:
        tick = self.mt5.symbol_info_tick(symbol) if self._ensure_symbol(symbol) else None
        return Decimal(str(tick.ask)) if tick else None

    def place_order(self, request: OrderRequest) -> BrokerResult:
        with self._order_lock:
            if not self._ensure_symbol(request.symbol):
                return BrokerResult(False, None, None, "DISCONNECTED", self.last_error or "MT5 is disconnected", {})
            duplicate = self._existing_execution(request.idempotency_key)
            if duplicate is not None:
                return duplicate
            return self._place_order_once(request, allow_reconnect=True)

    @staticmethod
    def _execution_comment(idempotency_key: str) -> str:
        digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:20]
        return f"PPB:{digest}"

    def expected_position_comment(self, idempotency_key: str) -> str:
        return self._execution_comment(idempotency_key)

    def _existing_execution(self, idempotency_key: str) -> BrokerResult | None:
        if not self._ensure_connected():
            return None
        comments = {self._execution_comment(idempotency_key), f"PPB:{idempotency_key[:20]}"}
        collections: list[tuple[str, list[Any]]] = []
        for kind, getter_name in (("POSITION", "positions_get"), ("ORDER", "orders_get")):
            getter = getattr(self.mt5, getter_name, None)
            try:
                values = list(getter() or []) if getter else []
            except Exception:
                values = []
            collections.append((kind, values))
        for kind, values in collections:
            for item in values:
                magic = getattr(item, "magic", self.magic_number)
                if magic != self.magic_number or getattr(item, "comment", None) not in comments:
                    continue
                ticket = getattr(item, "ticket", None) or getattr(item, "order", None)
                price = getattr(item, "price_open", None) or getattr(item, "price_current", None)
                return BrokerResult(
                    True,
                    str(ticket) if ticket is not None else None,
                    Decimal(str(price)) if price not in {None, 0} else None,
                    "ALREADY_ACCEPTED",
                    "MT5 execution already exists for this idempotency key",
                    {"deduplicated": True, "kind": kind, "comment": getattr(item, "comment", None)},
                )
        return None

    def _place_order_once(self, request: OrderRequest, *, allow_reconnect: bool) -> BrokerResult:
        tick = self.mt5.symbol_info_tick(request.symbol)
        if not tick:
            return BrokerResult(False, None, None, "NO_PRICE", "Current price unavailable", {})
        price = Decimal(str(tick.ask if request.direction == Direction.BUY else tick.bid))
        payload = {
            "action": self.mt5.TRADE_ACTION_DEAL if request.order_type == OrderType.MARKET else self.mt5.TRADE_ACTION_PENDING,
            "symbol": request.symbol,
            "volume": float(request.size),
            "type": self.mt5.ORDER_TYPE_BUY if request.direction == Direction.BUY else self.mt5.ORDER_TYPE_SELL,
            "price": float(price),
            "sl": float(request.stop_loss),
            "tp": float(request.take_profit or 0),
            "deviation": request.max_slippage_points,
            "magic": self.magic_number,
            "comment": self._execution_comment(request.idempotency_key),
            "type_time": self.mt5.ORDER_TIME_GTC,
            "type_filling": self.mt5.ORDER_FILLING_IOC,
        }
        check = self.mt5.order_check(payload)
        if not check or check.retcode != 0:
            if check is None and allow_reconnect:
                self.healthy = False
                if self._ensure_connected():
                    duplicate = self._existing_execution(request.idempotency_key)
                    if duplicate is not None:
                        return duplicate
                    return self._place_order_once(request, allow_reconnect=False)
            raw = check._asdict() if check else {}
            return BrokerResult(False, None, None, str(raw.get("retcode", "CHECK_FAILED")), "MT5 order check failed", raw)
        result = self.mt5.order_send(payload)
        if self._connection_failed(result) and allow_reconnect:
            self.healthy = False
            if self._ensure_connected():
                duplicate = self._existing_execution(request.idempotency_key)
                if duplicate is not None:
                    return duplicate
                return self._place_order_once(request, allow_reconnect=False)
        raw = result._asdict() if result else {}
        accepted = bool(result and result.retcode in {self.mt5.TRADE_RETCODE_DONE, self.mt5.TRADE_RETCODE_PLACED})
        if not accepted and self._connection_failed(result):
            self.healthy = False
        return BrokerResult(accepted, str(result.order) if accepted else None, Decimal(str(result.price)) if accepted else None, str(raw.get("retcode", "SEND_FAILED")), raw.get("comment", ""), raw)

    def _connection_failed(self, result: Any | None) -> bool:
        if result is None:
            return True
        connection_codes = {
            value
            for name in ("TRADE_RETCODE_CONNECTION", "TRADE_RETCODE_TIMEOUT")
            if (value := getattr(self.mt5, name, None)) is not None
        }
        return getattr(result, "retcode", None) in connection_codes

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
        positions = self.mt5.positions_get() if self._ensure_connected() else None
        normalized: list[dict[str, Any]] = []
        for position in positions or []:
            if getattr(position, "magic", None) != self.magic_number or not str(getattr(position, "comment", "")).startswith("PPB:"):
                continue
            raw = position._asdict()
            raw.update({
                "id": str(position.ticket),
                "size": Decimal(str(position.volume)),
                "direction": Direction.BUY.value if position.type == self.mt5.POSITION_TYPE_BUY else Direction.SELL.value,
                "stop_loss": raw.get("sl"),
                "take_profit": raw.get("tp"),
            })
            normalized.append(raw)
        return normalized

    def get_account_positions(self) -> list[dict[str, Any]]:
        positions = self.mt5.positions_get() if self._ensure_connected() else None
        normalized: list[dict[str, Any]] = []
        for position in positions or []:
            raw = position._asdict()
            raw.update({
                "id": str(position.ticket),
                "size": Decimal(str(position.volume)),
                "direction": Direction.BUY.value if position.type == self.mt5.POSITION_TYPE_BUY else Direction.SELL.value,
                "stop_loss": raw.get("sl"),
                "take_profit": raw.get("tp"),
            })
            normalized.append(raw)
        return normalized

    def get_closed_position(self, position_id: str) -> dict[str, Any] | None:
        if not self._ensure_connected():
            return None
        getter = getattr(self.mt5, "history_deals_get", None)
        if getter is None:
            return None
        try:
            deals = list(getter(position=int(position_id)) or [])
        except Exception:
            return None
        exit_entries = {
            value for name in ("DEAL_ENTRY_OUT", "DEAL_ENTRY_OUT_BY")
            if (value := getattr(self.mt5, name, None)) is not None
        }
        exits = [deal for deal in deals if getattr(deal, "entry", None) in exit_entries and getattr(deal, "magic", self.magic_number) == self.magic_number]
        if not exits:
            return None
        deal = max(exits, key=lambda item: getattr(item, "time_msc", 0) or getattr(item, "time", 0))
        reason_names = {
            getattr(self.mt5, "DEAL_REASON_TP", object()): "TP",
            getattr(self.mt5, "DEAL_REASON_SL", object()): "SL",
            getattr(self.mt5, "DEAL_REASON_EXPERT", object()): "EXPERT",
            getattr(self.mt5, "DEAL_REASON_CLIENT", object()): "MANUAL",
            getattr(self.mt5, "DEAL_REASON_MOBILE", object()): "MANUAL",
            getattr(self.mt5, "DEAL_REASON_WEB", object()): "MANUAL",
        }
        timestamp = getattr(deal, "time", None)
        profit = sum(Decimal(str(getattr(deal, field, 0) or 0)) for field in ("profit", "commission", "swap", "fee"))
        return {
            "position_id": position_id,
            "price": Decimal(str(deal.price)),
            "volume": Decimal(str(deal.volume)),
            "profit": profit,
            "reason": reason_names.get(getattr(deal, "reason", None), "BROKER"),
            "closed_at": datetime.fromtimestamp(timestamp, tz=timezone.utc) if timestamp else datetime.now(timezone.utc),
            "deal_ticket": str(getattr(deal, "ticket", "")),
        }

    def get_pending_orders(self) -> list[dict[str, Any]]:
        orders = self.mt5.orders_get() if self._ensure_connected() else None
        return [order._asdict() for order in orders] if orders else []

    def _get_position(self, position_id: str) -> Any | None:
        if not self._ensure_connected():
            return None
        positions = self.mt5.positions_get(ticket=int(position_id))
        return positions[0] if positions else None

    def _close_deal(self, position: Any, volume: Decimal, *, allow_reconnect: bool = True) -> BrokerResult:
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
            if check is None and allow_reconnect:
                return self._retry_close_after_disconnect(position, volume)
            raw = check._asdict() if check else {}
            return BrokerResult(False, str(position.ticket), None, str(raw.get("retcode", "CHECK_FAILED")), "MT5 close check failed", raw)
        result = self.mt5.order_send(payload)
        if self._connection_failed(result) and allow_reconnect:
            return self._retry_close_after_disconnect(position, volume)
        raw = result._asdict() if result else {}
        accepted_codes = {self.mt5.TRADE_RETCODE_DONE}
        if hasattr(self.mt5, "TRADE_RETCODE_DONE_PARTIAL"):
            accepted_codes.add(self.mt5.TRADE_RETCODE_DONE_PARTIAL)
        accepted = bool(result and result.retcode in accepted_codes)
        fill = Decimal(str(result.price)) if accepted else None
        return BrokerResult(accepted, str(position.ticket), fill, str(raw.get("retcode")), raw.get("comment", ""), raw)

    def _retry_close_after_disconnect(self, original_position: Any, requested_volume: Decimal) -> BrokerResult:
        self.healthy = False
        if not self._ensure_connected():
            return BrokerResult(False, str(original_position.ticket), None, "DISCONNECTED", self.last_error or "MT5 is disconnected", {})
        positions = self.mt5.positions_get(ticket=int(original_position.ticket)) or []
        if not positions:
            return BrokerResult(True, str(original_position.ticket), None, "ALREADY_CLOSED", "Position was closed before the reconnect completed", {"deduplicated": True})
        current = positions[0]
        original_volume = Decimal(str(original_position.volume))
        current_volume = Decimal(str(current.volume))
        if current_volume <= original_volume - requested_volume:
            return BrokerResult(True, str(original_position.ticket), None, "ALREADY_PARTIALLY_CLOSED", "Requested close was already applied before reconnect", {"deduplicated": True, "remaining_volume": str(current_volume)})
        return self._close_deal(current, requested_volume, allow_reconnect=False)

    def _position_request(self, position_id: str, action: int, extra: dict[str, Any]) -> BrokerResult:
        if not self._ensure_connected():
            return BrokerResult(False, position_id, None, "DISCONNECTED", "MT5 is disconnected", {})
        payload = {"action": action, "position": int(position_id), **extra}
        result = self.mt5.order_send(payload)
        if self._connection_failed(result):
            self.healthy = False
            if not self._ensure_connected():
                return BrokerResult(False, position_id, None, "DISCONNECTED", self.last_error or "MT5 is disconnected", {})
            result = self.mt5.order_send(payload)
        raw = result._asdict() if result else {}
        accepted = bool(result and result.retcode == self.mt5.TRADE_RETCODE_DONE)
        if not accepted and self._connection_failed(result):
            self.healthy = False
        return BrokerResult(accepted, position_id, None, str(raw.get("retcode")), raw.get("comment", ""), raw)

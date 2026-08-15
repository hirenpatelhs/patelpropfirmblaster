from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from app.brokers.base import BrokerAdapter


class LiveEquityUnavailable(RuntimeError):
    """Raised when a broker cannot provide a complete, trustworthy snapshot."""


@dataclass(frozen=True)
class LiveEquitySnapshot:
    balance: Decimal
    equity: Decimal
    margin: Decimal
    margin_level: Decimal
    floating_pnl: Decimal
    positions: list[dict[str, Any]]
    pending_orders: list[dict[str, Any]]


def _decimal(info: dict[str, Any], key: str) -> Decimal:
    value = info.get(key)
    if value is None:
        raise LiveEquityUnavailable(f"MT5 account_info omitted {key}")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise LiveEquityUnavailable(f"MT5 account_info returned invalid {key}") from exc


def capture_live_equity(broker: BrokerAdapter) -> LiveEquitySnapshot:
    """Read one fail-closed broker snapshot, including both exposure queries."""
    if not broker.health_check():
        raise LiveEquityUnavailable("MT5 broker is disconnected")
    info = broker.get_account_info()
    positions = broker.get_account_positions()
    pending_orders = broker.get_pending_orders()
    if not broker.health_check():
        raise LiveEquityUnavailable("MT5 broker disconnected during live-equity polling")

    balance = _decimal(info, "balance")
    equity = _decimal(info, "equity")
    margin = _decimal(info, "margin")
    margin_level = _decimal(info, "margin_level")
    # MT5 position.profit is authoritative. With no positions floating P&L is
    # zero; adapters missing profit on an open row are not safe to route on.
    try:
        floating = sum((Decimal(str(position["profit"])) for position in positions), Decimal("0"))
    except (KeyError, InvalidOperation, ValueError) as exc:
        raise LiveEquityUnavailable("MT5 open position omitted valid floating profit") from exc
    return LiveEquitySnapshot(balance, equity, margin, margin_level, floating, positions, pending_orders)


def remaining_daily_buffer(limit: Decimal, realized_pnl: Decimal, floating_pnl: Decimal) -> Decimal:
    """Return limit minus current signed loss (profits do not enlarge the limit)."""
    current_loss = max(Decimal("0"), -(realized_pnl + floating_pnl))
    return max(Decimal("0"), abs(limit) - current_loss)

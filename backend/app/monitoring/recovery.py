from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.brokers.base import BrokerAdapter


@dataclass(frozen=True)
class ReconciliationIssue:
    kind: str
    broker_position_id: str
    detail: str


def reconcile_positions(database_positions: list[dict[str, Any]], broker: BrokerAdapter, broker_positions: list[dict[str, Any]] | None = None) -> list[ReconciliationIssue]:
    db_by_id = {str(p["broker_position_id"]): p for p in database_positions}
    broker_by_id = {str(p.get("id") or p.get("ticket")): p for p in (broker_positions if broker_positions is not None else broker.get_open_positions())}
    issues: list[ReconciliationIssue] = []
    for position_id in db_by_id.keys() - broker_by_id.keys():
        issues.append(ReconciliationIssue("MISSING_ON_BROKER", position_id, "Database position is absent from broker"))
    for position_id in broker_by_id.keys() - db_by_id.keys():
        issues.append(ReconciliationIssue("UNKNOWN_ON_BROKER", position_id, "Broker position is not tracked in database"))
    for position_id in db_by_id.keys() & broker_by_id.keys():
        db_pos, broker_pos = db_by_id[position_id], broker_by_id[position_id]
        expected_volume = db_pos.get("remaining_volume", db_pos.get("size"))
        if Decimal(str(expected_volume)) != Decimal(str(broker_pos.get("size") or broker_pos.get("volume"))):
            issues.append(ReconciliationIssue("VOLUME_MISMATCH", position_id, "Remaining position volume differs"))
        database_sl = db_pos.get("stop_loss")
        broker_sl = broker_pos.get("stop_loss") if broker_pos.get("stop_loss") is not None else broker_pos.get("sl")
        if (database_sl is None) != (broker_sl is None) or (database_sl is not None and Decimal(str(database_sl)) != Decimal(str(broker_sl))):
            issues.append(ReconciliationIssue("SL_MISMATCH", position_id, "Stop loss differs"))
        database_tp = db_pos.get("take_profit")
        broker_tp = broker_pos.get("take_profit") or broker_pos.get("tp")
        if database_tp is not None and Decimal(str(database_tp)) != Decimal(str(broker_tp or 0)):
            issues.append(ReconciliationIssue("TP_MISMATCH", position_id, "Broker take profit differs"))
    return issues

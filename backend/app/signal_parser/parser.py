import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from uuid import UUID

from app.core.enums import Confidence, Direction, OrderType, RiskClassification
from app.schemas.signal import NormalizedSignal, SignalUpdate


SYMBOL_ALIASES = {
    "GOLD": "XAUUSD",
    "XAU/USD": "XAUUSD",
    "XAUUSD": "XAUUSD",
    "NASDAQ": "NAS100",
    "NAS100": "NAS100",
    "US30": "US30",
}
NUMBER = r"(\d+(?:\.\d+)?)"


class ParseError(ValueError):
    pass


class ManualReviewRequired(ParseError):
    """The message contains a trade instruction that is unsafe to automate."""


class DeterministicSignalParser:
    """Fail-closed parser. LLM results, if added, must be fed back through this validator."""

    def parse(
        self,
        text: str,
        source_id: UUID | str,
        message_id: int,
        timestamp: datetime | None = None,
    ) -> NormalizedSignal:
        clean = " ".join(text.upper().replace("@", " @ ").split())
        symbol = self._symbol(clean)
        direction_match = re.search(r"\b(BUY|SELL)\b", clean)
        if not symbol or not direction_match:
            raise ParseError("Signal must contain an unambiguous symbol and direction")
        direction = Direction(direction_match.group(1))
        entry_min, entry_max, entry_price = self._entry(clean, symbol, direction.value)
        stop_loss = self._labelled_number(clean, ("SL", "STOP", "STOP LOSS"))
        take_profits: list[Decimal] = []
        for label in (r"TP\d*", r"TAKE\s+PROFIT\d*", r"TARGET\d*"):
            # Do not backtrack TP1 to TP and mistake its sequence number for a
            # price in management phrases such as "SL entry TP1".
            pattern = rf"\b{label}(?!\d)\s*[:=@-]?\s*" + NUMBER
            take_profits.extend(Decimal(value) for value in re.findall(pattern, clean))
        confidence = Confidence.HIGH if stop_loss is not None and take_profits else Confidence.MEDIUM
        risk_classification = self._risk_classification(clean)
        risk_hint = "MOVE_SL_TO_ENTRY_AT_TP1" if re.search(r"\bSL\s+(?:TO\s+)?ENTRY\s+(?:AT\s+)?TP\s*1\b", clean) else None
        return NormalizedSignal(
            source_id=source_id,
            telegram_message_id=message_id,
            symbol=symbol,
            direction=direction,
            order_type=OrderType.MARKET,
            entry_price=entry_price,
            entry_min=entry_min,
            entry_max=entry_max,
            stop_loss=stop_loss,
            take_profits=take_profits,
            risk_hint=risk_hint,
            timestamp=timestamp or datetime.now(timezone.utc),
            confidence=confidence,
            risk_classification=risk_classification,
            raw_text=text,
        )

    def parse_update(self, text: str) -> SignalUpdate:
        clean = " ".join(text.upper().split())
        symbol = self._symbol(clean)
        if re.search(r"\b(?:OPTIONS?\s+FOR\s+YOU|YOU\s+CHOOSE|DOES\s+THIS\s+MEAN|IF\s+YOU(?:['’]RE|\s+ARE)?|IF\s+WISHING|YOU\s+CAN\s+CLOSE)\b", clean):
            raise ManualReviewRequired("Conditional or optional trade instruction requires manual review")
        if re.search(r"\b(?:REENTER|RE-ENTER|REWNTER)\b", clean):
            raise ManualReviewRequired("Re-entry instruction requires a new risk decision and manual review")
        if re.search(r"\b(?:MOVE|EXTEND)\s+(?:TP|TARGET)\s*\d+\b", clean):
            raise ManualReviewRequired("Target-price modification is not supported automatically")
        if re.search(r"\bCLOSE\s+TP\s*[1-9]\d*\s+MANUALLY(?:\s+NOW)?\b", clean):
            raise ManualReviewRequired("Manual target-close instruction requires manual review")

        target_hits = list(re.finditer(r"\bTP\s*([1-9]\d*)(?:\s+HIT|\s*(?:✅|☑|✔|âœ…|â˜‘|âœ”))", clean))
        if len(target_hits) > 1:
            raise ManualReviewRequired("Multiple target results in one message require manual review")

        partial = bool(re.search(r"\b(?:CLOSE\s+(?:50%|HALF)|TAKE\s+PARTIAL|BOOK\s+PARTIAL|SECURE\s+PARTIAL)(?=\s|$)", clean))
        break_even = bool(re.search(r"\b(?:MOVE\s+(?:SL|STOP)\s+(?:TO\s+)?(?:BE|BREAK\s*EVEN)|SECURE\s+ENTRY|(?:AND|&)\s+BE|SL\s+(?:TO\s+)?ENTRY|(?:USE\s+)?MY\s+ENTRY\s+SL)\b", clean))
        if target_hits and break_even:
            return SignalUpdate(action="TARGET_HIT_AND_BREAK_EVEN", symbol=symbol, target_sequence=int(target_hits[0].group(1)), confidence=Confidence.HIGH)
        if partial and break_even:
            return SignalUpdate(action="PARTIAL_CLOSE_AND_BREAK_EVEN", symbol=symbol, percentage=Decimal("0.50"), confidence=Confidence.HIGH)
        if break_even:
            return SignalUpdate(action="MOVE_BREAK_EVEN", symbol=symbol, confidence=Confidence.HIGH)
        if partial:
            return SignalUpdate(action="PARTIAL_CLOSE", symbol=symbol, percentage=Decimal("0.50"), confidence=Confidence.HIGH)
        move = re.search(r"\bMOVE\s+(?:SL|STOP)\s+(?:TO\s+)?" + NUMBER, clean)
        if move:
            return SignalUpdate(action="MOVE_STOP", symbol=symbol, value=Decimal(move.group(1)), confidence=Confidence.HIGH)
        if re.search(r"\b(?:FULLY\s+CLOSE|CLOSE(?:\s+\w+)?\s+NOW|CLOSE\s+(?:GOLD|XAUUSD)|CLOSE\s+HERE)\b", clean):
            return SignalUpdate(action="CLOSE", symbol=symbol, confidence=Confidence.MEDIUM if symbol is None else Confidence.HIGH)
        if re.search(r"\b(CANCEL ORDER|DELETE PENDING)\b", clean):
            return SignalUpdate(action="CANCEL_PENDING", symbol=symbol, confidence=Confidence.MEDIUM)
        if target_hits:
            return SignalUpdate(action="TARGET_HIT", symbol=symbol, target_sequence=int(target_hits[0].group(1)), confidence=Confidence.HIGH)
        target_hit = re.search(r"\bTP\s*([1-9]\d*)(?:\s+HIT|\s*[✅☑✔])", clean)
        if target_hit:
            return SignalUpdate(action="TARGET_HIT", symbol=symbol, target_sequence=int(target_hit.group(1)), confidence=Confidence.HIGH)
        if re.search(r"\bHOLD(?:\s+RUNNERS?)?\b", clean):
            return SignalUpdate(action="HOLD", symbol=symbol, confidence=Confidence.MEDIUM)
        raise ParseError("Unrecognized or ambiguous trade update")

    @staticmethod
    def _risk_classification(text: str) -> RiskClassification:
        if re.search(r"\b(?:VERY\s+HIGH\s+RISK|EXTREMELY\s+RISKY|VERY\s+RISKY)\b", text):
            return RiskClassification.VERY_HIGH_RISK
        if re.search(r"\b(?:HIGH\s+RISK|RISKY\s+TRADE|HIGH[-\s]+RISK)\b", text):
            return RiskClassification.HIGH_RISK
        if re.search(r"\b(?:MEDIUM\s+RISK|MODERATE\s+RISK|MED[-\s]+RISK)\b", text):
            return RiskClassification.MEDIUM_RISK
        return RiskClassification.NORMAL

    @staticmethod
    def _symbol(text: str) -> str | None:
        for alias in sorted(SYMBOL_ALIASES, key=len, reverse=True):
            if re.search(rf"(?<![A-Z]){re.escape(alias)}(?![A-Z])", text):
                return SYMBOL_ALIASES[alias]
        return None

    @staticmethod
    def _labelled_number(text: str, labels: tuple[str, ...]) -> Decimal | None:
        for label in sorted(labels, key=len, reverse=True):
            match = re.search(rf"\b{re.escape(label)}\s*[:=@-]?\s*{NUMBER}", text)
            if match:
                return Decimal(match.group(1))
        return None

    @staticmethod
    def _entry(text: str, symbol: str, direction: str) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
        range_match = re.search(r"\b(?:ENTRY\s*)?" + NUMBER + r"\s*[-–]\s*" + NUMBER, text)
        if range_match:
            low, high = Decimal(range_match.group(1)), Decimal(range_match.group(2))
            return min(low, high), max(low, high), None
        labelled = re.search(r"\b(?:ENTRY|ENTER)\s*[:=@-]?\s*" + NUMBER, text)
        if labelled:
            return None, None, Decimal(labelled.group(1))
        at_price = re.search(r"@\s*" + NUMBER, text)
        if at_price:
            return None, None, Decimal(at_price.group(1))
        escaped = re.escape(symbol)
        compact = text.replace("XAU/USD", "XAUUSD").replace("GOLD", "XAUUSD")
        implicit = re.search(rf"\b{escaped}\s+{direction}(?:\s+NOW)?\s+{NUMBER}", compact)
        if implicit:
            try:
                value = Decimal(implicit.group(1))
                # In the supported gold/index dialects, a small unlabeled
                # value after BUY/SELL is a lot hint rather than an entry.
                if value <= Decimal("10"):
                    return None, None, None
                return None, None, value
            except InvalidOperation:
                pass
        return None, None, None

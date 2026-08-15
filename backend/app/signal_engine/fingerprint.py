import hashlib
from datetime import datetime

from app.schemas.signal import NormalizedSignal


def signal_fingerprint(signal: NormalizedSignal, bucket_seconds: int = 300) -> str:
    bucket = int(signal.timestamp.timestamp()) // bucket_seconds
    entry = f"{signal.entry_min or signal.entry_price or ''}:{signal.entry_max or ''}"
    raw = f"{signal.source_id}|{signal.symbol}|{signal.direction}|{entry}|{signal.stop_loss}|{bucket}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def is_expired(signal: NormalizedSignal, now: datetime, ttl_seconds: int = 120) -> bool:
    return (now - signal.timestamp).total_seconds() > ttl_seconds

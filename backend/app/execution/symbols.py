def broker_symbol(canonical_symbol: str, mappings: dict[str, str] | None) -> str:
    """Resolve an account-specific broker symbol without mutating the signal."""
    mapped = (mappings or {}).get(canonical_symbol.upper(), canonical_symbol).strip()
    if not mapped or len(mapped) > 40:
        raise ValueError("Invalid broker symbol mapping")
    return mapped

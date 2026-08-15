from pathlib import Path
from uuid import UUID

from app.brokers.mt5 import MetaTrader5Adapter
from app.core.security import decrypt_secret
from app.models.entities import BrokerConnection, TradingAccount


class BrokerConfigurationError(ValueError):
    pass


class MT5DemoBrokerRegistry:
    """Process-local adapters built only from encrypted, database-backed configuration."""

    def __init__(self) -> None:
        self._brokers: dict[UUID, MetaTrader5Adapter] = {}
        self._signatures: dict[UUID, tuple[object, ...]] = {}

    def get(self, account: TradingAccount, connection: BrokerConnection) -> MetaTrader5Adapter:
        if account.platform.upper() not in {"MT5", "METATRADER5"}:
            raise BrokerConfigurationError("DEMO account platform must be MT5")
        if connection.adapter.upper() not in {"MT5", "METATRADER5"}:
            raise BrokerConfigurationError("Broker connection adapter must be MT5")
        if not connection.terminal_path:
            raise BrokerConfigurationError("MT5 terminal path is missing")
        if not account.broker_server:
            raise BrokerConfigurationError("MT5 broker server is missing")
        if connection.magic_number is None:
            raise BrokerConfigurationError("MT5 magic number is missing")
        try:
            login = int(account.account_number)
        except ValueError as exc:
            raise BrokerConfigurationError("MT5 account number must be numeric") from exc
        password = decrypt_secret(account.credentials_encrypted) if account.credentials_encrypted else None
        signature = (
            connection.terminal_path,
            login,
            account.broker_server,
            account.credentials_encrypted,
            connection.magic_number,
        )
        broker = self._brokers.get(account.id)
        if broker is None or self._signatures.get(account.id) != signature:
            if broker is not None:
                broker.disconnect()
            broker = MetaTrader5Adapter(
                Path(connection.terminal_path),
                login,
                account.broker_server,
                password,
                connection.magic_number,
                require_demo=True,
            )
            self._brokers[account.id] = broker
            self._signatures[account.id] = signature
        return broker


demo_broker_registry = MT5DemoBrokerRegistry()

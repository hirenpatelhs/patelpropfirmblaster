# Execution

Broker implementations conform to `BrokerAdapter`. `MockBrokerAdapter` supplies idempotent fills, stop modification, partial close and close for development and Shadow mode. `MetaTrader5Adapter` delays importing the Windows-only official package and records all MT5 return codes.

Execution rechecks signal age, confidence, SL, broker health, current price, entry range and instrument specification before sizing. It never blindly retries an ambiguous broker response. A retry is permitted only with the same execution ID after reconciliation proves no order exists.

Break-even may only improve the stop. Partial closes are account-specific. “Close gold now” is scoped to positions linked to the resolved signal; it is not a global close command. Unknown broker positions alert an administrator and remain untouched by default.

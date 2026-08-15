# Architecture

PPB separates ingestion, deterministic decision making, asynchronous execution and presentation. FastAPI handles authenticated configuration and reads; Redis workers handle signal processing, routing, broker execution, synchronization, notifications and analytics. PostgreSQL is authoritative. HTTP handlers never place orders.

Critical decisions run twice: an initial account routing/rule/risk decision and a final pre-execution check against fresh price, equity, drawdown, exposure, lot constraints and signal age. A unique `(signal_id, account_id)` decision and unique execution ID provide database idempotency; broker comments/magic numbers provide broker-side correlation.

Shadow accounts use only the mock/virtual execution path. A live adapter is rejected when mode is SHADOW. Startup recovery reconnects adapters, reloads locks/daily state and reconciles broker positions. Unknown broker positions produce alerts and are not automatically closed.

## Trust boundaries

Telegram messages, broker state, browser input and news providers are untrusted. Pydantic validates boundary data. The parser can accept an optional LLM suggestion later, but that suggestion must be normalized and pass the same deterministic pipeline. Secrets remain server-side and broker credentials use Fernet encryption at rest.

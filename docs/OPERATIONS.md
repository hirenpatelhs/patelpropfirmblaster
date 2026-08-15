# Operations

Monitor backend, database, Redis, Telegram clients, notification bot, workers and each broker heartbeat. CONNECTED means fresh and responsive; DEGRADED means a non-critical dependency is unavailable; DISCONNECTED prevents affected execution.

On restart: restore global/account locks, load today’s statistics, reconnect brokers, fetch open positions and pending orders, compare with the database, flag missing/unknown/size/SL/TP mismatches, and only then enable routing. Never assume an empty broker.

Daily: review locked accounts, reconciliation alerts, rejected-decision explanations and backup success. Weekly: test health and restore procedures. Before firm rule changes or account stage changes: review current terms, update the profile, record the review timestamp and repeat Shadow acceptance testing.

Incident response: stop new trades first. Evaluate open positions separately; close only with deliberate confirmation. Preserve logs, audit rows, Telegram message IDs and rule/risk snapshots. Do not edit historical snapshots.
# Worker leadership and recovery

Register exactly one `PatelPropfirmWorker` Windows service. `start-worker.ps1` holds the host-wide `Global\PatelPropfirmBlasterWorker` mutex, while the worker must also own the renewable Redis key `ppb:lock:scheduler` before it can enqueue reconciliation, notification, or aggregation cycles. A second process may consume queued work, but it cannot run another scheduler.

Jobs use the `ppb:jobs:stream` Redis Stream consumer group. A job is acknowledged only after its handler commits successfully. Following a crash, a worker claims pending events after 30 seconds. Do not delete the stream, consumer group, scheduler key, or PostgreSQL decision records as a way to recover trading.

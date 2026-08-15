# Real infrastructure validation results

Status: **PENDING VPS EXECUTION**

This file is deliberately not a claim of infrastructure validation. The integration harness is implemented, but no PostgreSQL or Redis service was installed or started on the development Mac. Fill this ledger only from an isolated Windows VPS test run; never use production PPB data or service names.

## Required result record

- Run date/time:
- PPB revision/package:
- Windows version:
- PostgreSQL version:
- Redis-compatible server and version:
- Dedicated test database name:
- Dedicated test Redis port/database:
- Unit/torture result:
- Real-infrastructure integration result:
- Frontend lint result:
- Frontend production build result:
- Two-worker result:
- Leader failover result:
- In-flight crash/reclaim result:
- PostgreSQL outage/recovery result:
- Redis restart/recovery result:
- API restart result:
- Full lifecycle state-recovery result:
- Duplicate listener-delivery result:
- Consistency-check result:
- Remaining limitations:

## Safety gates

- [ ] Every seeded account was SHADOW.
- [ ] The PostgreSQL URL named a dedicated TEST database.
- [ ] The Redis URL used a nonzero test database.
- [ ] Disruptive restart tests used separate TEST service instances.
- [ ] No MT5 terminal or prop-firm account was connected.
- [ ] LIVE execution remained disabled.

Run instructions and guarded commands are in `docs/INTEGRATION_TESTING.md`.

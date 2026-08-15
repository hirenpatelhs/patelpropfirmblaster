# Rule and risk engine

Rule profiles are editable records, not permanent claims about a prop firm. A profile is specific to stage, size and platform. Live activation requires a recent human acknowledgement that current terms permit the configured method.

The engine returns APPROVED or REJECTED with every applicable reason. Checks include automation permissions, symbols, news blackout, hedge restrictions, maximum positions, daily/overall/trailing drawdown buffers and internal reserves. The daily guard adds loss, profit, trade-count, consecutive-loss and manual locks.

Position size is `risk currency / ((entry − stop) / tick size × tick value)`, rounded down to the broker step and capped by broker bounds, configured maximum risk, remaining daily/overall buffer and exposure. Loss streak and recovery multipliers can only reduce risk. Martingale and loss-recovery increases are unsupported.

Drawdown modes are STATIC, TRAILING_BALANCE, TRAILING_EQUITY and END_OF_DAY_TRAILING. The safety threshold is the firm threshold plus the configured reserve. A proposed order is evaluated against the safety threshold, never the theoretical firm limit.

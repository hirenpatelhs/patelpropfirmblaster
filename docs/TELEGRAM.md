# Telegram

Telethon reads authorized sources with a persistent session and handles new and edited messages, replies, message IDs, sender/time/body and attachment metadata. `(source_id, telegram_message_id)` prevents replay. A second fingerprint covers reposts and network retries.

The deterministic parser handles common BUY/SELL formats, aliases and entry ranges. Unclear messages fail closed. HIGH confidence may be automatic, MEDIUM should require confirmation and LOW is rejected. Updates are linked using reply IDs first, then unambiguous source/symbol/direction/recent-position context. An update with multiple plausible targets is not applied.

The notification Bot API is outbound-only. Never expose Telethon session files or bot tokens; restrict the Telegram data directory ACL.

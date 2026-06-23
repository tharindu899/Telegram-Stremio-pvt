# Live upload intake verification

This build binds the live upload receiver directly to the primary Telegram bot before it starts receiving updates. It also normalizes source IDs and logs the primary bot role for every configured source at startup.

Look for these startup lines:

- `Live upload receiver bound to @...`
- `Live source verified: ... primary bot role=administrator`

If the role is not administrator or cannot be verified, make the primary bot an administrator in the exact source channel/group. For topic groups, use the parent group ID.

When a test media message is received, the log starts with `Live media received`. If no such line appears after a fresh upload, Telegram did not deliver the update to the primary bot; file naming is not the cause.

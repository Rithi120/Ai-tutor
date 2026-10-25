# Learnova Premium keys

Premium is a manually paid, redeemable 30-day entitlement. There is no online checkout or automatic renewal.

## Deployment

1. Set `PREMIUM_ADMIN_IDENTIFIERS` to the comma-separated usernames or email addresses of existing accounts that may manage Premium keys. Keep this environment setting restricted to the operator.
2. Keep `AI_BUDGET_GLOBAL_TOKENS_PER_DAY` configured. The application defaults it to 2,000,000 tokens per UTC day when the variable is absent; set a value that matches the provider budget. An explicit blank disables this limit. Global/provider budgets, request-size bounds, session limits, and hourly abuse limits still apply to Premium.
3. Deploy the application normally. Startup's existing `ensure_database()` migration runner creates the new Premium tables and records schema version `029_premium_keys`. For an explicit maintenance step, run `flask init-db` against the intended database before switching application instances. This is additive and does not delete existing data.

## Cash payment and key issue

Confirm the cash payment in person first. Sign into the allowlisted account, open **Settings → Manage Premium keys**, enter the amount, date, and an optional non-sensitive reference, then generate a key. Copy the key from the one-time result and give it directly to the student. The database stores only its SHA-256 hash. Unused keys expire after one year and can be revoked from the issued-key list.

The management page shows unused, redeemed, expired, or revoked status, the payment reference, redemption time, and the account that redeemed a key. It does not provide a key recovery view. If a plaintext key is lost, revoke it and issue another after confirming the payment.

## Student redemption

The student signs in, opens **Settings → Premium**, enters the key, and redeems it. A new entitlement lasts 30 days; another key redeemed while Premium is active adds 30 days to the existing expiry. Expired access stops applying based on the timestamp even before any status cleanup.

## Operational notes

Premium removes the AdSense loader from authenticated Premium pages and bypasses the normal per-user daily request and token quotas in the central AI gateway. It is not technically unlimited: hourly per-account abuse protection and global, provider, session, input, and output safeguards remain active. Review global limits and provider prices before issuing keys at scale. Payment records should contain only a non-sensitive reference; follow the operator's applicable accounting, consumer, tax, privacy, and data-retention requirements.

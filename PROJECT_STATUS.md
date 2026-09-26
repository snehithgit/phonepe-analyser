# PhonePe Analyser — Project Status v0.1.2

## Current architecture

Single-container, local-first deployment:

- FastAPI API + React static frontend in one image
- SQLite database persisted at `/data/phonepe.db`
- PhonePe CSV is the only source format in scope
- No AI, no cloud APIs, no telemetry

## Verified fixes in v0.1.2

- Rollback is genuinely reversible: rolled-back transaction IDs are restored, not re-inserted against the unique constraint.
- Preview reports active duplicates separately from restorable tombstones.
- Category rules are loaded once per import instead of once per transaction.
- Payment instruments are cached/preloaded during import.
- Catch-all CREDIT → Receipts categorization removed; unmatched credits remain Uncategorized.
- Valid rows without a UTR are retained and flagged as a data-quality warning.
- Self-transfers are excluded from pattern detection and all effective-spend analytics.
- Exact refund/debit pairs are excluded consistently from spend/category/daily analytics; unmatched refunds remain visible separately.
- Recurring detection separates clearly different amount streams before cadence analysis and uses calendar-month signal as a secondary monthly signal.
- Rule matching uses an explicit safe-field whitelist; default field is `counterparty_normalized`.
- Transaction/rule list endpoints eager-load related categories to avoid lazy-load churn.
- Permissive CORS removed because production and Vite development are same-origin/proxied.
- Frontend API errors parse FastAPI `detail` cleanly.
- Frontend request hook clears stale errors, aborts stale requests, and transaction search is debounced.
- React error boundary added.
- Hash-backed navigation supports refresh/back/forward/deep links without another routing dependency.
- Frontend top-level dependency versions pinned; uploader v1.4 generates and commits `package-lock.json` before publishing.
- Obsolete split-container Dockerfiles/nginx config removed.

## Validation

Backend regression suite: 8 tests passing.

Real PhonePe samples still parse exactly:

- Apr–Sep sample: 575 transactions, 7 footer rows skipped, 0 missing UTR, 0 direction conflicts.
- Aug–Sep sample: 110 transactions, 7 footer rows skipped, 0 missing UTR, 0 direction conflicts.

## Next product work (not bug fixes)

- Rule/category CRUD and rule tester UI
- Manual transaction edit drawer
- Probable/fuzzy refund review UI beyond conservative exact matching
- Own-account matching UI
- Monthly/category trend screens and heatmap
- Budgets/report export/backup UI
- Audit log/data-quality review queue

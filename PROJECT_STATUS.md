# Project Status — PhonePe Analyser v0.1.0

## Implemented now

### P0 data correctness
- CSV-only source contract; no PDF/OCR code
- Dynamic PhonePe transaction-header detection
- Metadata duration parsing
- Footer/non-transaction row rejection
- `Decimal` money parsing to integer paise
- Transaction ID and UTR stored as TEXT
- SHA-256 import fingerprint metadata
- Transaction-ID dedup across overlapping exports
- Import preview
- Reversible (soft/tombstone) import rollback
- SQLite WAL mode
- First-class account/payment instrument table
- Categories and rules tables

### P1 usable analyser
- Dashboard totals
- Daily-spend mini chart
- Category totals
- Top counterparties
- Searchable transaction ledger
- Direction filter
- Import history
- Responsive mobile transaction cards
- Mobile bottom navigation

### P2 deterministic rules foundation
- Description parsing: Paid to / Received from / Payment to / Transfer to / Refund from / Mobile recharged / International Roaming Pack
- Direction conflict warning
- Category rules: EXACT / CONTAINS / STARTS_WITH / ENDS_WITH / REGEX engine support in backend
- Seed rules for obvious food/grocery/medical/entertainment/utility/recharge/etc. merchants
- Rule listing page
- Manual transaction patch API prepared for category/notes/self-transfer overrides

### P3 pattern foundation
- Patterns run separately for DEBIT and CREDIT
- recurring fixed
- recurring variable
- frequent counterparty
- probable (2 occurrence) vs confirmed (3+)
- median interval
- interval MAD
- median amount
- amount MAD / stability
- expected next date

## Validated against the supplied real CSVs

- Apr 01–Sep 26 statement: 575 valid transactions, 7 footer rows skipped, 0 direction conflicts
- Aug 27–Sep 26 statement: 110 valid transactions, 7 footer rows skipped, 0 direction conflicts
- Import Apr–Sep first: 575 new
- Preview/import Aug–Sep next: 0 new, 110 duplicates
- Parser unit tests: 3/3 passing
- Python compile: passing

The real statements are NOT bundled in this project.

## Not complete yet

### Immediate next work
1. Editable rule CRUD + rule tester/preview
2. Transaction edit drawer/UI (category, notes, self-transfer)
3. Counterparty aliases / merchant normalization UI
4. Exact/probable refund-pair matching
5. Own-account alias management + self-transfer pair matching
6. Improve recurrence with amount-compatible sub-clustering before cadence
7. Category/month trends and date-range filtering
8. Data-quality/review inbox for ambiguous duplicates and rule conflicts
9. CSV/XLSX/JSON export endpoints and report UI
10. Alembic database migrations

### Later
- Budgets
- calendar heatmap
- modified-z-score category/merchant anomalies
- expected-vs-actual recurring dashboard
- backup/restore and optional encryption

## Validation caveat

The frontend source is complete, but `npm install` could not finish inside the artifact environment because package registry access timed out. Backend and parser behavior were validated locally. Docker/normal development environments with registry access should install the frontend dependencies during build.

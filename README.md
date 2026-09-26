# PhonePe Financial Statement Analyser

Local-first, deterministic PhonePe CSV analyser. **No AI, no cloud APIs, no telemetry.**

## Implemented

- PhonePe CSV header detection (metadata before the real header is fine)
- Footer/non-transaction row rejection
- Exact Decimal → integer-paise money parsing
- Transaction ID + UTR preserved as TEXT
- Description grammar for `Paid to`, `Received from`, `Payment to`, `Transfer to`, `Refund from`, mobile recharge and roaming pack
- Direction-consistency warning
- Transaction-ID deduplication across overlapping statements
- Import preview and genuinely reversible import batches (rolled-back rows can be restored)
- SQLite/WAL storage
- Payment-instrument normalization table
- Deterministic category rules; unmatched credits stay Uncategorized instead of being guessed as income/receipts
- Dashboard totals, categories, counterparties and daily spending
- Transaction search/filtering
- Recurring/frequent payment **and receipt** pattern detection using amount-stream clustering, cadence, calendar-month signal and median/MAD amount stability
- Responsive desktop/tablet/mobile UI with mobile transaction cards and bottom navigation
- **Single-container** Docker deployment: React is built into the FastAPI image

## Run with Docker

The production build uses **one container**. A multi-stage Dockerfile builds the React frontend, copies it into the Python runtime image, and FastAPI serves both the UI and `/api/*`.

```bash
docker compose up -d --build
```

Open: `http://SERVER_IP:8088`

Persistent database: `./data/phonepe.db`

Container layout:

```text
phonepe-analyser
├── FastAPI API        /api/*
├── React UI           /*
└── SQLite             /data/phonepe.db
```

No nginx, supervisor, or second frontend container is required.

## Local development

Backend:

```bash
cd backend
pip install -r requirements.txt
PHONEPE_DB_PATH=./phonepe.db uvicorn app.main:app --reload
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`.

## Tests

```bash
cd backend
PYTHONPATH=. python -m unittest discover -s tests -v
```

The included fixture is sanitized. Real user statements are **not** bundled into this repository.

## Current limitations / next implementation phases

This is the working P0/P1 foundation, not the final feature-complete product. Next product phases should add:

1. Editable rule CRUD + rule tester/preview
2. Manual category editing from the transaction UI
3. Probable/fuzzy refund review beyond the conservative exact matcher
4. Explicit own-account/self-transfer matching UI
5. Monthly/category trend screens, heatmap and anomaly detection
6. Budgets, report exports and backup/restore UI
7. Audit log and data-quality review queue

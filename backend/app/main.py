from __future__ import annotations

import os
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from .database import Base, SessionLocal, engine, get_db
from .models import AccountInstrument, Category, ImportBatch, Rule, Transaction
from .migrations import migrate_transaction_identity
from .parser import ParsedTransaction, parse_phonepe_csv
from .patterns import detect_patterns
from .rules import apply_category_rules, get_uncategorized_id, load_category_rules
from .relationships import router as relationships_router
from .seed import seed_defaults

APP_VERSION = "0.2.0"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

app = FastAPI(title="PhonePe Analyser", version=APP_VERSION)
app.include_router(relationships_router)

migrate_transaction_identity(engine)
Base.metadata.create_all(engine)
with SessionLocal() as _db:
    seed_defaults(_db)


def money(paise: int) -> float:
    # API convenience only. Accounting/storage stays integer paise.
    return round(paise / 100, 2)


def mask_from_instrument(raw: str) -> str | None:
    match = re.search(r"([Xx*]+\d{2,8}|\d{4,})$", raw.strip())
    return match.group(1) if match else None


def _active_transactions(db: Session, *, with_category: bool = False) -> list[Transaction]:
    stmt = select(Transaction).where(Transaction.is_deleted.is_(False))
    if with_category:
        stmt = stmt.options(selectinload(Transaction.category))
    return list(db.scalars(stmt).all())


def _match_refund_pairs(transactions: list[Transaction]) -> tuple[set[int], int, int]:
    """Return IDs of exact refund/debit pairs plus matched/unmatched refund totals.

    Exact matching is deliberately conservative: same normalized counterparty,
    same amount, refund after debit, within 90 days. Uncertain refunds remain
    visible and never silently erase spending.
    """
    debit_candidates: dict[tuple[str | None, int], list[Transaction]] = defaultdict(list)
    matched_ids: set[int] = set()
    matched_total = 0
    unmatched_total = 0

    for tx in sorted(transactions, key=lambda item: item.txn_datetime):
        if tx.is_self_transfer:
            continue
        key = (tx.counterparty_normalized, tx.amount_paise)
        if tx.direction == "DEBIT":
            debit_candidates[key].append(tx)
            continue
        if tx.operation != "REFUND":
            continue

        candidates = debit_candidates.get(key, [])
        match = None
        for candidate in reversed(candidates):
            if candidate.id in matched_ids:
                continue
            age = tx.txn_datetime - candidate.txn_datetime
            if timedelta(0) <= age <= timedelta(days=90):
                match = candidate
                break

        if match is not None:
            matched_ids.add(match.id)
            matched_ids.add(tx.id)
            matched_total += tx.amount_paise
        else:
            unmatched_total += tx.amount_paise

    return matched_ids, matched_total, unmatched_total


def _effective_transactions(db: Session) -> tuple[list[Transaction], dict[str, int]]:
    active = _active_transactions(db, with_category=True)
    non_self = [tx for tx in active if not tx.is_self_transfer]
    matched_ids, matched_refunds, unmatched_refunds = _match_refund_pairs(non_self)
    effective = [tx for tx in non_self if tx.id not in matched_ids and tx.operation != "REFUND"]
    return effective, {
        "active_count": len(active),
        "matched_refund_paise": matched_refunds,
        "unmatched_refund_paise": unmatched_refunds,
        "refund_paise": matched_refunds + unmatched_refunds,
    }


def _instrument_cache(db: Session, parsed: list[ParsedTransaction]) -> dict[str, AccountInstrument]:
    raw_values = sorted({item.instrument_raw for item in parsed if item.instrument_raw})
    if not raw_values:
        return {}
    existing = db.scalars(select(AccountInstrument).where(AccountInstrument.raw_value.in_(raw_values))).all()
    cache = {item.raw_value: item for item in existing}
    for raw in raw_values:
        if raw not in cache:
            instrument = AccountInstrument(raw_value=raw, mask=mask_from_instrument(raw))
            db.add(instrument)
            cache[raw] = instrument
    db.flush()
    return cache


def _parsed_identity(tx: ParsedTransaction) -> tuple[str, str, int, str]:
    return (
        tx.transaction_id,
        tx.direction,
        tx.amount_paise,
        tx.utr,
    )


def _stored_identity(tx: Transaction) -> tuple[str, str, int, str]:
    return (
        tx.transaction_id,
        tx.direction,
        tx.amount_paise,
        tx.utr,
    )


def _copy_parsed_fields(
    tx: Transaction,
    parsed: ParsedTransaction,
    *,
    import_id: int,
    instrument_id: int | None,
) -> None:
    tx.import_id = import_id
    tx.account_instrument_id = instrument_id
    tx.txn_datetime = parsed.txn_datetime
    tx.direction = parsed.direction
    tx.operation = parsed.operation
    tx.amount_paise = parsed.amount_paise
    tx.description_raw = parsed.description_raw
    tx.counterparty_raw = parsed.counterparty_raw
    tx.counterparty_normalized = parsed.counterparty_normalized
    tx.utr = parsed.utr
    tx.instrument_raw = parsed.instrument_raw
    tx.is_deleted = False


@app.get("/api/health")
def health():
    return {"ok": True, "app": "PhonePe Analyser", "version": APP_VERSION}


async def _read_upload(file: UploadFile) -> bytes:
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "CSV is larger than the 50 MB import limit")
    return data


@app.post("/api/imports/preview")
async def preview_import(file: UploadFile = File(...), db: Session = Depends(get_db)):
    data = await _read_upload(file)
    try:
        parsed = parse_phonepe_csv(data, file.filename or "statement.csv")
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc

    txids = sorted({tx.transaction_id for tx in parsed.transactions})
    existing = db.scalars(
        select(Transaction).where(
            Transaction.provider == "PHONEPE",
            Transaction.transaction_id.in_(txids),
        )
    ).all() if txids else []

    parsed_keys = {_parsed_identity(tx) for tx in parsed.transactions}
    active_keys = {
        _stored_identity(tx)
        for tx in existing
        if not tx.is_deleted
    }
    restorable_keys = {
        _stored_identity(tx)
        for tx in existing
        if tx.is_deleted
    }
    file_duplicate_rows = len(parsed.transactions) - len(parsed_keys)

    result = parsed.to_preview()
    result.update(
        {
            "new_transactions": len(parsed_keys - active_keys - restorable_keys),
            "restorable_transactions": len(parsed_keys & restorable_keys),
            "already_imported": len(parsed_keys & active_keys),
            "duplicate_rows_in_file": file_duplicate_rows,
            "possible_duplicates": 0,
            "sample": [
                {
                    "date": tx.date,
                    "time": tx.time,
                    "description": tx.description_raw,
                    "direction": tx.direction,
                    "operation": tx.operation,
                    "amount": money(tx.amount_paise),
                    "transaction_id": tx.transaction_id,
                    "missing_utr": tx.missing_utr,
                }
                for tx in parsed.transactions[:8]
            ],
        }
    )
    return result


@app.post("/api/imports/commit")
async def commit_import(file: UploadFile = File(...), db: Session = Depends(get_db)):
    data = await _read_upload(file)
    try:
        parsed = parse_phonepe_csv(data, file.filename or "statement.csv")
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc

    batch = ImportBatch(
        filename=parsed.filename,
        sha256=parsed.sha256,
        statement_start=parsed.statement_start,
        statement_end=parsed.statement_end,
        rows_detected=len(parsed.transactions),
        skipped_rows=parsed.skipped_rows,
    )
    db.add(batch)
    db.flush()

    txids = sorted({item.transaction_id for item in parsed.transactions})
    existing = db.scalars(
        select(Transaction).where(
            Transaction.provider == "PHONEPE",
            Transaction.transaction_id.in_(txids),
        )
    ).all() if txids else []
    existing_by_identity = {_stored_identity(tx): tx for tx in existing}

    instruments = _instrument_cache(db, parsed.transactions)
    category_rules = load_category_rules(db)
    uncategorized_id = get_uncategorized_id(db)

    created_count = 0
    restored_count = 0
    duplicate_count = 0

    for item in parsed.transactions:
        instrument = instruments.get(item.instrument_raw)
        identity = _parsed_identity(item)
        existing_tx = existing_by_identity.get(identity)

        if existing_tx is not None and not existing_tx.is_deleted:
            duplicate_count += 1
            continue

        if existing_tx is not None:
            _copy_parsed_fields(
                existing_tx,
                item,
                import_id=batch.id,
                instrument_id=instrument.id if instrument else None,
            )
            if existing_tx.category_source != "MANUAL":
                apply_category_rules(existing_tx, category_rules, uncategorized_id)
            restored_count += 1
            existing_by_identity[identity] = existing_tx
            continue

        tx = Transaction(
            import_id=batch.id,
            account_instrument_id=instrument.id if instrument else None,
            txn_datetime=item.txn_datetime,
            direction=item.direction,
            operation=item.operation,
            amount_paise=item.amount_paise,
            description_raw=item.description_raw,
            counterparty_raw=item.counterparty_raw,
            counterparty_normalized=item.counterparty_normalized,
            transaction_id=item.transaction_id,
            utr=item.utr,
            instrument_raw=item.instrument_raw,
        )
        db.add(tx)
        apply_category_rules(tx, category_rules, uncategorized_id)
        existing_by_identity[identity] = tx
        created_count += 1

    batch.new_rows = created_count + restored_count
    batch.duplicate_rows = duplicate_count
    db.commit()
    return {
        "import_id": batch.id,
        "new_transactions": created_count,
        "restored_transactions": restored_count,
        "activated_transactions": created_count + restored_count,
        "duplicates": duplicate_count,
        "warnings": parsed.warnings,
    }


@app.get("/api/imports")
def list_imports(db: Session = Depends(get_db)):
    rows = db.scalars(select(ImportBatch).order_by(ImportBatch.created_at.desc())).all()
    return [
        {
            "id": row.id,
            "filename": row.filename,
            "period": [row.statement_start, row.statement_end],
            "rows": row.rows_detected,
            "new": row.new_rows,
            "duplicates": row.duplicate_rows,
            "status": row.status,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


@app.post("/api/imports/{import_id}/rollback")
def rollback_import(import_id: int, db: Session = Depends(get_db)):
    batch = db.get(ImportBatch, import_id)
    if not batch or batch.status == "ROLLED_BACK":
        raise HTTPException(404, "Active import not found")
    txs = db.scalars(
        select(Transaction).where(
            Transaction.import_id == import_id,
            Transaction.is_deleted.is_(False),
        )
    ).all()
    for tx in txs:
        tx.is_deleted = True
    batch.status = "ROLLED_BACK"
    batch.rolled_back_at = datetime.utcnow()
    db.commit()
    return {"rolled_back": len(txs)}


@app.get("/api/categories")
def categories(db: Session = Depends(get_db)):
    rows = db.scalars(
        select(Category).where(Category.active.is_(True)).order_by(Category.name)
    ).all()
    return [{"id": category.id, "name": category.name, "parent_id": category.parent_id} for category in rows]


class TransactionPatch(BaseModel):
    category_id: int | None = None
    notes: str | None = None
    is_self_transfer: bool | None = None


@app.patch("/api/transactions/{transaction_id}")
def patch_transaction(transaction_id: int, body: TransactionPatch, db: Session = Depends(get_db)):
    tx = db.get(Transaction, transaction_id)
    if not tx or tx.is_deleted:
        raise HTTPException(404, "Transaction not found")
    if "category_id" in body.model_fields_set:
        if body.category_id is not None and not db.get(Category, body.category_id):
            raise HTTPException(400, "Unknown category")
        tx.category_id = body.category_id
        tx.category_source = "MANUAL"
        tx.category_rule_id = None
    if "notes" in body.model_fields_set:
        tx.notes = body.notes
    if "is_self_transfer" in body.model_fields_set:
        tx.is_self_transfer = bool(body.is_self_transfer)
    db.commit()
    return {"ok": True}


@app.get("/api/transactions")
def transactions(
    q: str = "",
    direction: str = "",
    category_id: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=10, le=200),
    db: Session = Depends(get_db),
):
    conditions = [Transaction.is_deleted.is_(False)]
    if direction in {"DEBIT", "CREDIT"}:
        conditions.append(Transaction.direction == direction)
    if category_id:
        conditions.append(Transaction.category_id == category_id)
    if q:
        like = f"%{q}%"
        conditions.append(
            or_(
                Transaction.description_raw.ilike(like),
                Transaction.counterparty_raw.ilike(like),
                Transaction.transaction_id.ilike(like),
                Transaction.utr.ilike(like),
            )
        )
    count = db.scalar(select(func.count()).select_from(Transaction).where(*conditions)) or 0
    rows = db.scalars(
        select(Transaction)
        .options(selectinload(Transaction.category))
        .where(*conditions)
        .order_by(Transaction.txn_datetime.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "total": count,
        "page": page,
        "page_size": page_size,
        "items": [serialize_tx(tx) for tx in rows],
    }


def serialize_tx(tx: Transaction):
    return {
        "id": tx.id,
        "datetime": tx.txn_datetime.isoformat(),
        "direction": tx.direction,
        "operation": tx.operation,
        "amount": money(tx.amount_paise),
        "amount_paise": tx.amount_paise,
        "description": tx.description_raw,
        "counterparty": tx.counterparty_raw,
        "normalized_counterparty": tx.counterparty_normalized,
        "transaction_id": tx.transaction_id,
        "utr": tx.utr,
        "instrument": tx.instrument_raw,
        "category": tx.category.name if tx.category else "Uncategorized",
        "category_id": tx.category_id,
        "category_source": tx.category_source,
        "notes": tx.notes,
        "is_self_transfer": tx.is_self_transfer,
    }


def _category_rows(transactions: list[Transaction]) -> list[dict]:
    totals: dict[str, list[int]] = defaultdict(list)
    for tx in transactions:
        if tx.direction != "DEBIT":
            continue
        name = tx.category.name if tx.category else "Uncategorized"
        totals[name].append(tx.amount_paise)
    rows = [
        {"name": name, "transactions": len(amounts), "amount": money(sum(amounts))}
        for name, amounts in totals.items()
    ]
    return sorted(rows, key=lambda row: row["amount"], reverse=True)


@app.get("/api/dashboard")
def dashboard(db: Session = Depends(get_db)):
    effective, meta = _effective_transactions(db)
    debits = [tx for tx in effective if tx.direction == "DEBIT"]
    credits = [tx for tx in effective if tx.direction == "CREDIT"]
    spent_paise = sum(tx.amount_paise for tx in debits)
    received_paise = sum(tx.amount_paise for tx in credits)
    # Unmatched refunds represent real positive cash flow inside the selected history
    # but are kept separate from ordinary receipts/income.
    net_paise = received_paise + meta["unmatched_refund_paise"] - spent_paise

    category_rows = _category_rows(effective)[:8]

    merchant_totals: dict[str, dict[str, int]] = defaultdict(lambda: {"count": 0, "amount": 0})
    for tx in debits:
        name = tx.counterparty_normalized or "Unknown"
        merchant_totals[name]["count"] += 1
        merchant_totals[name]["amount"] += tx.amount_paise
    top_merchants = sorted(
        merchant_totals.items(), key=lambda item: item[1]["amount"], reverse=True
    )[:8]

    daily_totals: dict[str, int] = defaultdict(int)
    for tx in debits:
        daily_totals[tx.txn_datetime.date().isoformat()] += tx.amount_paise
    daily = sorted(daily_totals.items(), key=lambda item: item[0])[-30:]

    return {
        "total_spent": money(spent_paise),
        "total_received": money(received_paise),
        "net_flow": money(net_paise),
        "transaction_count": meta["active_count"],
        "refunds": money(meta["refund_paise"]),
        "matched_refunds": money(meta["matched_refund_paise"]),
        "unmatched_refunds": money(meta["unmatched_refund_paise"]),
        "categories": category_rows,
        "top_merchants": [
            {
                "name": name.title(),
                "count": values["count"],
                "amount": money(values["amount"]),
            }
            for name, values in top_merchants
        ],
        "daily_spending": [{"date": date, "amount": money(amount)} for date, amount in daily],
    }


@app.get("/api/analytics/categories")
def category_analytics(db: Session = Depends(get_db)):
    effective, _ = _effective_transactions(db)
    return _category_rows(effective)


@app.get("/api/analytics/merchants")
def merchant_analytics(db: Session = Depends(get_db)):
    effective, _ = _effective_transactions(db)
    grouped: dict[str, list[int]] = defaultdict(list)
    for tx in effective:
        if tx.counterparty_normalized:
            grouped[tx.counterparty_normalized].append(tx.amount_paise)
    rows = []
    for name, amounts in grouped.items():
        rows.append(
            {
                "name": name.title(),
                "transactions": len(amounts),
                "total": money(sum(amounts)),
                "average": money(int(sum(amounts) / len(amounts))),
            }
        )
    return sorted(rows, key=lambda row: row["total"], reverse=True)[:100]


@app.get("/api/analytics/monthly")
def monthly_analytics(db: Session = Depends(get_db)):
    """Month x category debit totals plus monthly income, for the trend chart."""
    effective, _ = _effective_transactions(db)
    spend_by_month: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    income_by_month: dict[str, int] = defaultdict(int)
    for tx in effective:
        month = tx.txn_datetime.strftime("%Y-%m")
        if tx.direction == "DEBIT":
            name = tx.category.name if tx.category else "Uncategorized"
            spend_by_month[month][name] += tx.amount_paise
        else:
            income_by_month[month] += tx.amount_paise
    all_months = sorted(set(spend_by_month) | set(income_by_month))
    return {
        "months": [
            {
                "month": month,
                "categories": {
                    name: money(paise) for name, paise in spend_by_month.get(month, {}).items()
                },
                "income": money(income_by_month.get(month, 0)),
            }
            for month in all_months
        ]
    }


@app.post("/api/rules/reapply")
def reapply_rules(db: Session = Depends(get_db)):
    """Re-run current category rules against every non-manually-categorized,
    active transaction. Lets rule/pattern edits sweep the existing backlog
    instead of only affecting future imports.
    """
    transactions = db.scalars(
        select(Transaction).where(
            Transaction.is_deleted.is_(False),
            Transaction.category_source != "MANUAL",
        )
    ).all()
    category_rules = load_category_rules(db)
    uncategorized_id = get_uncategorized_id(db)
    changed = 0
    for tx in transactions:
        before = (tx.category_id, tx.category_rule_id)
        apply_category_rules(tx, category_rules, uncategorized_id)
        if (tx.category_id, tx.category_rule_id) != before:
            changed += 1
    db.commit()
    return {"checked": len(transactions), "recategorized": changed}


@app.get("/api/patterns")
def patterns(db: Session = Depends(get_db)):
    return detect_patterns(db)


@app.get("/api/rules")
def rules(db: Session = Depends(get_db)):
    rows = db.scalars(
        select(Rule).options(selectinload(Rule.category)).order_by(Rule.area, Rule.priority.desc())
    ).all()
    return [
        {
            "id": rule.id,
            "area": rule.area,
            "priority": rule.priority,
            "match_field": rule.match_field,
            "match_type": rule.match_type,
            "pattern": rule.pattern,
            "operation": rule.operation,
            "expected_direction": rule.expected_direction,
            "category": rule.category.name if rule.category else None,
            "enabled": rule.enabled,
            "builtin": rule.builtin,
        }
        for rule in rows
    ]


# Serve the compiled React application from the same FastAPI process.
# API routes are registered above this catch-all, so /api/* remains unaffected.
FRONTEND_DIST = Path(os.getenv("PHONEPE_FRONTEND_DIST", "/app/frontend/dist"))
if FRONTEND_DIST.exists():
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="frontend-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def frontend(full_path: str):
        requested = (FRONTEND_DIST / full_path).resolve()
        try:
            requested.relative_to(FRONTEND_DIST.resolve())
        except ValueError:
            raise HTTPException(404, "Not found")

        if full_path and requested.is_file():
            return FileResponse(requested)

        index_file = FRONTEND_DIST / "index.html"
        if index_file.is_file():
            return FileResponse(index_file)

        return JSONResponse({"ok": True, "app": "PhonePe Analyser", "frontend": "not built"})

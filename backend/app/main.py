from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Annotated
from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from .database import Base, engine, get_db, SessionLocal
from .models import AccountInstrument, Category, ImportBatch, Rule, Transaction
from .parser import parse_phonepe_csv
from .patterns import detect_patterns
from .rules import apply_category_rules
from .seed import seed_defaults

app = FastAPI(title="PhonePe Analyser", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])

Base.metadata.create_all(engine)
with SessionLocal() as _db:
    seed_defaults(_db)


def money(paise: int) -> float:
    return round(paise / 100, 2)

def mask_from_instrument(raw: str) -> str | None:
    m = re.search(r"([Xx*]+\d{2,8}|\d{4,})$", raw.strip())
    return m.group(1) if m else None

def get_or_create_instrument(db: Session, raw: str) -> AccountInstrument | None:
    if not raw:
        return None
    obj = db.scalar(select(AccountInstrument).where(AccountInstrument.raw_value == raw))
    if obj:
        return obj
    obj = AccountInstrument(raw_value=raw, mask=mask_from_instrument(raw))
    db.add(obj); db.flush()
    return obj

@app.get("/api/health")
def health():
    return {"ok": True, "app": "PhonePe Analyser", "version": "0.1.0"}

@app.post("/api/imports/preview")
async def preview_import(file: UploadFile = File(...), db: Session = Depends(get_db)):
    data = await file.read()
    try:
        parsed = parse_phonepe_csv(data, file.filename or "statement.csv")
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    txids = [t.transaction_id for t in parsed.transactions]
    existing = set(db.scalars(select(Transaction.transaction_id).where(Transaction.provider == "PHONEPE", Transaction.transaction_id.in_(txids))).all()) if txids else set()
    result = parsed.to_preview()
    result.update({
        "new_transactions": len(txids) - len(existing),
        "already_imported": len(existing),
        "possible_duplicates": 0,
        "sample": [{
            "date": t.date, "time": t.time, "description": t.description_raw,
            "direction": t.direction, "operation": t.operation,
            "amount": money(t.amount_paise), "transaction_id": t.transaction_id,
        } for t in parsed.transactions[:8]],
    })
    return result

@app.post("/api/imports/commit")
async def commit_import(file: UploadFile = File(...), db: Session = Depends(get_db)):
    data = await file.read()
    try:
        parsed = parse_phonepe_csv(data, file.filename or "statement.csv")
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    batch = ImportBatch(
        filename=parsed.filename, sha256=parsed.sha256,
        statement_start=parsed.statement_start, statement_end=parsed.statement_end,
        rows_detected=len(parsed.transactions), skipped_rows=parsed.skipped_rows,
    )
    db.add(batch); db.flush()
    new_count = dup_count = 0
    for p in parsed.transactions:
        exists = db.scalar(select(Transaction.id).where(Transaction.provider == "PHONEPE", Transaction.transaction_id == p.transaction_id))
        if exists:
            dup_count += 1
            continue
        inst = get_or_create_instrument(db, p.instrument_raw)
        tx = Transaction(
            import_id=batch.id, account_instrument_id=inst.id if inst else None,
            txn_datetime=p.txn_datetime, direction=p.direction, operation=p.operation,
            amount_paise=p.amount_paise, description_raw=p.description_raw,
            counterparty_raw=p.counterparty_raw, counterparty_normalized=p.counterparty_normalized,
            transaction_id=p.transaction_id, utr=p.utr, instrument_raw=p.instrument_raw,
        )
        db.add(tx); db.flush(); apply_category_rules(db, tx); new_count += 1
    batch.new_rows = new_count; batch.duplicate_rows = dup_count
    db.commit()
    return {"import_id": batch.id, "new_transactions": new_count, "duplicates": dup_count, "warnings": parsed.warnings}

@app.get("/api/imports")
def list_imports(db: Session = Depends(get_db)):
    rows = db.scalars(select(ImportBatch).order_by(ImportBatch.created_at.desc())).all()
    return [{"id": x.id, "filename": x.filename, "period": [x.statement_start, x.statement_end], "rows": x.rows_detected, "new": x.new_rows, "duplicates": x.duplicate_rows, "status": x.status, "created_at": x.created_at.isoformat()} for x in rows]

@app.post("/api/imports/{import_id}/rollback")
def rollback_import(import_id: int, db: Session = Depends(get_db)):
    batch = db.get(ImportBatch, import_id)
    if not batch or batch.status == "ROLLED_BACK":
        raise HTTPException(404, "Active import not found")
    txs = db.scalars(select(Transaction).where(Transaction.import_id == import_id)).all()
    for tx in txs:
        tx.is_deleted = True
    batch.status = "ROLLED_BACK"; batch.rolled_back_at = datetime.utcnow()
    db.commit()
    return {"rolled_back": len(txs)}

@app.get("/api/categories")
def categories(db: Session = Depends(get_db)):
    rows = db.scalars(select(Category).where(Category.active.is_(True)).order_by(Category.name)).all()
    return [{"id": c.id, "name": c.name, "parent_id": c.parent_id} for c in rows]

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
        tx.category_id = body.category_id; tx.category_source = "MANUAL"; tx.category_rule_id = None
    if "notes" in body.model_fields_set: tx.notes = body.notes
    if "is_self_transfer" in body.model_fields_set: tx.is_self_transfer = bool(body.is_self_transfer)
    db.commit()
    return {"ok": True}

@app.get("/api/transactions")
def transactions(
    q: str = "", direction: str = "", category_id: int | None = None,
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=10, le=200),
    db: Session = Depends(get_db),
):
    conditions = [Transaction.is_deleted.is_(False)]
    if direction in {"DEBIT", "CREDIT"}: conditions.append(Transaction.direction == direction)
    if category_id: conditions.append(Transaction.category_id == category_id)
    if q:
        like = f"%{q}%"
        conditions.append(or_(Transaction.description_raw.ilike(like), Transaction.counterparty_raw.ilike(like), Transaction.transaction_id.ilike(like), Transaction.utr.ilike(like)))
    count = db.scalar(select(func.count()).select_from(Transaction).where(*conditions)) or 0
    rows = db.scalars(select(Transaction).where(*conditions).order_by(Transaction.txn_datetime.desc()).offset((page-1)*page_size).limit(page_size)).all()
    return {"total": count, "page": page, "page_size": page_size, "items": [serialize_tx(x) for x in rows]}

def serialize_tx(tx: Transaction):
    return {
        "id": tx.id, "datetime": tx.txn_datetime.isoformat(), "direction": tx.direction,
        "operation": tx.operation, "amount": money(tx.amount_paise), "amount_paise": tx.amount_paise,
        "description": tx.description_raw, "counterparty": tx.counterparty_raw,
        "normalized_counterparty": tx.counterparty_normalized,
        "transaction_id": tx.transaction_id, "utr": tx.utr, "instrument": tx.instrument_raw,
        "category": tx.category.name if tx.category else "Uncategorized", "category_id": tx.category_id,
        "category_source": tx.category_source, "notes": tx.notes, "is_self_transfer": tx.is_self_transfer,
    }

@app.get("/api/dashboard")
def dashboard(db: Session = Depends(get_db)):
    txs = db.scalars(select(Transaction).where(Transaction.is_deleted.is_(False))).all()
    debits = [t for t in txs if t.direction == "DEBIT" and not t.is_self_transfer]
    refunds = [t for t in txs if t.operation == "REFUND" and not t.is_self_transfer]
    credits = [t for t in txs if t.direction == "CREDIT" and t.operation != "REFUND" and not t.is_self_transfer]
    gross_debit = sum(t.amount_paise for t in debits)
    refund_total = sum(t.amount_paise for t in refunds)
    spent = max(0, gross_debit - refund_total)
    received = sum(t.amount_paise for t in credits)

    cat_rows = db.execute(select(Category.name, func.sum(Transaction.amount_paise)).join(Transaction, Transaction.category_id == Category.id).where(Transaction.is_deleted.is_(False), Transaction.direction == "DEBIT", Transaction.is_self_transfer.is_(False)).group_by(Category.name).order_by(func.sum(Transaction.amount_paise).desc()).limit(8)).all()
    merchant_rows = db.execute(select(Transaction.counterparty_normalized, func.count(Transaction.id), func.sum(Transaction.amount_paise)).where(Transaction.is_deleted.is_(False), Transaction.direction == "DEBIT", Transaction.counterparty_normalized.is_not(None)).group_by(Transaction.counterparty_normalized).order_by(func.sum(Transaction.amount_paise).desc()).limit(8)).all()

    daily = db.execute(select(func.date(Transaction.txn_datetime), func.sum(Transaction.amount_paise)).where(Transaction.is_deleted.is_(False), Transaction.direction == "DEBIT").group_by(func.date(Transaction.txn_datetime)).order_by(func.date(Transaction.txn_datetime).desc()).limit(30)).all()
    daily = list(reversed(daily))
    return {
        "total_spent": money(spent), "total_received": money(received), "net_flow": money(received-spent),
        "transaction_count": len(txs), "refunds": money(refund_total),
        "categories": [{"name": n, "amount": money(v or 0)} for n, v in cat_rows],
        "top_merchants": [{"name": (n or "Unknown").title(), "count": c, "amount": money(v or 0)} for n,c,v in merchant_rows],
        "daily_spending": [{"date": d, "amount": money(v or 0)} for d,v in daily],
    }

@app.get("/api/analytics/categories")
def category_analytics(db: Session = Depends(get_db)):
    rows = db.execute(select(Category.name, func.count(Transaction.id), func.sum(Transaction.amount_paise)).join(Transaction, Transaction.category_id == Category.id).where(Transaction.is_deleted.is_(False), Transaction.direction == "DEBIT").group_by(Category.name).order_by(func.sum(Transaction.amount_paise).desc())).all()
    return [{"name": n, "transactions": c, "amount": money(v or 0)} for n,c,v in rows]

@app.get("/api/analytics/merchants")
def merchant_analytics(db: Session = Depends(get_db)):
    rows = db.execute(select(Transaction.counterparty_normalized, func.count(Transaction.id), func.sum(Transaction.amount_paise), func.avg(Transaction.amount_paise)).where(Transaction.is_deleted.is_(False), Transaction.counterparty_normalized.is_not(None)).group_by(Transaction.counterparty_normalized).order_by(func.sum(Transaction.amount_paise).desc()).limit(100)).all()
    return [{"name": n.title(), "transactions": c, "total": money(v or 0), "average": money(int(a or 0))} for n,c,v,a in rows]

@app.get("/api/patterns")
def patterns(db: Session = Depends(get_db)):
    return detect_patterns(db)

@app.get("/api/rules")
def rules(db: Session = Depends(get_db)):
    rows = db.scalars(select(Rule).order_by(Rule.area, Rule.priority.desc())).all()
    return [{"id": r.id, "area": r.area, "priority": r.priority, "match_field": r.match_field, "match_type": r.match_type, "pattern": r.pattern, "operation": r.operation, "expected_direction": r.expected_direction, "category": r.category.name if r.category else None, "enabled": r.enabled, "builtin": r.builtin} for r in rows]


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

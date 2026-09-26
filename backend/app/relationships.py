from __future__ import annotations

import re
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .database import get_db
from .models import (
    Category,
    CounterpartyAlias,
    CounterpartyProfile,
    Loan,
    LoanPayment,
    Transaction,
)

router = APIRouter(prefix="/api")

RELATIONSHIP_TYPES = {
    "GENERAL",
    "PERSONAL_LENDING",
    "FAMILY",
    "BUSINESS",
    "LENDER",
    "OTHER",
}


def _normalize_name(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().upper()


def _money(paise: int | None) -> float | None:
    if paise is None:
        return None
    return round(paise / 100, 2)


def _to_paise(value: Decimal | None) -> int | None:
    if value is None:
        return None
    return int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _profile_for_name(db: Session, normalized_name: str) -> CounterpartyProfile | None:
    alias = db.scalar(
        select(CounterpartyAlias)
        .options(selectinload(CounterpartyAlias.profile).selectinload(CounterpartyProfile.aliases))
        .where(CounterpartyAlias.normalized_name == normalized_name)
    )
    return alias.profile if alias else None


def _profile_names(profile: CounterpartyProfile) -> list[str]:
    return sorted({alias.normalized_name for alias in profile.aliases})


def _ensure_profile(
    db: Session,
    display_name: str,
    normalized_name: str,
    *,
    relationship_type: str = "GENERAL",
) -> CounterpartyProfile:
    existing = _profile_for_name(db, normalized_name)
    if existing:
        return existing
    profile = CounterpartyProfile(
        display_name=display_name.strip() or normalized_name.title(),
        relationship_type=relationship_type,
    )
    db.add(profile)
    db.flush()
    db.add(CounterpartyAlias(profile_id=profile.id, normalized_name=normalized_name))
    db.flush()
    return profile


def _ledger_rows(db: Session, names: list[str]) -> list[Transaction]:
    if not names:
        return []
    return list(
        db.scalars(
            select(Transaction)
            .options(selectinload(Transaction.category))
            .where(
                Transaction.is_deleted.is_(False),
                Transaction.counterparty_normalized.in_(names),
            )
            .order_by(Transaction.txn_datetime.desc(), Transaction.id.desc())
        ).all()
    )


def _tx_json(tx: Transaction) -> dict:
    return {
        "id": tx.id,
        "datetime": tx.txn_datetime.isoformat(),
        "direction": tx.direction,
        "operation": tx.operation,
        "amount": _money(tx.amount_paise),
        "amount_paise": tx.amount_paise,
        "description": tx.description_raw,
        "counterparty": tx.counterparty_raw,
        "normalized_counterparty": tx.counterparty_normalized,
        "transaction_id": tx.transaction_id,
        "utr": tx.utr,
        "category": tx.category.name if tx.category else "Uncategorized",
        "category_id": tx.category_id,
        "notes": tx.notes,
    }


def _profile_json(profile: CounterpartyProfile | None) -> dict | None:
    if not profile:
        return None
    return {
        "id": profile.id,
        "display_name": profile.display_name,
        "relationship_type": profile.relationship_type,
        "notes": profile.notes,
        "aliases": _profile_names(profile),
    }


@router.get("/counterparties/{name:path}/ledger")
def counterparty_ledger(name: str, db: Session = Depends(get_db)):
    normalized = _normalize_name(name)
    if not normalized:
        raise HTTPException(400, "Counterparty name is required")

    profile = _profile_for_name(db, normalized)
    names = _profile_names(profile) if profile else [normalized]
    rows = _ledger_rows(db, names)

    paid = sum(tx.amount_paise for tx in rows if tx.direction == "DEBIT")
    received = sum(tx.amount_paise for tx in rows if tx.direction == "CREDIT")

    monthly: dict[str, dict[str, int]] = defaultdict(lambda: {"paid": 0, "received": 0, "count": 0})
    categories: dict[str, dict[str, int]] = defaultdict(lambda: {"paid": 0, "received": 0, "count": 0})
    for tx in rows:
        month = tx.txn_datetime.strftime("%Y-%m")
        monthly[month]["count"] += 1
        category = tx.category.name if tx.category else "Uncategorized"
        categories[category]["count"] += 1
        if tx.direction == "DEBIT":
            monthly[month]["paid"] += tx.amount_paise
            categories[category]["paid"] += tx.amount_paise
        else:
            monthly[month]["received"] += tx.amount_paise
            categories[category]["received"] += tx.amount_paise

    category_rows = [
        {
            "name": category,
            "count": values["count"],
            "paid": _money(values["paid"]),
            "received": _money(values["received"]),
        }
        for category, values in categories.items()
    ]
    category_rows.sort(key=lambda item: (item["paid"] or 0) + (item["received"] or 0), reverse=True)

    lending_category = any(
        (tx.category.name if tx.category else "") == "Personal Lending / Interest"
        for tx in rows
    )
    lending_mode = bool(
        (profile and profile.relationship_type == "PERSONAL_LENDING")
        or lending_category
    )

    return {
        "requested_name": normalized,
        "display_name": profile.display_name if profile else (rows[0].counterparty_raw if rows else normalized.title()),
        "profile": _profile_json(profile),
        "aliases": names,
        "relationship_type": profile.relationship_type if profile else "GENERAL",
        "lending_mode": lending_mode,
        "transaction_count": len(rows),
        "total_paid": _money(paid),
        "total_received": _money(received),
        "net_cashflow": _money(received - paid),
        "balance_due_to_you": _money(max(0, paid - received)) if lending_mode else None,
        "monthly": [
            {
                "month": month,
                "paid": _money(values["paid"]),
                "received": _money(values["received"]),
                "net": _money(values["received"] - values["paid"]),
                "count": values["count"],
            }
            for month, values in sorted(monthly.items())
        ],
        "categories": category_rows,
        "transactions": [_tx_json(tx) for tx in rows],
    }


class ProfileCreate(BaseModel):
    display_name: str
    primary_alias: str
    relationship_type: str = "GENERAL"
    notes: str | None = None


class ProfilePatch(BaseModel):
    display_name: str | None = None
    relationship_type: str | None = None
    notes: str | None = None


class AliasCreate(BaseModel):
    alias: str


class CategorizeProfile(BaseModel):
    category_name: str = "Personal Lending / Interest"


def _valid_relationship(value: str) -> str:
    normalized = value.strip().upper()
    if normalized not in RELATIONSHIP_TYPES:
        raise HTTPException(400, f"Unknown relationship type: {value}")
    return normalized


@router.post("/counterparty-profiles")
def create_counterparty_profile(body: ProfileCreate, db: Session = Depends(get_db)):
    alias = _normalize_name(body.primary_alias)
    if not alias:
        raise HTTPException(400, "Primary alias is required")
    if _profile_for_name(db, alias):
        raise HTTPException(409, "That counterparty is already attached to a profile")
    profile = CounterpartyProfile(
        display_name=body.display_name.strip() or alias.title(),
        relationship_type=_valid_relationship(body.relationship_type),
        notes=body.notes,
    )
    db.add(profile)
    db.flush()
    db.add(CounterpartyAlias(profile_id=profile.id, normalized_name=alias))
    db.commit()
    db.refresh(profile)
    profile = db.scalar(
        select(CounterpartyProfile)
        .options(selectinload(CounterpartyProfile.aliases))
        .where(CounterpartyProfile.id == profile.id)
    )
    return _profile_json(profile)


@router.patch("/counterparty-profiles/{profile_id}")
def patch_counterparty_profile(
    profile_id: int,
    body: ProfilePatch,
    db: Session = Depends(get_db),
):
    profile = db.get(CounterpartyProfile, profile_id)
    if not profile:
        raise HTTPException(404, "Counterparty profile not found")
    if "display_name" in body.model_fields_set and body.display_name is not None:
        profile.display_name = body.display_name.strip() or profile.display_name
    if "relationship_type" in body.model_fields_set and body.relationship_type is not None:
        profile.relationship_type = _valid_relationship(body.relationship_type)
    if "notes" in body.model_fields_set:
        profile.notes = body.notes
    db.commit()
    return {"ok": True}


@router.post("/counterparty-profiles/{profile_id}/aliases")
def add_counterparty_alias(
    profile_id: int,
    body: AliasCreate,
    db: Session = Depends(get_db),
):
    profile = db.get(CounterpartyProfile, profile_id)
    if not profile:
        raise HTTPException(404, "Counterparty profile not found")
    alias = _normalize_name(body.alias)
    if not alias:
        raise HTTPException(400, "Alias is required")
    existing = db.scalar(
        select(CounterpartyAlias).where(CounterpartyAlias.normalized_name == alias)
    )
    if existing:
        if existing.profile_id == profile_id:
            return {"ok": True, "alias": alias}
        raise HTTPException(409, "That alias belongs to another profile")
    db.add(CounterpartyAlias(profile_id=profile_id, normalized_name=alias))
    db.commit()
    return {"ok": True, "alias": alias}


@router.delete("/counterparty-profiles/{profile_id}/aliases/{alias_id}")
def delete_counterparty_alias(
    profile_id: int,
    alias_id: int,
    db: Session = Depends(get_db),
):
    alias = db.get(CounterpartyAlias, alias_id)
    if not alias or alias.profile_id != profile_id:
        raise HTTPException(404, "Alias not found")
    count = db.scalar(
        select(CounterpartyAlias.id)
        .where(CounterpartyAlias.profile_id == profile_id)
        .limit(2)
    )
    # Keep at least one alias; profiles without a ledger key are not useful.
    aliases = db.scalars(
        select(CounterpartyAlias).where(CounterpartyAlias.profile_id == profile_id)
    ).all()
    if len(aliases) <= 1:
        raise HTTPException(400, "A profile must keep at least one alias")
    db.delete(alias)
    db.commit()
    return {"ok": True}


@router.post("/counterparty-profiles/{profile_id}/categorize")
def categorize_profile_transactions(
    profile_id: int,
    body: CategorizeProfile,
    db: Session = Depends(get_db),
):
    profile = db.scalar(
        select(CounterpartyProfile)
        .options(selectinload(CounterpartyProfile.aliases))
        .where(CounterpartyProfile.id == profile_id)
    )
    if not profile:
        raise HTTPException(404, "Counterparty profile not found")
    category = db.scalar(select(Category).where(Category.name == body.category_name))
    if not category:
        raise HTTPException(400, "Category not found")
    names = _profile_names(profile)
    rows = db.scalars(
        select(Transaction).where(
            Transaction.is_deleted.is_(False),
            Transaction.counterparty_normalized.in_(names),
        )
    ).all()
    for tx in rows:
        tx.category_id = category.id
        tx.category_source = "MANUAL"
        tx.category_rule_id = None
    db.commit()
    return {"updated": len(rows), "category": category.name}


class LoanCreate(BaseModel):
    name: str
    lender_name: str
    loan_type: str = "HOME_LOAN"
    account_ref: str | None = None
    original_principal: Decimal | None = None
    annual_interest_rate: Decimal | None = None
    emi: Decimal | None = None
    start_date: str | None = None
    term_months: int | None = None
    notes: str | None = None


class LoanPatch(BaseModel):
    name: str | None = None
    loan_type: str | None = None
    account_ref: str | None = None
    original_principal: Decimal | None = None
    annual_interest_rate: Decimal | None = None
    emi: Decimal | None = None
    start_date: str | None = None
    term_months: int | None = None
    notes: str | None = None
    active: bool | None = None


class AllocationPatch(BaseModel):
    interest: Decimal = Decimal("0")
    fees: Decimal = Decimal("0")
    principal: Decimal | None = None
    note: str | None = None


def _loan_profile(db: Session, loan: Loan) -> CounterpartyProfile:
    profile = db.scalar(
        select(CounterpartyProfile)
        .options(selectinload(CounterpartyProfile.aliases))
        .where(CounterpartyProfile.id == loan.profile_id)
    )
    if not profile:
        raise HTTPException(500, "Loan counterparty profile is missing")
    return profile


def _linked_payments(db: Session, loan_id: int) -> list[LoanPayment]:
    return list(
        db.scalars(
            select(LoanPayment)
            .options(selectinload(LoanPayment.transaction))
            .where(LoanPayment.loan_id == loan_id)
            .order_by(LoanPayment.id)
        ).all()
    )


def _loan_summary(db: Session, loan: Loan) -> dict:
    profile = _loan_profile(db, loan)
    payments = _linked_payments(db, loan.id)
    total_paid = sum(payment.transaction.amount_paise for payment in payments)
    principal = sum(payment.principal_paise for payment in payments)
    interest = sum(payment.interest_paise for payment in payments)
    fees = sum(payment.fees_paise for payment in payments)
    unallocated = max(0, total_paid - principal - interest - fees)
    outstanding = None
    if loan.original_principal_paise is not None:
        outstanding = max(0, loan.original_principal_paise - principal)
    return {
        "id": loan.id,
        "name": loan.name,
        "lender": profile.display_name,
        "profile_id": profile.id,
        "aliases": _profile_names(profile),
        "loan_type": loan.loan_type,
        "account_ref": loan.account_ref,
        "original_principal": _money(loan.original_principal_paise),
        "annual_interest_rate": (
            round(loan.annual_interest_bps / 100, 2)
            if loan.annual_interest_bps is not None
            else None
        ),
        "emi": _money(loan.emi_paise),
        "start_date": loan.start_date,
        "term_months": loan.term_months,
        "notes": loan.notes,
        "active": loan.active,
        "payment_count": len(payments),
        "total_paid": _money(total_paid),
        "principal_paid": _money(principal),
        "interest_paid": _money(interest),
        "fees_paid": _money(fees),
        "unallocated": _money(unallocated),
        "outstanding_estimate": _money(outstanding),
    }


@router.get("/loans")
def list_loans(db: Session = Depends(get_db)):
    loans = db.scalars(
        select(Loan).order_by(Loan.active.desc(), Loan.created_at.desc())
    ).all()
    return [_loan_summary(db, loan) for loan in loans]


@router.post("/loans")
def create_loan(body: LoanCreate, db: Session = Depends(get_db)):
    lender_normalized = _normalize_name(body.lender_name)
    if not lender_normalized:
        raise HTTPException(400, "Lender/counterparty is required")
    profile = _ensure_profile(
        db,
        body.lender_name,
        lender_normalized,
        relationship_type="LENDER",
    )
    if profile.relationship_type == "GENERAL":
        profile.relationship_type = "LENDER"

    loan = Loan(
        profile_id=profile.id,
        name=body.name.strip() or f"{profile.display_name} Loan",
        loan_type=body.loan_type.strip().upper() or "LOAN",
        account_ref=body.account_ref,
        original_principal_paise=_to_paise(body.original_principal),
        annual_interest_bps=(
            int((body.annual_interest_rate * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
            if body.annual_interest_rate is not None
            else None
        ),
        emi_paise=_to_paise(body.emi),
        start_date=body.start_date,
        term_months=body.term_months,
        notes=body.notes,
    )
    db.add(loan)
    db.commit()
    db.refresh(loan)
    return _loan_summary(db, loan)


@router.patch("/loans/{loan_id}")
def patch_loan(loan_id: int, body: LoanPatch, db: Session = Depends(get_db)):
    loan = db.get(Loan, loan_id)
    if not loan:
        raise HTTPException(404, "Loan not found")
    fields = body.model_fields_set
    if "name" in fields and body.name is not None:
        loan.name = body.name.strip() or loan.name
    if "loan_type" in fields and body.loan_type is not None:
        loan.loan_type = body.loan_type.strip().upper() or loan.loan_type
    if "account_ref" in fields:
        loan.account_ref = body.account_ref
    if "original_principal" in fields:
        loan.original_principal_paise = _to_paise(body.original_principal)
    if "annual_interest_rate" in fields:
        loan.annual_interest_bps = (
            int((body.annual_interest_rate * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
            if body.annual_interest_rate is not None
            else None
        )
    if "emi" in fields:
        loan.emi_paise = _to_paise(body.emi)
    if "start_date" in fields:
        loan.start_date = body.start_date
    if "term_months" in fields:
        loan.term_months = body.term_months
    if "notes" in fields:
        loan.notes = body.notes
    if "active" in fields and body.active is not None:
        loan.active = body.active
    db.commit()
    return {"ok": True}


def _loan_candidates(db: Session, loan: Loan, limit: int = 250) -> list[Transaction]:
    profile = _loan_profile(db, loan)
    names = _profile_names(profile)
    linked_ids = select(LoanPayment.transaction_id)
    return list(
        db.scalars(
            select(Transaction)
            .options(selectinload(Transaction.category))
            .where(
                Transaction.is_deleted.is_(False),
                Transaction.direction == "DEBIT",
                Transaction.counterparty_normalized.in_(names),
                ~Transaction.id.in_(linked_ids),
            )
            .order_by(Transaction.txn_datetime.desc())
            .limit(limit)
        ).all()
    )


def _maybe_set_loan_category(db: Session, tx: Transaction) -> None:
    if tx.category_source == "MANUAL":
        return
    category = db.scalar(select(Category).where(Category.name == "EMI / Loan"))
    if category:
        tx.category_id = category.id
        tx.category_source = "MANUAL"
        tx.category_rule_id = None


@router.get("/loans/{loan_id}")
def loan_detail(loan_id: int, db: Session = Depends(get_db)):
    loan = db.get(Loan, loan_id)
    if not loan:
        raise HTTPException(404, "Loan not found")
    summary = _loan_summary(db, loan)
    payments = _linked_payments(db, loan.id)
    monthly: dict[str, dict[str, int]] = defaultdict(
        lambda: {"payment": 0, "principal": 0, "interest": 0, "fees": 0, "unallocated": 0, "count": 0}
    )
    payment_rows = []
    for payment in sorted(payments, key=lambda item: item.transaction.txn_datetime, reverse=True):
        tx = payment.transaction
        allocated = payment.principal_paise + payment.interest_paise + payment.fees_paise
        unallocated = max(0, tx.amount_paise - allocated)
        month = tx.txn_datetime.strftime("%Y-%m")
        monthly[month]["payment"] += tx.amount_paise
        monthly[month]["principal"] += payment.principal_paise
        monthly[month]["interest"] += payment.interest_paise
        monthly[month]["fees"] += payment.fees_paise
        monthly[month]["unallocated"] += unallocated
        monthly[month]["count"] += 1
        payment_rows.append(
            {
                "allocation_id": payment.id,
                "transaction": _tx_json(tx),
                "principal": _money(payment.principal_paise),
                "interest": _money(payment.interest_paise),
                "fees": _money(payment.fees_paise),
                "unallocated": _money(unallocated),
                "note": payment.note,
            }
        )

    candidates = _loan_candidates(db, loan)
    summary.update(
        {
            "payments": payment_rows,
            "monthly": [
                {
                    "month": month,
                    "payment": _money(values["payment"]),
                    "principal": _money(values["principal"]),
                    "interest": _money(values["interest"]),
                    "fees": _money(values["fees"]),
                    "unallocated": _money(values["unallocated"]),
                    "count": values["count"],
                }
                for month, values in sorted(monthly.items())
            ],
            "candidates": [_tx_json(tx) for tx in candidates],
        }
    )
    return summary


def _link_transaction(db: Session, loan: Loan, tx: Transaction) -> LoanPayment:
    if tx.is_deleted or tx.direction != "DEBIT":
        raise HTTPException(400, "Only active debit transactions can be linked to a loan")
    profile = _loan_profile(db, loan)
    if tx.counterparty_normalized not in _profile_names(profile):
        raise HTTPException(400, "Transaction does not match this loan's counterparty aliases")
    existing = db.scalar(
        select(LoanPayment).where(LoanPayment.transaction_id == tx.id)
    )
    if existing:
        if existing.loan_id == loan.id:
            return existing
        raise HTTPException(409, "Transaction is already linked to another loan")
    payment = LoanPayment(loan_id=loan.id, transaction_id=tx.id)
    db.add(payment)
    _maybe_set_loan_category(db, tx)
    return payment


@router.post("/loans/{loan_id}/payments/{transaction_id}")
def link_loan_payment(
    loan_id: int,
    transaction_id: int,
    db: Session = Depends(get_db),
):
    loan = db.get(Loan, loan_id)
    tx = db.get(Transaction, transaction_id)
    if not loan or not tx:
        raise HTTPException(404, "Loan or transaction not found")
    payment = _link_transaction(db, loan, tx)
    db.commit()
    return {"ok": True, "allocation_id": payment.id}


@router.post("/loans/{loan_id}/auto-link")
def auto_link_loan_payments(loan_id: int, db: Session = Depends(get_db)):
    loan = db.get(Loan, loan_id)
    if not loan:
        raise HTTPException(404, "Loan not found")
    candidates = _loan_candidates(db, loan, limit=5000)
    for tx in candidates:
        _link_transaction(db, loan, tx)
    db.commit()
    return {"linked": len(candidates)}


@router.patch("/loans/{loan_id}/payments/{transaction_id}")
def allocate_loan_payment(
    loan_id: int,
    transaction_id: int,
    body: AllocationPatch,
    db: Session = Depends(get_db),
):
    payment = db.scalar(
        select(LoanPayment)
        .options(selectinload(LoanPayment.transaction))
        .where(
            LoanPayment.loan_id == loan_id,
            LoanPayment.transaction_id == transaction_id,
        )
    )
    if not payment:
        raise HTTPException(404, "Loan payment link not found")

    interest = _to_paise(body.interest) or 0
    fees = _to_paise(body.fees) or 0
    if interest < 0 or fees < 0:
        raise HTTPException(400, "Interest and fees cannot be negative")
    if body.principal is None:
        principal = payment.transaction.amount_paise - interest - fees
    else:
        principal = _to_paise(body.principal) or 0
    if principal < 0:
        raise HTTPException(400, "Interest + fees exceed the transaction amount")
    if principal + interest + fees > payment.transaction.amount_paise:
        raise HTTPException(400, "Allocation exceeds the transaction amount")

    payment.principal_paise = principal
    payment.interest_paise = interest
    payment.fees_paise = fees
    payment.note = body.note
    db.commit()
    return {
        "ok": True,
        "principal": _money(principal),
        "interest": _money(interest),
        "fees": _money(fees),
        "unallocated": _money(
            payment.transaction.amount_paise - principal - interest - fees
        ),
    }


@router.delete("/loans/{loan_id}/payments/{transaction_id}")
def unlink_loan_payment(
    loan_id: int,
    transaction_id: int,
    db: Session = Depends(get_db),
):
    payment = db.scalar(
        select(LoanPayment).where(
            LoanPayment.loan_id == loan_id,
            LoanPayment.transaction_id == transaction_id,
        )
    )
    if not payment:
        raise HTTPException(404, "Loan payment link not found")
    db.delete(payment)
    db.commit()
    return {"ok": True}

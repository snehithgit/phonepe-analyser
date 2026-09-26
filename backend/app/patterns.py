from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from statistics import median
from sqlalchemy import select
from sqlalchemy.orm import Session
from .models import Transaction

CADENCES = [
    ("DAILY", 1, 2), ("WEEKLY", 5, 9), ("FORTNIGHTLY", 12, 18),
    ("MONTHLY", 25, 35), ("BIMONTHLY", 55, 70), ("QUARTERLY", 80, 100),
    ("HALF_YEARLY", 170, 195), ("YEARLY", 350, 380),
]

def _mad(values: list[float]) -> float:
    if not values:
        return 0.0
    m = median(values)
    return float(median([abs(x - m) for x in values]))

def _cadence(intervals: list[int]) -> tuple[str | None, float]:
    if not intervals:
        return None, 0.0
    best = (None, 0.0)
    for name, low, high in CADENCES:
        ratio = sum(low <= x <= high for x in intervals) / len(intervals)
        if ratio > best[1]:
            best = (name, ratio)
    return best

def detect_patterns(db: Session) -> list[dict]:
    txs = db.scalars(select(Transaction).where(Transaction.is_deleted.is_(False)).order_by(Transaction.txn_datetime)).all()
    groups: dict[tuple[str, str], list[Transaction]] = defaultdict(list)
    for tx in txs:
        if tx.counterparty_normalized and tx.operation != "REFUND":
            groups[(tx.counterparty_normalized, tx.direction)].append(tx)

    out = []
    for (counterparty, direction), items in groups.items():
        if len(items) < 2:
            continue
        items.sort(key=lambda t: t.txn_datetime)
        dates = [x.txn_datetime for x in items]
        amounts = [x.amount_paise for x in items]
        intervals = [(b.date() - a.date()).days for a, b in zip(dates, dates[1:])]
        cadence, cadence_score = _cadence(intervals)
        med_amount = int(median(amounts))
        amount_mad = _mad(amounts)
        amount_stability = 1.0 if med_amount == 0 else max(0.0, 1.0 - (amount_mad / med_amount))
        status = None
        pattern_type = None
        if cadence and cadence_score >= 0.6:
            status = "CONFIRMED" if len(items) >= 3 else "PROBABLE"
            pattern_type = "RECURRING_FIXED" if amount_stability >= 0.85 else "RECURRING_VARIABLE"
        elif len(items) >= 5:
            status = "CONFIRMED"
            pattern_type = "FREQUENT"
        else:
            continue
        next_expected = None
        if cadence and intervals:
            next_expected = (dates[-1].date() + timedelta(days=int(median(intervals)))).isoformat()
        out.append({
            "counterparty": counterparty.title(),
            "direction": direction,
            "pattern_type": pattern_type,
            "status": status,
            "occurrences": len(items),
            "first_seen": dates[0].date().isoformat(),
            "last_seen": dates[-1].date().isoformat(),
            "median_amount_paise": med_amount,
            "amount_mad_paise": int(amount_mad),
            "median_interval_days": int(median(intervals)) if intervals else None,
            "interval_mad_days": round(_mad(intervals), 2),
            "cadence": cadence,
            "cadence_score": round(cadence_score, 3),
            "amount_stability": round(amount_stability, 3),
            "next_expected": next_expected,
        })
    out.sort(key=lambda x: (x["pattern_type"] != "RECURRING_FIXED", -x["occurrences"], x["counterparty"]))
    return out

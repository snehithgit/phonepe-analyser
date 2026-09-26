from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from statistics import median
from sqlalchemy import select
from sqlalchemy.orm import Session
from .models import Transaction

CADENCES = [
    ("DAILY", 1, 2),
    ("WEEKLY", 5, 9),
    ("FORTNIGHTLY", 12, 18),
    ("MONTHLY", 25, 35),
    ("BIMONTHLY", 55, 70),
    ("QUARTERLY", 80, 100),
    ("HALF_YEARLY", 170, 195),
    ("YEARLY", 350, 380),
]


def _mad(values: list[float]) -> float:
    if not values:
        return 0.0
    middle = median(values)
    return float(median([abs(value - middle) for value in values]))


def _calendar_monthly_score(items: list[Transaction]) -> float:
    if len(items) < 2:
        return 0.0
    days = [item.txn_datetime.day for item in items]
    typical = int(median(days))
    # Calendar-month patterns such as rent/salary can move by a few days around
    # weekends and month boundaries while still clearly being monthly.
    return sum(abs(day - typical) <= 3 for day in days) / len(days)


def _cadence(items: list[Transaction], intervals: list[int]) -> tuple[str | None, float]:
    if not intervals:
        return None, 0.0
    best: tuple[str | None, float] = (None, 0.0)
    for name, low, high in CADENCES:
        ratio = sum(low <= value <= high for value in intervals) / len(intervals)
        if ratio > best[1]:
            best = (name, ratio)

    calendar_monthly = _calendar_monthly_score(items)
    if calendar_monthly >= 0.75 and calendar_monthly > best[1]:
        return "MONTHLY", calendar_monthly
    return best


def _amount_clusters(items: list[Transaction]) -> list[list[Transaction]]:
    """Separate clearly different payment streams to the same counterparty.

    This is intentionally conservative. A stream can vary by about 25% (or ₹100,
    whichever is larger) without being split, which preserves variable bills while
    separating materially different fixed obligations to the same payee.
    """
    if len(items) < 3:
        return [items]

    clusters: list[list[Transaction]] = []
    for item in sorted(items, key=lambda tx: tx.amount_paise):
        placed = False
        for cluster in clusters:
            cluster_median = int(median([tx.amount_paise for tx in cluster]))
            tolerance = max(10_000, int(cluster_median * 0.25))
            if abs(item.amount_paise - cluster_median) <= tolerance:
                cluster.append(item)
                placed = True
                break
        if not placed:
            clusters.append([item])
    return clusters


def _classify(items: list[Transaction]) -> dict | None:
    if len(items) < 2:
        return None
    items = sorted(items, key=lambda tx: tx.txn_datetime)
    dates = [item.txn_datetime for item in items]
    amounts = [item.amount_paise for item in items]
    intervals = [(later.date() - earlier.date()).days for earlier, later in zip(dates, dates[1:])]
    cadence, cadence_score = _cadence(items, intervals)
    med_amount = int(median(amounts))
    amount_mad = _mad(amounts)
    amount_stability = 1.0 if med_amount == 0 else max(0.0, 1.0 - (amount_mad / med_amount))

    if cadence and cadence_score >= 0.6:
        status = "CONFIRMED" if len(items) >= 3 else "PROBABLE"
        pattern_type = "RECURRING_FIXED" if amount_stability >= 0.85 else "RECURRING_VARIABLE"
    elif len(items) >= 5:
        status = "CONFIRMED"
        pattern_type = "FREQUENT"
    else:
        return None

    next_expected = None
    if cadence and intervals:
        next_expected = (dates[-1].date() + timedelta(days=int(median(intervals)))).isoformat()

    return {
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
    }


def detect_patterns(db: Session) -> list[dict]:
    txs = db.scalars(
        select(Transaction)
        .where(
            Transaction.is_deleted.is_(False),
            Transaction.is_self_transfer.is_(False),
        )
        .order_by(Transaction.txn_datetime)
    ).all()

    groups: dict[tuple[str, str], list[Transaction]] = defaultdict(list)
    for tx in txs:
        if tx.counterparty_normalized and tx.operation != "REFUND":
            groups[(tx.counterparty_normalized, tx.direction)].append(tx)

    out: list[dict] = []
    for (counterparty, direction), items in groups.items():
        emitted = 0
        clusters = _amount_clusters(items)
        for cluster_index, cluster in enumerate(clusters, start=1):
            candidate = _classify(cluster)
            if candidate is None:
                continue
            emitted += 1
            candidate.update(
                {
                    "counterparty": counterparty.title(),
                    "direction": direction,
                    "stream": cluster_index if len(clusters) > 1 else None,
                }
            )
            out.append(candidate)

        # If conservative amount-clustering fragments a genuinely variable stream,
        # fall back to the complete counterparty+direction series rather than losing it.
        if emitted == 0 and len(clusters) > 1:
            candidate = _classify(items)
            if candidate is not None:
                candidate.update(
                    {
                        "counterparty": counterparty.title(),
                        "direction": direction,
                        "stream": None,
                    }
                )
                out.append(candidate)

    out.sort(
        key=lambda item: (
            item["pattern_type"] != "RECURRING_FIXED",
            -item["occurrences"],
            item["counterparty"],
            item.get("stream") or 0,
        )
    )
    return out

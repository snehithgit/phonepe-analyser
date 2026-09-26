from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

REQUIRED_HEADERS = [
    "Date", "Time", "Transaction Details", "Transaction ID", "UTR",
    "Transaction Type", "Credit/debit instrument", "Amount",
]

DESCRIPTION_RULES = [
    (re.compile(r"^Refund from\s+(.+)$", re.I), "REFUND", "CREDIT"),
    (re.compile(r"^Received from\s+(.+)$", re.I), "RECEIVED", "CREDIT"),
    (re.compile(r"^Paid to\s+(.+)$", re.I), "PAYMENT", "DEBIT"),
    (re.compile(r"^Payment to\s+(.+)$", re.I), "PAYMENT", "DEBIT"),
    (re.compile(r"^Transfer to\s+(.+)$", re.I), "TRANSFER", "DEBIT"),
    (re.compile(r"^Mobile recharged\s*(.*)$", re.I), "RECHARGE", "DEBIT"),
    (re.compile(r"^International Roaming Pack for\s+(.+)$", re.I), "ROAMING", "DEBIT"),
    (re.compile(r"^Paid - Mobile Recharge$", re.I), "RECHARGE", "DEBIT"),
    (re.compile(r"^Bill paid - (.+)$", re.I), "BILL_PAYMENT", "DEBIT"),
    (re.compile(r"^Bill paid$", re.I), "BILL_PAYMENT", "DEBIT"),
]


@dataclass
class ParsedTransaction:
    txn_datetime: datetime
    date: str
    time: str
    direction: str
    operation: str
    amount_paise: int
    description_raw: str
    counterparty_raw: str | None
    counterparty_normalized: str | None
    transaction_id: str
    utr: str
    instrument_raw: str
    direction_conflict: bool = False
    missing_utr: bool = False


@dataclass
class ParsedStatement:
    filename: str
    sha256: str
    statement_start: str | None
    statement_end: str | None
    transactions: list[ParsedTransaction]
    skipped_rows: int
    warnings: list[str]
    missing_utr_rows: int = 0

    def to_preview(self) -> dict:
        debit = sum(t.amount_paise for t in self.transactions if t.direction == "DEBIT")
        credit = sum(t.amount_paise for t in self.transactions if t.direction == "CREDIT")
        return {
            "filename": self.filename,
            "sha256": self.sha256,
            "statement_start": self.statement_start,
            "statement_end": self.statement_end,
            "transactions_found": len(self.transactions),
            "skipped_rows": self.skipped_rows,
            "missing_utr_rows": self.missing_utr_rows,
            "warnings": self.warnings,
            "total_debit_paise": debit,
            "total_credit_paise": credit,
        }


def normalize_counterparty(value: str | None) -> str | None:
    if not value:
        return None
    value = re.sub(r"\s+", " ", value).strip()
    return value.upper()


def parse_description(description: str, csv_direction: str) -> tuple[str, str | None, bool]:
    text = re.sub(r"\s+", " ", description).strip()
    for regex, operation, expected in DESCRIPTION_RULES:
        match = regex.match(text)
        if match:
            captured = match.group(1).strip() if match.lastindex and match.group(1).strip() else None
            return operation, captured, expected != csv_direction
    return "OTHER", text or None, False


def rupees_to_paise(value: str) -> int:
    cleaned = value.replace("₹", "").replace(",", "").strip()
    try:
        amount = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"invalid amount: {value!r}") from exc
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _find_header(lines: list[str]) -> int:
    for index, line in enumerate(lines):
        try:
            row = next(csv.reader([line]))
        except csv.Error:
            continue
        if all(header in row for header in REQUIRED_HEADERS):
            return index
    raise ValueError("PhonePe transaction header not found")


DURATION_DATE_FORMATS = (
    "%d %b, %Y",  # newer export: 01 Apr, 2026
    "%d %b %Y",   # older export: 09 Jun 2017
    "%Y-%m-%d",
)

TRANSACTION_DATETIME_FORMATS = (
    "%b %d, %Y %I:%M %p",  # newer export: Apr 01, 2026 03:32 PM
    "%Y-%m-%d %H:%M",       # older export: 2020-12-24 15:32
    "%Y-%m-%d %I:%M %p",
)


def _parse_date_value(value: str) -> str | None:
    value = value.strip()
    for fmt in DURATION_DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _parse_duration(lines: list[str], header_index: int) -> tuple[str | None, str | None]:
    for line in lines[:header_index]:
        if line.lower().startswith("duration"):
            row = next(csv.reader([line]))
            if len(row) > 1 and " - " in row[1]:
                left, right = row[1].split(" - ", 1)
                start = _parse_date_value(left)
                end = _parse_date_value(right)
                if start and end:
                    return start, end
    return None, None


def _parse_transaction_datetime(date_value: str, time_value: str) -> datetime:
    combined = f"{date_value.strip()} {time_value.strip()}"
    for fmt in TRANSACTION_DATETIME_FORMATS:
        try:
            return datetime.strptime(combined, fmt)
        except ValueError:
            continue
    raise ValueError(
        f"unsupported PhonePe date/time format: date={date_value!r}, time={time_value!r}"
    )


def _looks_like_empty_or_footer(row: dict[str, str | None]) -> bool:
    values = [str(value or "").strip() for value in row.values()]
    if not any(values):
        return True
    direction = (row.get("Transaction Type") or "").strip().upper()
    txid = (row.get("Transaction ID") or "").strip()
    date = (row.get("Date") or "").strip()
    amount = (row.get("Amount") or "").strip()
    description = (row.get("Transaction Details") or "").strip()
    # PhonePe footer/disclaimer rows do not resemble transaction rows.
    return direction not in {"DEBIT", "CREDIT"} and not txid and not (date and amount and description)


def parse_phonepe_csv(data: bytes, filename: str) -> ParsedStatement:
    digest = hashlib.sha256(data).hexdigest()
    text = data.decode("utf-8-sig", errors="strict")
    lines = text.splitlines()
    header_index = _find_header(lines)
    start, end = _parse_duration(lines, header_index)
    reader = csv.DictReader(io.StringIO("\n".join(lines[header_index:])))

    transactions: list[ParsedTransaction] = []
    skipped = 0
    warnings: list[str] = []
    missing_utr_rows = 0

    for row_number, row in enumerate(reader, start=header_index + 2):
        if _looks_like_empty_or_footer(row):
            skipped += 1
            continue

        direction = (row.get("Transaction Type") or "").strip().upper()
        txid = (row.get("Transaction ID") or "").strip()
        utr = (row.get("UTR") or "").strip()

        if direction not in {"DEBIT", "CREDIT"} or not txid:
            skipped += 1
            warnings.append(
                f"Skipped malformed transaction-like row {row_number}: "
                f"missing valid direction or Transaction ID"
            )
            continue

        try:
            dt = _parse_transaction_datetime(
                row.get("Date") or "",
                row.get("Time") or "",
            )
            amount_paise = rupees_to_paise(row.get("Amount") or "")
        except (ValueError, TypeError) as exc:
            skipped += 1
            warnings.append(f"Skipped malformed row for transaction {txid}: {exc}")
            continue

        description = (row.get("Transaction Details") or "").strip()
        operation, counterparty, conflict = parse_description(description, direction)
        if conflict:
            warnings.append(f"Direction conflict for {txid}: description suggests opposite direction")
        missing_utr = not bool(utr)
        if missing_utr:
            missing_utr_rows += 1

        transactions.append(
            ParsedTransaction(
                txn_datetime=dt,
                date=dt.date().isoformat(),
                time=dt.time().isoformat(timespec="minutes"),
                direction=direction,
                operation=operation,
                amount_paise=amount_paise,
                description_raw=description,
                counterparty_raw=counterparty,
                counterparty_normalized=normalize_counterparty(counterparty),
                transaction_id=txid,
                utr=utr,  # intentionally TEXT; blank is allowed and leading zeroes are preserved
                instrument_raw=(row.get("Credit/debit instrument") or "").strip(),
                direction_conflict=conflict,
                missing_utr=missing_utr,
            )
        )

    if skipped:
        warnings.insert(0, f"Skipped {skipped} footer/empty or malformed rows")
    if missing_utr_rows:
        warnings.append(
            f"Kept {missing_utr_rows} valid transaction(s) without a UTR; "
            "Transaction ID remains the primary identifier"
        )

    return ParsedStatement(
        filename,
        digest,
        start,
        end,
        transactions,
        skipped,
        warnings,
        missing_utr_rows,
    )

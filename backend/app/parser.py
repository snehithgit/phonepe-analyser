from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass, asdict
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Iterable

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

@dataclass
class ParsedStatement:
    filename: str
    sha256: str
    statement_start: str | None
    statement_end: str | None
    transactions: list[ParsedTransaction]
    skipped_rows: int
    warnings: list[str]

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
        m = regex.match(text)
        if m:
            captured = m.group(1).strip() if m.lastindex and m.group(1).strip() else None
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
    for i, line in enumerate(lines):
        try:
            row = next(csv.reader([line]))
        except csv.Error:
            continue
        if all(h in row for h in REQUIRED_HEADERS):
            return i
    raise ValueError("PhonePe transaction header not found")


def _parse_duration(lines: list[str], header_index: int) -> tuple[str | None, str | None]:
    for line in lines[:header_index]:
        if line.lower().startswith("duration"):
            row = next(csv.reader([line]))
            if len(row) > 1 and " - " in row[1]:
                left, right = row[1].split(" - ", 1)
                try:
                    a = datetime.strptime(left.strip(), "%d %b, %Y").date().isoformat()
                    b = datetime.strptime(right.strip(), "%d %b, %Y").date().isoformat()
                    return a, b
                except ValueError:
                    pass
    return None, None


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

    for row in reader:
        direction = (row.get("Transaction Type") or "").strip().upper()
        txid = (row.get("Transaction ID") or "").strip()
        utr = (row.get("UTR") or "").strip()
        if direction not in {"DEBIT", "CREDIT"} or not txid or not utr:
            skipped += 1
            continue
        try:
            dt = datetime.strptime(
                f"{(row.get('Date') or '').strip()} {(row.get('Time') or '').strip()}",
                "%b %d, %Y %I:%M %p",
            )
            amount_paise = rupees_to_paise(row.get("Amount") or "")
        except (ValueError, TypeError) as exc:
            skipped += 1
            warnings.append(f"Skipped malformed row for transaction {txid or '<unknown>'}: {exc}")
            continue

        description = (row.get("Transaction Details") or "").strip()
        operation, counterparty, conflict = parse_description(description, direction)
        if conflict:
            warnings.append(f"Direction conflict for {txid}: description suggests opposite direction")
        transactions.append(ParsedTransaction(
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
            utr=utr,  # intentionally TEXT; leading zeroes preserved
            instrument_raw=(row.get("Credit/debit instrument") or "").strip(),
            direction_conflict=conflict,
        ))

    if skipped:
        warnings.insert(0, f"Skipped {skipped} non-transaction/footer or malformed rows")
    return ParsedStatement(filename, digest, start, end, transactions, skipped, warnings)

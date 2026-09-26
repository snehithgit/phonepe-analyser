from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session
from .models import Category, Rule

CATEGORY_NAMES = [
    "Food & Dining", "Groceries", "Medical & Hospital", "Entertainment", "Travel",
    "Transport", "Shopping", "Bills & Utilities", "Mobile & Recharge", "Education",
    "Home & Household", "Personal Care", "Insurance", "EMI / Loan", "Rent",
    "Subscriptions", "Family / Personal Transfers", "Personal Lending / Interest",
    "Investments", "Business", "Refunds", "Receipts", "Uncategorized",
]

# Patterns are matched CONTAINS against counterparty_normalized. Longer/more specific
# patterns are always tried before shorter ones (see priority assignment below), so
# e.g. "SWIGGY INSTAMART" (Groceries) is never shadowed by the shorter "SWIGGY" (Food).
CATEGORY_RULES = {
    "Food & Dining": [
        "DOMINOS", "PIZZA", "MOMO", "TIFFIN", "FOODS", "CHICKEN PAKODI", "PASTRY",
        "LASSI", "CATERING", "SWEETS", "ROLLS AND SOUPS", "SWIGGY", "ZOMATO",
        "RESTAURANT", "BAKERY", "NOODLES", "CURRY", "BIRYANI", "DHABA", "CAFE",
        "EAT AND SIP", "EAT & SIP", "KFC", "MCDONALD",
    ],
    "Groceries": [
        "SWIGGY INSTAMART", "BLINKIT", "KIRANA", "WHOLE SALE FRUITS",
        "AVENUE SUPERMARTS", "SUPERMARTS", "GENERAL STORE", "SUPERMARKET",
    ],
    "Medical & Hospital": [
        "APOLLO PHARMACY", "PHARMACY", "HOSPITAL", "CLINIC", "DIAGNOSTIC",
        "MEDICAL", "CHEMISTS",
    ],
    "Entertainment": [
        "PVR", "INOX", "PLAYMORE", "JUBILANT ADVENTURES", "NETFLIX", "SPOTIFY",
        "BOOKMYSHOW", "PRIME VIDEO", "HOTSTAR",
    ],
    "Transport": [
        "AUTOMOBILES", "PETROL", "DIESEL", "FUEL", "FASTAG", "UBER", "OLA",
        "RAPIDO", "IRCTC", "METRO",
    ],
    "Shopping": ["AMAZON", "FLIPKART", "SHOES", "MYNTRA", "AJIO", "MEESHO", "NYKAA"],
    "Bills & Utilities": [
        "APEPDCL", "ELECTRICITY", "WATER BILL", "GAS BILL", "LPG", "BROADBAND",
        "WATER SUPPLY", "DTH", "TATA PLAY", "GAS AGENCY", "POWER DISTRIBUTION",
    ],
    "Mobile & Recharge": [
        "RECHARGE", "RELIANCE JIO INFOCOMM", "JIO MOBILITY", "BHARTI AIRTEL",
        "AIRTEL PAYMENTS BANK", "VODAFONE IDEA", "WWW AIRTEL IN",
    ],
    "Education": ["BOOK AND STATIONERY", "BOOKS", "STATIONERY"],
    "Personal Care": ["HAIR STYLE", "SALOON", "SALON"],
    "EMI / Loan": [
        "CREDIT CARD", "BOBCARD", "ONECARD", "HOUSING FINANCE", "NBFC",
        "MONEYVIEW", "KREDITBEE",
    ],
    "Insurance": [
        "INSURANCE", "GO DIGIT", "HDFC ERGO", "STAR HEALTH", "BAJAJ ALLIANZ",
        "POLICYBAZAAR",
    ],
    "Investments": ["ZERODHA", "GROWW", "UPSTOX", "ICICI DIRECT", "ANGEL ONE", "ANGEL BROKING"],
}

DESCRIPTION_SEEDS = [
    (100, r"^Refund from\s+(.+)$", "REFUND", "CREDIT"),
    (95, r"^Received from\s+(.+)$", "RECEIVED", "CREDIT"),
    (90, r"^Paid to\s+(.+)$", "PAYMENT", "DEBIT"),
    (85, r"^Payment to\s+(.+)$", "PAYMENT", "DEBIT"),
    (80, r"^Transfer to\s+(.+)$", "TRANSFER", "DEBIT"),
    (75, r"^Mobile recharged\s*(.*)$", "RECHARGE", "DEBIT"),
    (70, r"^International Roaming Pack for\s+(.+)$", "ROAMING", "DEBIT"),
    # Mirrors parser.py DESCRIPTION_RULES for accurate display on the Rules page.
    # Parsing itself is driven by parser.py, not by these rows.
    (65, r"^Paid - Mobile Recharge$", "RECHARGE", "DEBIT"),
    (64, r"^Bill paid - (.+)$", "BILL_PAYMENT", "DEBIT"),
    (63, r"^Bill paid$", "BILL_PAYMENT", "DEBIT"),
]


def _rule_exists(db: Session, *, area: str, match_field: str, match_type: str, pattern: str) -> bool:
    return db.scalar(
        select(Rule.id).where(
            Rule.area == area,
            Rule.match_field == match_field,
            Rule.match_type == match_type,
            Rule.pattern == pattern,
        ).limit(1)
    ) is not None


def _upsert_category_rule(
    db: Session,
    *,
    priority: int,
    pattern: str,
    category_id: int,
) -> None:
    """Insert a builtin CATEGORY rule, or fix its priority/category if it already
    exists. Builtin rules are re-derived from CATEGORY_RULES on every startup so a
    previously-seeded database converges to the current spec (e.g. picks up a
    reordered priority) instead of only ever gaining new rows.
    """
    existing = db.scalar(
        select(Rule).where(
            Rule.area == "CATEGORY",
            Rule.match_field == "counterparty_normalized",
            Rule.match_type == "CONTAINS",
            Rule.pattern == pattern,
        )
    )
    if existing is None:
        db.add(
            Rule(
                area="CATEGORY",
                priority=priority,
                match_field="counterparty_normalized",
                match_type="CONTAINS",
                pattern=pattern,
                category_id=category_id,
                enabled=True,
                builtin=True,
            )
        )
        return
    if not existing.builtin:
        # A user edited/took ownership of this pattern; leave it alone.
        return
    if existing.priority != priority:
        existing.priority = priority
    if existing.category_id != category_id:
        existing.category_id = category_id


def seed_defaults(db: Session):
    existing = {c.name: c for c in db.scalars(select(Category)).all()}
    for name in CATEGORY_NAMES:
        if name not in existing:
            category = Category(name=name)
            db.add(category)
            db.flush()
            existing[name] = category

    # Remove the legacy catch-all CREDIT -> Receipts seed. Incoming money remains
    # uncategorized until a more specific deterministic or manual rule classifies it.
    legacy_credit_rules = db.scalars(
        select(Rule).where(
            Rule.area == "CATEGORY",
            Rule.builtin.is_(True),
            Rule.match_field == "direction",
            Rule.match_type == "EXACT",
            Rule.pattern == "CREDIT",
        )
    ).all()
    for rule in legacy_credit_rules:
        db.delete(rule)

    for priority, pattern, operation, expected in DESCRIPTION_SEEDS:
        if not _rule_exists(
            db,
            area="DESCRIPTION",
            match_field="description_raw",
            match_type="REGEX",
            pattern=pattern,
        ):
            db.add(
                Rule(
                    area="DESCRIPTION",
                    priority=priority,
                    match_field="description_raw",
                    match_type="REGEX",
                    pattern=pattern,
                    operation=operation,
                    expected_direction=expected,
                    enabled=True,
                    builtin=True,
                )
            )

    # Longer/more specific patterns must always be tried before shorter ones that
    # could otherwise shadow them (e.g. "SWIGGY INSTAMART" before "SWIGGY"), so
    # priority is derived from pattern length rather than dict insertion order.
    all_patterns = [
        (category_name, pattern)
        for category_name, patterns in CATEGORY_RULES.items()
        for pattern in patterns
    ]
    all_patterns.sort(key=lambda item: (-len(item[1]), item[1], item[0]))
    priority = 500
    for category_name, pattern in all_patterns:
        _upsert_category_rule(
            db,
            priority=priority,
            pattern=pattern,
            category_id=existing[category_name].id,
        )
        priority -= 1

    operation_rules = [
        (1000, "REFUND", "Refunds"),
        (990, "RECHARGE", "Mobile & Recharge"),
        (989, "ROAMING", "Mobile & Recharge"),
    ]
    for priority, operation, category_name in operation_rules:
        if not _rule_exists(
            db,
            area="CATEGORY",
            match_field="operation",
            match_type="EXACT",
            pattern=operation,
        ):
            db.add(
                Rule(
                    area="CATEGORY",
                    priority=priority,
                    match_field="operation",
                    match_type="EXACT",
                    pattern=operation,
                    category_id=existing[category_name].id,
                    enabled=True,
                    builtin=True,
                )
            )

    db.commit()

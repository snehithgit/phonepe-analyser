from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session
from .models import Category, Rule

CATEGORY_NAMES = [
    "Food & Dining", "Groceries", "Medical & Hospital", "Entertainment", "Travel",
    "Transport", "Shopping", "Bills & Utilities", "Mobile & Recharge", "Education",
    "Home & Household", "Personal Care", "Insurance", "EMI / Loan", "Rent",
    "Subscriptions", "Family / Personal Transfers", "Business", "Refunds",
    "Receipts", "Uncategorized",
]

CATEGORY_RULES = {
    "Food & Dining": ["DOMINOS", "PIZZA", "MOMO", "TIFFIN", "FOODS", "CHICKEN PAKODI", "PASTRY", "LASSI", "CATERING", "SWEETS", "ROLLS AND SOUPS"],
    "Groceries": ["SWIGGY INSTAMART", "BLINKIT", "KIRANA", "WHOLE SALE FRUITS"],
    "Medical & Hospital": ["APOLLO PHARMACY", "PHARMACY", "HOSPITAL", "CLINIC", "DIAGNOSTIC", "MEDICAL"],
    "Entertainment": ["PVR", "INOX", "PLAYMORE", "JUBILANT ADVENTURES", "NETFLIX", "SPOTIFY"],
    "Transport": ["AUTOMOBILES", "PETROL", "DIESEL", "FUEL", "FASTAG", "UBER", "OLA"],
    "Shopping": ["AMAZON", "FLIPKART", "SHOES"],
    "Bills & Utilities": ["APEPDCL", "ELECTRICITY", "WATER BILL", "GAS BILL"],
    "Education": ["BOOK AND STATIONERY", "BOOKS", "STATIONERY"],
    "Personal Care": ["HAIR STYLE", "SALOON", "SALON"],
    "EMI / Loan": ["CREDIT CARD", "BOBCARD"],
}

DESCRIPTION_SEEDS = [
    (100, r"^Refund from\s+(.+)$", "REFUND", "CREDIT"),
    (95, r"^Received from\s+(.+)$", "RECEIVED", "CREDIT"),
    (90, r"^Paid to\s+(.+)$", "PAYMENT", "DEBIT"),
    (85, r"^Payment to\s+(.+)$", "PAYMENT", "DEBIT"),
    (80, r"^Transfer to\s+(.+)$", "TRANSFER", "DEBIT"),
    (75, r"^Mobile recharged\s*(.*)$", "RECHARGE", "DEBIT"),
    (70, r"^International Roaming Pack for\s+(.+)$", "ROAMING", "DEBIT"),
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

    priority = 500
    for category_name, patterns in CATEGORY_RULES.items():
        for pattern in patterns:
            if not _rule_exists(
                db,
                area="CATEGORY",
                match_field="counterparty_normalized",
                match_type="CONTAINS",
                pattern=pattern,
            ):
                db.add(
                    Rule(
                        area="CATEGORY",
                        priority=priority,
                        match_field="counterparty_normalized",
                        match_type="CONTAINS",
                        pattern=pattern,
                        category_id=existing[category_name].id,
                        enabled=True,
                        builtin=True,
                    )
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

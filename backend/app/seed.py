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

def seed_defaults(db: Session):
    existing = {c.name: c for c in db.scalars(select(Category)).all()}
    for name in CATEGORY_NAMES:
        if name not in existing:
            c = Category(name=name)
            db.add(c)
            db.flush()
            existing[name] = c

    if not db.scalar(select(Rule.id).limit(1)):
        for priority, pattern, operation, expected in DESCRIPTION_SEEDS:
            db.add(Rule(area="DESCRIPTION", priority=priority, match_field="description_raw", match_type="REGEX", pattern=pattern, operation=operation, expected_direction=expected, enabled=True, builtin=True))
        priority = 500
        for category, patterns in CATEGORY_RULES.items():
            for pattern in patterns:
                db.add(Rule(area="CATEGORY", priority=priority, match_field="counterparty_normalized", match_type="CONTAINS", pattern=pattern, category_id=existing[category].id, enabled=True, builtin=True))
                priority -= 1
        db.add(Rule(area="CATEGORY", priority=1000, match_field="operation", match_type="EXACT", pattern="REFUND", category_id=existing["Refunds"].id, enabled=True, builtin=True))
        db.add(Rule(area="CATEGORY", priority=990, match_field="operation", match_type="EXACT", pattern="RECHARGE", category_id=existing["Mobile & Recharge"].id, enabled=True, builtin=True))
        db.add(Rule(area="CATEGORY", priority=989, match_field="operation", match_type="EXACT", pattern="ROAMING", category_id=existing["Mobile & Recharge"].id, enabled=True, builtin=True))
        db.add(Rule(area="CATEGORY", priority=200, match_field="direction", match_type="EXACT", pattern="CREDIT", category_id=existing["Receipts"].id, enabled=True, builtin=True))
    db.commit()

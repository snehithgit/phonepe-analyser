from __future__ import annotations

import re
from collections.abc import Sequence
from sqlalchemy import select
from sqlalchemy.orm import Session
from .models import Category, Rule, Transaction

ALLOWED_MATCH_FIELDS = {
    "description_raw",
    "counterparty_raw",
    "counterparty_normalized",
    "operation",
    "direction",
    "instrument_raw",
}


def _matches(rule: Rule, tx: Transaction) -> bool:
    if rule.match_field not in ALLOWED_MATCH_FIELDS:
        return False
    value = getattr(tx, rule.match_field, None)
    if value is None:
        return False
    value = str(value)
    pattern = rule.pattern
    if rule.match_type == "EXACT":
        return value.casefold() == pattern.casefold()
    if rule.match_type == "CONTAINS":
        return pattern.casefold() in value.casefold()
    if rule.match_type == "STARTS_WITH":
        return value.casefold().startswith(pattern.casefold())
    if rule.match_type == "ENDS_WITH":
        return value.casefold().endswith(pattern.casefold())
    if rule.match_type == "REGEX":
        # Built-in regexes are simple and bounded by short transaction text. Invalid
        # user regexes fail closed instead of breaking imports.
        try:
            return re.search(pattern, value, flags=re.I) is not None
        except re.error:
            return False
    return False


def load_category_rules(db: Session) -> list[Rule]:
    return list(
        db.scalars(
            select(Rule)
            .where(Rule.area == "CATEGORY", Rule.enabled.is_(True))
            .order_by(Rule.priority.desc(), Rule.id.asc())
        ).all()
    )


def get_uncategorized_id(db: Session) -> int | None:
    category = db.scalar(select(Category).where(Category.name == "Uncategorized"))
    return category.id if category else None


def apply_category_rules(
    tx: Transaction,
    rules: Sequence[Rule],
    uncategorized_id: int | None,
) -> None:
    if tx.category_source == "MANUAL":
        return
    for rule in rules:
        if _matches(rule, tx):
            tx.category_id = rule.category_id
            tx.category_rule_id = rule.id
            tx.category_source = "RULE"
            return
    tx.category_id = uncategorized_id
    tx.category_rule_id = None
    tx.category_source = "DEFAULT"

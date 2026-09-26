from __future__ import annotations

import re
from sqlalchemy import select
from sqlalchemy.orm import Session
from .models import Rule, Transaction, Category


def _matches(rule: Rule, tx: Transaction) -> bool:
    value = getattr(tx, rule.match_field, None)
    if value is None:
        return False
    value = str(value)
    p = rule.pattern
    if rule.match_type == "EXACT":
        return value.casefold() == p.casefold()
    if rule.match_type == "CONTAINS":
        return p.casefold() in value.casefold()
    if rule.match_type == "STARTS_WITH":
        return value.casefold().startswith(p.casefold())
    if rule.match_type == "ENDS_WITH":
        return value.casefold().endswith(p.casefold())
    if rule.match_type == "REGEX":
        try:
            return re.search(p, value, flags=re.I) is not None
        except re.error:
            return False
    return False


def apply_category_rules(db: Session, tx: Transaction) -> None:
    if tx.category_source == "MANUAL":
        return
    rules = db.scalars(select(Rule).where(Rule.area == "CATEGORY", Rule.enabled.is_(True)).order_by(Rule.priority.desc(), Rule.id.asc())).all()
    for rule in rules:
        if _matches(rule, tx):
            tx.category_id = rule.category_id
            tx.category_rule_id = rule.id
            tx.category_source = "RULE"
            return
    uncategorized = db.scalar(select(Category).where(Category.name == "Uncategorized"))
    tx.category_id = uncategorized.id if uncategorized else None
    tx.category_rule_id = None
    tx.category_source = "DEFAULT"

from __future__ import annotations

from datetime import datetime
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base

class ImportBatch(Base):
    __tablename__ = "import_batches"
    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), default="PHONEPE", index=True)
    filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    statement_start: Mapped[str | None] = mapped_column(String(10), nullable=True)
    statement_end: Mapped[str | None] = mapped_column(String(10), nullable=True)
    rows_detected: Mapped[int] = mapped_column(Integer, default=0)
    new_rows: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_rows: Mapped[int] = mapped_column(Integer, default=0)
    skipped_rows: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(24), default="IMPORTED")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="import_batch")

class AccountInstrument(Base):
    __tablename__ = "account_instruments"
    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), default="PHONEPE")
    raw_value: Mapped[str] = mapped_column(String(255), unique=True)
    mask: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_own: Mapped[bool] = mapped_column(Boolean, default=True)

class Category(Base):
    __tablename__ = "categories"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(96), unique=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    parent: Mapped["Category | None"] = relationship(remote_side=[id])

class Rule(Base):
    __tablename__ = "rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    area: Mapped[str] = mapped_column(String(32), index=True)  # DESCRIPTION / CATEGORY
    priority: Mapped[int] = mapped_column(Integer, default=100)
    match_field: Mapped[str] = mapped_column(String(48), default="counterparty_normalized")
    match_type: Mapped[str] = mapped_column(String(24), default="CONTAINS")
    pattern: Mapped[str] = mapped_column(String(255))
    operation: Mapped[str | None] = mapped_column(String(48), nullable=True)
    expected_direction: Mapped[str | None] = mapped_column(String(8), nullable=True)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), nullable=True)
    normalized_counterparty: Mapped[str | None] = mapped_column(String(255), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    category: Mapped[Category | None] = relationship()


class CounterpartyProfile(Base):
    __tablename__ = "counterparty_profiles"
    id: Mapped[int] = mapped_column(primary_key=True)
    display_name: Mapped[str] = mapped_column(String(255))
    relationship_type: Mapped[str] = mapped_column(String(32), default="GENERAL", index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    aliases: Mapped[list["CounterpartyAlias"]] = relationship(
        back_populates="profile",
        cascade="all, delete-orphan",
    )
    loans: Mapped[list["Loan"]] = relationship(back_populates="profile")


class CounterpartyAlias(Base):
    __tablename__ = "counterparty_aliases"
    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("counterparty_profiles.id"), index=True)
    normalized_name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    profile: Mapped[CounterpartyProfile] = relationship(back_populates="aliases")


class Loan(Base):
    __tablename__ = "loans"
    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("counterparty_profiles.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    loan_type: Mapped[str] = mapped_column(String(64), default="HOME_LOAN")
    account_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    original_principal_paise: Mapped[int | None] = mapped_column(Integer, nullable=True)
    annual_interest_bps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    emi_paise: Mapped[int | None] = mapped_column(Integer, nullable=True)
    start_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    term_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    profile: Mapped[CounterpartyProfile] = relationship(back_populates="loans")
    payments: Mapped[list["LoanPayment"]] = relationship(
        back_populates="loan",
        cascade="all, delete-orphan",
    )


class LoanPayment(Base):
    __tablename__ = "loan_payments"
    id: Mapped[int] = mapped_column(primary_key=True)
    loan_id: Mapped[int] = mapped_column(ForeignKey("loans.id"), index=True)
    transaction_id: Mapped[int] = mapped_column(
        ForeignKey("transactions.id"),
        unique=True,
        index=True,
    )
    principal_paise: Mapped[int] = mapped_column(Integer, default=0)
    interest_paise: Mapped[int] = mapped_column(Integer, default=0)
    fees_paise: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    loan: Mapped[Loan] = relationship(back_populates="payments")
    transaction: Mapped["Transaction"] = relationship()

class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "transaction_id",
            "direction",
            "amount_paise",
            "utr",
            name="uq_provider_transaction_entry",
        ),
        Index("ix_transactions_datetime", "txn_datetime"),
        Index("ix_transactions_counterparty", "counterparty_normalized"),
        Index("ix_transactions_category", "category_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), default="PHONEPE", index=True)
    import_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id"), index=True)
    account_instrument_id: Mapped[int | None] = mapped_column(ForeignKey("account_instruments.id"), nullable=True)
    txn_datetime: Mapped[datetime] = mapped_column(DateTime)
    direction: Mapped[str] = mapped_column(String(8), index=True)
    operation: Mapped[str] = mapped_column(String(48), default="OTHER", index=True)
    amount_paise: Mapped[int] = mapped_column(Integer)
    description_raw: Mapped[str] = mapped_column(Text)
    counterparty_raw: Mapped[str | None] = mapped_column(String(255), nullable=True)
    counterparty_normalized: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    transaction_id: Mapped[str] = mapped_column(String(96), index=True)
    utr: Mapped[str] = mapped_column(String(96), index=True)
    instrument_raw: Mapped[str] = mapped_column(String(255))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), nullable=True)
    category_source: Mapped[str] = mapped_column(String(16), default="DEFAULT")
    category_rule_id: Mapped[int | None] = mapped_column(ForeignKey("rules.id"), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_self_transfer: Mapped[bool] = mapped_column(Boolean, default=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    import_batch: Mapped[ImportBatch] = relationship(back_populates="transactions")
    account_instrument: Mapped[AccountInstrument | None] = relationship()
    category: Mapped[Category | None] = relationship(foreign_keys=[category_id])
    category_rule: Mapped[Rule | None] = relationship(foreign_keys=[category_rule_id])

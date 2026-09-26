from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from .models import Transaction


LEGACY_TRANSACTION_UNIQUE = "uq_provider_transaction_id"


def migrate_transaction_identity(engine: Engine) -> bool:
    """Replace the legacy transaction-id-only unique constraint.

    Older PhonePe exports can reuse one Transaction ID for related debit/credit
    ledger legs. The stable import identity is therefore:
    provider + transaction_id + direction + amount_paise + utr.

    SQLite cannot drop a UNIQUE constraint in place, so the transactions table
    is rebuilt once while preserving every existing row and primary key.
    """
    with engine.begin() as conn:
        table_sql = conn.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='transactions'"
        ).scalar()

        if not table_sql or LEGACY_TRANSACTION_UNIQUE not in table_sql:
            return False

        old_name = "transactions_legacy_identity"
        existing_old = conn.exec_driver_sql(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (old_name,),
        ).scalar()
        if existing_old:
            raise RuntimeError(
                "Cannot migrate transaction identity: leftover "
                f"{old_name!r} table exists"
            )

        conn.exec_driver_sql(
            f'ALTER TABLE "transactions" RENAME TO "{old_name}"'
        )

        # Named indexes retain their names when SQLite renames a table.
        # Drop only those legacy indexes so the new table can recreate them.
        legacy_indexes = conn.exec_driver_sql(
            f'PRAGMA index_list("{old_name}")'
        ).fetchall()
        for row in legacy_indexes:
            index_name = row[1]
            if index_name.startswith("sqlite_autoindex_"):
                continue
            escaped = index_name.replace('"', '""')
            conn.exec_driver_sql(f'DROP INDEX "{escaped}"')

        Transaction.__table__.create(bind=conn)

        old_columns = {
            row[1]
            for row in conn.exec_driver_sql(
                f'PRAGMA table_info("{old_name}")'
            ).fetchall()
        }
        new_columns = [column.name for column in Transaction.__table__.columns]
        common = [column for column in new_columns if column in old_columns]
        quoted = ", ".join(f'"{column}"' for column in common)

        conn.exec_driver_sql(
            f'INSERT INTO "transactions" ({quoted}) '
            f'SELECT {quoted} FROM "{old_name}"'
        )
        conn.exec_driver_sql(f'DROP TABLE "{old_name}"')

    return True

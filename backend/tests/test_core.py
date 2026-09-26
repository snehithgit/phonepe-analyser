import os
import tempfile
import unittest
from pathlib import Path

_TMP = tempfile.TemporaryDirectory()
os.environ["PHONEPE_DB_PATH"] = str(Path(_TMP.name) / "test.db")

from fastapi.testclient import TestClient
from app.main import app
from app.database import SessionLocal
from app.models import Transaction
from app.patterns import detect_patterns

FIXTURE = Path(__file__).parent / "fixtures" / "phonepe_sample.csv"


class CoreRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def setUp(self):
        with SessionLocal() as db:
            db.query(Transaction).delete()
            from app.models import ImportBatch
            db.query(ImportBatch).delete()
            db.commit()

    def _upload(self, endpoint: str, path: Path = FIXTURE):
        return self.client.post(
            endpoint,
            files={"file": (path.name, path.read_bytes(), "text/csv")},
        )

    def test_import_rollback_reimport_restores_rows(self):
        first = self._upload("/api/imports/commit")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["new_transactions"], 3)

        import_id = first.json()["import_id"]
        rolled = self.client.post(f"/api/imports/{import_id}/rollback")
        self.assertEqual(rolled.status_code, 200)
        self.assertEqual(rolled.json()["rolled_back"], 3)

        preview = self._upload("/api/imports/preview")
        self.assertEqual(preview.json()["new_transactions"], 0)
        self.assertEqual(preview.json()["restorable_transactions"], 3)
        self.assertEqual(preview.json()["already_imported"], 0)

        restored = self._upload("/api/imports/commit")
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(restored.json()["restored_transactions"], 3)
        self.assertEqual(restored.json()["duplicates"], 0)

    def test_reimport_active_rows_are_duplicates(self):
        self._upload("/api/imports/commit")
        second = self._upload("/api/imports/commit")
        self.assertEqual(second.json()["new_transactions"], 0)
        self.assertEqual(second.json()["restored_transactions"], 0)
        self.assertEqual(second.json()["duplicates"], 3)

    def test_self_transfer_excluded_from_patterns(self):
        self._upload("/api/imports/commit")
        with SessionLocal() as db:
            rows = db.query(Transaction).all()
            for row in rows:
                row.counterparty_normalized = "TEST PERSON"
                row.operation = "PAYMENT"
                row.direction = "DEBIT"
                row.is_self_transfer = True
            db.commit()
            self.assertEqual(detect_patterns(db), [])


class PatternClusteringTests(unittest.TestCase):
    def test_distinct_amount_streams_do_not_merge(self):
        from datetime import datetime
        from app.database import SessionLocal
        from app.models import ImportBatch, Transaction
        with SessionLocal() as db:
            db.query(Transaction).delete()
            db.query(ImportBatch).delete()
            batch=ImportBatch(filename='synthetic.csv',sha256='x'*64,rows_detected=4)
            db.add(batch);db.flush()
            for idx,(day,amount) in enumerate([(1,210000),(2,530000),(31,210000),(32,530000)],start=1):
                month=1 if day<=31 else 2
                real_day=day if day<=31 else day-31
                db.add(Transaction(import_id=batch.id,txn_datetime=datetime(2026,month,real_day),direction='DEBIT',operation='PAYMENT',amount_paise=amount,description_raw='Paid to INSURER',counterparty_raw='INSURER',counterparty_normalized='INSURER',transaction_id=f'P{idx}',utr=f'U{idx}',instrument_raw='Paid by XXXX1234'))
            db.commit()
            rows=detect_patterns(db)
            self.assertEqual(len(rows),2)
            self.assertEqual(sorted(r['median_amount_paise'] for r in rows),[210000,530000])

if __name__ == "__main__":
    unittest.main()

import os
import tempfile
import unittest
from pathlib import Path

_TMP = tempfile.TemporaryDirectory()
os.environ["PHONEPE_DB_PATH"] = str(Path(_TMP.name) / "test.db")

from fastapi.testclient import TestClient
from app.main import app
from app.database import SessionLocal
from app.models import (
    CounterpartyAlias,
    CounterpartyProfile,
    Loan,
    LoanPayment,
    Transaction,
)
from app.patterns import detect_patterns

FIXTURE = Path(__file__).parent / "fixtures" / "phonepe_sample.csv"


class CoreRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def setUp(self):
        with SessionLocal() as db:
            from app.models import ImportBatch
            db.query(LoanPayment).delete()
            db.query(Loan).delete()
            db.query(CounterpartyAlias).delete()
            db.query(CounterpartyProfile).delete()
            db.query(Transaction).delete()
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



    def test_same_transaction_id_can_have_debit_and_credit_legs(self):
        raw = b'''Transaction Statement for 9000000000
Duration,01 Jan 2021 - 02 Jan 2021

Date,Time,Transaction Details,Transaction ID,UTR,Transaction Type,Credit/debit instrument,Amount
2021-01-01,10:00,Paid to TEST MERCHANT,TSHARED,111111111111,Debit,XXXX1234,100.00
2021-01-01,10:01,Received from PhonePe,TSHARED,,Credit,Wallet,1.50
'''
        import tempfile
        from pathlib import Path

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as handle:
            handle.write(raw)
            temp_path = Path(handle.name)

        try:
            response = self._upload("/api/imports/commit", temp_path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["new_transactions"], 2)

            second = self._upload("/api/imports/commit", temp_path)
            self.assertEqual(second.status_code, 200)
            self.assertEqual(second.json()["duplicates"], 2)
        finally:
            temp_path.unlink(missing_ok=True)

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


class RelationshipFeatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def setUp(self):
        from app.models import ImportBatch
        with SessionLocal() as db:
            db.query(LoanPayment).delete()
            db.query(Loan).delete()
            db.query(CounterpartyAlias).delete()
            db.query(CounterpartyProfile).delete()
            db.query(Transaction).delete()
            db.query(ImportBatch).delete()
            db.commit()

    def _import_csv(self, raw: bytes):
        return self.client.post(
            "/api/imports/commit",
            files={"file": ("relationship.csv", raw, "text/csv")},
        )

    def test_counterparty_aliases_merge_one_ledger(self):
        raw = b'''Transaction Statement for 9000000000
Duration,01 Jul 2026 - 31 Aug 2026

Date,Time,Transaction Details,Transaction ID,UTR,Transaction Type,Credit/debit instrument,Amount
"Jul 01, 2026","10:00 AM","Paid to PILLA LAXMANA RAO","TL1","111","DEBIT","XXXX1234","40000"
"Aug 01, 2026","10:00 AM","Received from pilla lakshman rao","TL2","222","CREDIT","XXXX1234","5000"
'''
        self.assertEqual(self._import_csv(raw).status_code, 200)
        created = self.client.post(
            "/api/counterparty-profiles",
            json={
                "display_name": "Pilla Lakshman Rao",
                "primary_alias": "PILLA LAXMANA RAO",
                "relationship_type": "PERSONAL_LENDING",
            },
        )
        self.assertEqual(created.status_code, 200)
        profile_id = created.json()["id"]
        alias = self.client.post(
            f"/api/counterparty-profiles/{profile_id}/aliases",
            json={"alias": "pilla lakshman rao"},
        )
        self.assertEqual(alias.status_code, 200)

        ledger = self.client.get("/api/counterparties/PILLA%20LAXMANA%20RAO/ledger")
        self.assertEqual(ledger.status_code, 200)
        body = ledger.json()
        self.assertEqual(body["transaction_count"], 2)
        self.assertEqual(body["total_paid"], 40000.0)
        self.assertEqual(body["total_received"], 5000.0)
        self.assertEqual(body["balance_due_to_you"], 35000.0)

    def test_merge_duplicate_counterparty_profiles_combines_ledger(self):
        raw = b'''Transaction Statement for 9000000000
Duration,01 Jul 2026 - 31 Aug 2026

Date,Time,Transaction Details,Transaction ID,UTR,Transaction Type,Credit/debit instrument,Amount
"Jul 01, 2026","10:00 AM","Paid to PILLA LAXMANA RAO","TM1","111","DEBIT","XXXX1234","40000"
"Aug 01, 2026","10:00 AM","Received from pilla lakshman rao","TM2","222","CREDIT","XXXX1234","5000"
'''
        self.assertEqual(self._import_csv(raw).status_code, 200)

        debit_profile = self.client.post(
            "/api/counterparty-profiles",
            json={
                "display_name": "Pilla Laxmana Rao",
                "primary_alias": "PILLA LAXMANA RAO",
                "relationship_type": "PERSONAL_LENDING",
            },
        ).json()
        credit_profile = self.client.post(
            "/api/counterparty-profiles",
            json={
                "display_name": "Pilla Lakshman Rao",
                "primary_alias": "PILLA LAKSHMAN RAO",
                "relationship_type": "PERSONAL_LENDING",
            },
        ).json()

        merged = self.client.post(
            f"/api/counterparty-profiles/{debit_profile['id']}/merge",
            json={"alias": "PILLA LAKSHMAN RAO"},
        )
        self.assertEqual(merged.status_code, 200)
        self.assertTrue(merged.json()["merged"])

        ledger = self.client.get(
            "/api/counterparties/PILLA%20LAXMANA%20RAO/ledger"
        ).json()
        self.assertEqual(ledger["transaction_count"], 2)
        self.assertEqual(ledger["total_paid"], 40000.0)
        self.assertEqual(ledger["total_received"], 5000.0)
        self.assertEqual(ledger["balance_due_to_you"], 35000.0)
        self.assertEqual(
            sorted(ledger["aliases"]),
            ["PILLA LAKSHMAN RAO", "PILLA LAXMANA RAO"],
        )

        with SessionLocal() as db:
            self.assertIsNone(db.get(CounterpartyProfile, credit_profile["id"]))

    def test_loan_auto_link_and_interest_allocation(self):
        raw = b'''Transaction Statement for 9000000000
Duration,01 Jul 2026 - 31 Aug 2026

Date,Time,Transaction Details,Transaction ID,UTR,Transaction Type,Credit/debit instrument,Amount
"Jul 10, 2026","10:00 AM","Paid to UNION ASHA","TH1","333","DEBIT","XXXX1234","40000"
"Aug 10, 2026","10:00 AM","Paid to UNION ASHA","TH2","444","DEBIT","XXXX1234","40000"
'''
        self.assertEqual(self._import_csv(raw).status_code, 200)
        loan = self.client.post(
            "/api/loans",
            json={
                "name": "Union Asha Home Loan",
                "lender_name": "UNION ASHA",
                "loan_type": "HOME_LOAN",
                "original_principal": "2000000",
                "annual_interest_rate": "8.5",
                "emi": "40000",
            },
        )
        self.assertEqual(loan.status_code, 200)
        loan_id = loan.json()["id"]

        linked = self.client.post(f"/api/loans/{loan_id}/auto-link")
        self.assertEqual(linked.status_code, 200)
        self.assertEqual(linked.json()["linked"], 2)

        detail = self.client.get(f"/api/loans/{loan_id}").json()
        transaction_id = detail["payments"][0]["transaction"]["id"]
        allocated = self.client.patch(
            f"/api/loans/{loan_id}/payments/{transaction_id}",
            json={"interest": "12000", "fees": "0"},
        )
        self.assertEqual(allocated.status_code, 200)
        self.assertEqual(allocated.json()["principal"], 28000.0)
        detail = self.client.get(f"/api/loans/{loan_id}").json()
        self.assertEqual(detail["interest_paid"], 12000.0)
        self.assertEqual(detail["principal_paid"], 28000.0)

if __name__ == "__main__":
    unittest.main()

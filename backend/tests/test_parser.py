import unittest
from pathlib import Path
from app.parser import parse_phonepe_csv, parse_description, rupees_to_paise

class ParserTests(unittest.TestCase):
    def test_real_shape_fixture(self):
        p = Path(__file__).parent / "fixtures" / "phonepe_sample.csv"
        result = parse_phonepe_csv(p.read_bytes(), p.name)
        self.assertEqual(len(result.transactions), 3)
        self.assertEqual(result.skipped_rows, 1)
        self.assertEqual(result.transactions[0].utr, "000000000123")
        self.assertEqual(result.transactions[0].operation, "PAYMENT")
        self.assertEqual(result.transactions[1].operation, "RECEIVED")
        self.assertEqual(result.transactions[2].operation, "RECHARGE")
        self.assertEqual(result.transactions[1].amount_paise, 150050)
        self.assertEqual(result.statement_start, "2026-09-01")

    def test_money_exact(self):
        self.assertEqual(rupees_to_paise("1,037.95"), 103795)
        self.assertEqual(rupees_to_paise("0.01"), 1)

    def test_direction_conflict(self):
        op, who, conflict = parse_description("Paid to ABC", "CREDIT")
        self.assertEqual(op, "PAYMENT")
        self.assertEqual(who, "ABC")
        self.assertTrue(conflict)

if __name__ == "__main__":
    unittest.main()

class MissingUtrTests(unittest.TestCase):
    def test_valid_row_without_utr_is_kept(self):
        raw = b'''Transaction Statement for 9000000000\nDuration,"01 Sep, 2026 - 02 Sep, 2026"\n\nDate,Time,Transaction Details,Transaction ID,UTR,Transaction Type,Credit/debit instrument,Amount\n"Sep 01, 2026","10:00 AM","Received from TEST USER","TNO-UTR-1","","CREDIT","Credited to XXXX1234","100.00"\n'''
        result = parse_phonepe_csv(raw, "missing-utr.csv")
        self.assertEqual(len(result.transactions), 1)
        self.assertEqual(result.transactions[0].utr, "")
        self.assertTrue(result.transactions[0].missing_utr)
        self.assertEqual(result.missing_utr_rows, 1)

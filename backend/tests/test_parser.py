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

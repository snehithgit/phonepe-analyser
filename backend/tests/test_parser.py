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


    def test_older_iso_date_24h_phonepe_export(self):
        raw = b'''Transaction Statement for +919000000000
Duration,09 Jun 2017 - 26 Sep 2026

Date,Time,Transaction Details,Transaction ID,UTR,Transaction Type,Credit/debit instrument,Amount
2020-12-24,\t15:32,Paid to IRCTC,C2012241531452369112119,035961612176,Debit,********0654,161.80
2020-12-25,\t21:04,Paid to Airtel HDFC UPI Master login,T2012252104181794279447,,Debit,********0654,558.00
This is an automatically generated statement.
'''
        result = parse_phonepe_csv(raw, "old-export.csv")
        self.assertEqual(result.statement_start, "2017-06-09")
        self.assertEqual(result.statement_end, "2026-09-26")
        self.assertEqual(len(result.transactions), 2)
        self.assertEqual(result.skipped_rows, 1)
        self.assertEqual(result.transactions[0].date, "2020-12-24")
        self.assertEqual(result.transactions[0].time, "15:32")
        self.assertEqual(result.transactions[0].amount_paise, 16180)
        self.assertTrue(result.transactions[1].missing_utr)

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


class DescriptionGrammarTests(unittest.TestCase):
    """Covers the recharge/bill-payment description patterns added to
    DESCRIPTION_RULES, which previously fell through to operation=OTHER."""

    def test_mobile_recharge_dash_variant(self):
        op, who, conflict = parse_description("Paid - Mobile Recharge", "DEBIT")
        self.assertEqual(op, "RECHARGE")
        self.assertIsNone(who)
        self.assertFalse(conflict)

    def test_bill_paid_with_biller_name(self):
        op, who, conflict = parse_description("Bill paid - Electricity Board", "DEBIT")
        self.assertEqual(op, "BILL_PAYMENT")
        self.assertEqual(who, "Electricity Board")
        self.assertFalse(conflict)

    def test_bill_paid_no_biller_name(self):
        op, who, conflict = parse_description("Bill paid", "DEBIT")
        self.assertEqual(op, "BILL_PAYMENT")
        self.assertIsNone(who)
        self.assertFalse(conflict)

    def test_existing_patterns_still_take_priority(self):
        # Guards against the new rules being inserted in a way that shadows
        # earlier, more specific matches like "Paid to X".
        op, who, conflict = parse_description("Paid to IRCTC", "DEBIT")
        self.assertEqual(op, "PAYMENT")
        self.assertEqual(who, "IRCTC")
        self.assertFalse(conflict)

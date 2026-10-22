"""Due-date parsing: human text → real date, plus urgency buckets."""
import os
import sys
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.dates import humanize, parse_due, urgency  # noqa: E402

TODAY = date(2026, 10, 22)          # a Thursday


class TestParseDue(unittest.TestCase):
    def test_iso_and_full_date(self):
        self.assertEqual(parse_due("2026-10-22", TODAY), date(2026, 10, 22))
        self.assertEqual(parse_due("2026/11/05 交期", TODAY), date(2026, 11, 5))
        self.assertEqual(parse_due("2026年12月1日", TODAY), date(2026, 12, 1))

    def test_iso_wins_over_relative_words(self):
        # the real record: "明天发货（2026-10-22）"
        self.assertEqual(parse_due("明天发货（2026-10-22）", TODAY), date(2026, 10, 22))

    def test_month_day(self):
        self.assertEqual(parse_due("10月20日", TODAY), date(2026, 10, 20))
        self.assertEqual(parse_due("10/28", TODAY), date(2026, 10, 28))
        self.assertEqual(parse_due("10-28 前", TODAY), date(2026, 10, 28))
        # a date said in November means NEXT year, not 10 months ago
        self.assertEqual(parse_due("1月5日", date(2026, 11, 10)), date(2027, 1, 5))

    def test_order_numbers_are_not_dates(self):
        self.assertIsNone(parse_due("Order 10/2026", TODAY))
        self.assertIsNone(parse_due("PO 2026/1022", TODAY))

    def test_relative_words(self):
        self.assertEqual(parse_due("今天", TODAY), TODAY)
        self.assertEqual(parse_due("明天", TODAY), TODAY + timedelta(days=1))
        self.assertEqual(parse_due("后天", TODAY), TODAY + timedelta(days=2))
        self.assertEqual(parse_due("大后天", TODAY), TODAY + timedelta(days=3))
        self.assertEqual(parse_due("3天后", TODAY), TODAY + timedelta(days=3))

    def test_weekdays(self):
        # Thursday 2026-10-22
        self.assertEqual(parse_due("周五", TODAY), date(2026, 10, 23))
        self.assertEqual(parse_due("本周五", TODAY), date(2026, 10, 23))
        self.assertEqual(parse_due("下周一", TODAY), date(2026, 10, 26))
        self.assertEqual(parse_due("周四", TODAY), date(2026, 10, 22))
        # a bare weekday that already passed moves to next week
        self.assertEqual(parse_due("周一", TODAY), date(2026, 10, 26))

    def test_month_and_year_ends(self):
        self.assertEqual(parse_due("月底", TODAY), date(2026, 10, 31))
        self.assertEqual(parse_due("下个月", TODAY), date(2026, 11, 22))
        self.assertEqual(parse_due("年底", TODAY), date(2026, 12, 31))

    def test_unparseable(self):
        for text in ("", "尽快", "   ", "as soon as possible"):
            self.assertIsNone(parse_due(text, TODAY), text)


class TestUrgency(unittest.TestCase):
    def test_buckets(self):
        self.assertEqual(urgency("2026-10-20", TODAY), "overdue")
        self.assertEqual(urgency("2026-10-22", TODAY), "today")
        self.assertEqual(urgency("2026-10-23", TODAY), "soon")
        self.assertEqual(urgency("2026-10-30", TODAY), "later")
        self.assertEqual(urgency("", TODAY), "")

    def test_humanize(self):
        self.assertEqual(humanize("2026-10-20", TODAY), "逾期2天")
        self.assertEqual(humanize("2026-10-22", TODAY), "今天")
        self.assertEqual(humanize("2026-10-23", TODAY), "明天")
        self.assertEqual(humanize("2026-10-28", TODAY), "10-28")
        self.assertEqual(humanize("", TODAY), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)

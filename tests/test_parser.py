"""一句话解析的回归：金额、时间、类别、AA 人数。

对应 temp_trying 里的 test_parser_time / test_amount_score / test_multi_clause，
这里挑了最容易被改坏的那几条。
"""
import unittest
from datetime import date

import helpers  # noqa: F401  （把项目根塞进 sys.path）
import parser

TODAY = date(2026, 11, 24)


class ParserTest(unittest.TestCase):
    def test_time_is_not_amount(self):
        """时间里的数字不能当成金额——这是真实踩过的坑。"""
        cases = [
            ("昨天22点打车32", 3200, "2026-11-23 22:00"),
            ("22点打车32", 3200, "2026-11-24 22:00"),
            ("22点30打车32", 3200, "2026-11-24 22:30"),
            ("22点半打车32", 3200, "2026-11-24 22:30"),
            ("22:30打车32", 3200, "2026-11-24 22:30"),
            ("晚上10点吃饭50", 5000, "2026-11-24 22:00"),
            ("下午3点咖啡28", 2800, "2026-11-24 15:00"),
            ("凌晨2点买了瓶水3", 300, "2026-11-24 02:00"),
        ]
        for text, amount, when in cases:
            with self.subTest(text=text):
                got = parser.parse(text, today=TODAY)
                self.assertEqual(got["amount_cents"], amount)
                self.assertEqual(got["occurred_at"], when)

    def test_bad_clock_is_ignored(self):
        """25 点、10 点 70 分不存在的时刻：当没写时间，金额照旧。"""
        for text in ("25点打车32", "10点70分打车32"):
            with self.subTest(text=text):
                got = parser.parse(text, today=TODAY)
                self.assertEqual(got["amount_cents"], 3200)
                self.assertEqual(got["occurred_at"], "2026-11-24")

    def test_amount_forms(self):
        cases = [
            ("32块5的奶茶", 3250),
            ("午饭44", 4400),
            ("1.5万", 1500000),
            ("一百零五块", 10500),
        ]
        for text, amount in cases:
            with self.subTest(text=text):
                self.assertEqual(parser.parse(text)["amount_cents"], amount)

    def test_model_number_is_not_money(self):
        """「5090显卡」里的 5090 是型号，整句没有金额才诚实。"""
        got = parser.parse("买了5090显卡")
        self.assertIsNone(got["amount_cents"])

    def test_category(self):
        self.assertEqual(parser.parse("昨天打车32")["category"], "交通")
        self.assertEqual(parser.parse("午饭25")["category"], "餐饮")
        self.assertIsNone(parser.parse("记一笔账")["category"])

    def test_aa_count(self):
        self.assertEqual(parser.parse("买水，四个舍友aa 15.6")["aa_count"], 4)
        self.assertEqual(parser.parse("四个人平摊火锅 200")["aa_count"], 4)
        self.assertIsNone(parser.parse("买了一瓶水 3")["aa_count"])


if __name__ == "__main__":
    unittest.main()

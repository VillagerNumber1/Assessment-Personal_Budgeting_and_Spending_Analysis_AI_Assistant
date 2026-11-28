"""小票 OCR 的**校验层**：模型给什么都要当不可信输入。

这里不打网络（read_image 要真 key），只测收尾那一步：
金额范围、日期真实存在、时刻不越界、文字怎么拼。
"""
import unittest

import helpers  # noqa: F401
import receipt


class ReceiptValidateTest(unittest.TestCase):
    def test_normal_receipt(self):
        d = receipt._validate({"amount": 14.5, "date": "2026-11-20",
                               "time": "18:32", "merchant": "全家便利店",
                               "items": "矿泉水、面包"})
        self.assertEqual(d["amount_cents"], 1450)
        self.assertEqual(d["date"], "2026-11-20")
        self.assertEqual(d["time"], "18:32")
        self.assertEqual(d["text"], "全家便利店 · 矿泉水、面包")

    def test_bad_values_are_dropped(self):
        d = receipt._validate({"amount": -5, "date": "2026-02-30",
                               "time": "25:99", "merchant": 123})
        self.assertIsNone(d["amount_cents"])
        self.assertIsNone(d["date"])
        self.assertIsNone(d["time"])
        self.assertIsNone(d["merchant"])

    def test_amount_upper_bound(self):
        """超过 100 万跟 llm 那边同一个上限，不收。"""
        self.assertIsNone(receipt._validate({"amount": 2_000_000})["amount_cents"])
        self.assertEqual(receipt._validate({"amount": 1_000_000})["amount_cents"], 100_000_000)

    def test_text_fallback(self):
        self.assertEqual(receipt._validate({"amount": 1})["text"], "小票消费")
        self.assertEqual(receipt._validate({"amount": 1, "merchant": "便利店"})["text"], "便利店")


if __name__ == "__main__":
    unittest.main()

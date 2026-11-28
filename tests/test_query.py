"""查账：把中文问题翻成时间范围；模型给的非法结构必须被白名单挡住。

这里只测**纯函数**（不连模型、不查库），所以离线可跑。
"""
import unittest
from datetime import date

import helpers  # noqa: F401
import llm
import query

TODAY = date(2026, 11, 26)


class TimeRangeTest(unittest.TestCase):
    def _range(self, text):
        d = query.parse_question(text, today=TODAY)
        return d["start"], d["end"]

    def test_relative_words(self):
        self.assertEqual(self._range("这个月花了多少"), ("2026-11-01", "2026-11-30"))
        self.assertEqual(self._range("上个月吃饭多少"), ("2026-10-01", "2026-10-31"))
        self.assertEqual(self._range("今天花了多少"), ("2026-11-26", "2026-11-26"))
        self.assertEqual(self._range("昨天花了多少"), ("2026-11-25", "2026-11-25"))

    def test_explicit_month_day(self):
        self.assertEqual(self._range("11月25日花了多少"), ("2026-11-25", "2026-11-25"))
        self.assertEqual(self._range("11月25号花了多少"), ("2026-11-25", "2026-11-25"))
        self.assertEqual(self._range("上个月25号花了多少"), ("2026-10-25", "2026-10-25"))

    def test_day_only_uses_current_month(self):
        """只写「25号」按当年当月算（用户定的规矩）。"""
        self.assertEqual(self._range("25号花了多少"), ("2026-11-25", "2026-11-25"))

    def test_month_only(self):
        self.assertEqual(self._range("11月花了多少"), ("2026-11-01", "2026-11-30"))

    def test_impossible_date_falls_back_to_month(self):
        """2 月 30 号不存在：不能编一个日期出来。"""
        start, end = self._range("2月30日花了多少")
        self.assertEqual((start, end), ("2026-11-01", "2026-11-30"))


class ModelQueryValidationTest(unittest.TestCase):
    """模型给的结构一律当不可信输入。"""

    def test_bad_enum_and_categories_are_filtered(self):
        raw = {"is_query": True, "metric": "删除表", "kind": "whatever",
               "start": "2026-11-01", "end": "2026-11-30",
               "group_by": "sql", "categories": ["餐饮", "不存在的类"]}
        v = llm._validate_query(raw)
        self.assertEqual(v["metric"], "sum")
        self.assertEqual(v["kind"], "expense")
        self.assertEqual(v["group_by"], "none")
        self.assertEqual(v["categories"], ["餐饮"])
        self.assertEqual(v["start"], "2026-11-01")

    def test_impossible_date_is_not_a_query(self):
        v = llm._validate_query({"is_query": True, "start": "2026-02-30", "end": "2026-03-01"})
        self.assertIs(v.get("is_query"), False)

    def test_reversed_range_is_not_a_query(self):
        v = llm._validate_query({"is_query": True, "start": "2026-12-01", "end": "2026-11-01"})
        self.assertIs(v.get("is_query"), False)

    def test_not_a_query(self):
        self.assertEqual(llm._validate_query({"is_query": False}), {"is_query": False})


if __name__ == "__main__":
    unittest.main()

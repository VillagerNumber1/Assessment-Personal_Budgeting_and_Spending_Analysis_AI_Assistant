"""接口层的校验与写入：金额、日期、月份、导入去重、月度汇总。

这些之前没有测试——解析层测得很细，但「接口会不会把脏数据放进来」一直没人管。
全部离线跑：显式给金额/日期/类别时不会触发模型调用。
"""
import unittest

from fastapi import HTTPException

import helpers
import app
import storage


class ValidationTest(unittest.TestCase):
    def test_amount_bounds(self):
        with self.assertRaises(HTTPException) as ctx:
            app.check_amount(0)
        self.assertEqual(ctx.exception.status_code, 400)

        with self.assertRaises(HTTPException):
            app.check_amount(100_000_001)          # 超过 100 万，确认了也不收

        # 10 万 ~ 100 万：要二次确认，返回带 code 的错误
        with self.assertRaises(HTTPException) as ctx:
            app.check_amount(20_000_000)
        self.assertEqual(ctx.exception.detail["code"], "amount_too_large")
        app.check_amount(20_000_000, confirm_large=True)   # 确认后放行，不抛

    def test_occurred_at(self):
        for bad in ("2026-02-30", "2026-11-23 25:00", "2026/11/23"):
            with self.subTest(bad=bad):
                with self.assertRaises(HTTPException):
                    app.check_occurred_at(bad)
        self.assertEqual(app.check_occurred_at("2026-11-23T14:30"), "2026-11-23 14:30")
        self.assertEqual(app.check_occurred_at("2026-11-23"), "2026-11-23")
        self.assertIsNone(app.check_occurred_at(None))

    def test_month(self):
        for bad in ("2026-13", "2026-1", "2026/11"):
            with self.subTest(bad=bad):
                with self.assertRaises(HTTPException):
                    app.check_month(bad)
        self.assertEqual(app.check_month("2026-11"), "2026-11")

    def test_budget(self):
        with self.assertRaises(HTTPException):
            app.check_budget_yuan(-1)
        with self.assertRaises(HTTPException):
            app.check_budget_yuan(1_000_001)
        self.assertEqual(app.check_budget_yuan(None), 0.0)
        self.assertEqual(app.check_budget_yuan(3000), 3000)


class CreateTransactionTest(unittest.TestCase):
    def setUp(self):
        helpers.fresh_db()

    def test_explicit_amount_is_saved(self):
        r = app.create_transaction(app.NewTransaction(
            text="午饭", amount_cents=2500, occurred_at="2026-11-20",
            category="餐饮", kind="expense"))
        self.assertTrue(r["ok"])
        row = storage.get(r["id"])
        self.assertEqual(row["amount_cents"], 2500)
        self.assertEqual(row["occurred_at"], "2026-11-20")
        self.assertEqual(row["category"], "餐饮")

    def test_large_amount_needs_confirm(self):
        with self.assertRaises(HTTPException) as ctx:
            app.create_transaction(app.NewTransaction(
                text="大额", amount_cents=20_000_000, occurred_at="2026-11-20",
                category="购物"))
        self.assertEqual(ctx.exception.detail["code"], "amount_too_large")

        r = app.create_transaction(app.NewTransaction(
            text="大额", amount_cents=20_000_000, occurred_at="2026-11-20",
            category="购物", confirm_large=True))
        self.assertTrue(r["ok"])


class ImportCommitTest(unittest.TestCase):
    def setUp(self):
        helpers.fresh_db()

    def test_validation_dedupe_and_category(self):
        storage.add("已存在", 2500, "2026-11-20")          # 去重键会撞上这一条
        body = app.ImportBody(rows=[
            app.ImportRow(text="已存在", amount_cents=2500, occurred_at="2026-11-20"),
            app.ImportRow(text="负数", amount_cents=-100, occurred_at="2026-11-21"),
            app.ImportRow(text="咖啡", amount_cents=1900, occurred_at="2026-11-21",
                          category="餐饮"),
            app.ImportRow(text="午饭", amount_cents=1500, occurred_at="2026-11-22",
                          category="不存在的类"),
        ])
        result = app.import_commit(body)
        self.assertEqual(result["imported"], 2)
        self.assertEqual(result["duplicated"], 1)
        self.assertEqual(result["skipped"], 1)

        rows = {row["text"]: row for row in storage.list_recent(10)}
        self.assertEqual(rows["午饭"]["category"], "餐饮")   # 白名单外 → 按文字重新猜

    def test_empty_rows(self):
        with self.assertRaises(HTTPException):
            app.import_commit(app.ImportBody(rows=[]))


class SummaryTest(unittest.TestCase):
    def setUp(self):
        helpers.fresh_db()

    def test_month_totals_and_balance(self):
        storage.add("吃饭", 3000, "2026-11-10", "餐饮")
        storage.add("工资", 500000, "2026-11-10", None, kind="income")
        d = app.summary("2026-11")
        self.assertEqual(d["expense_cents"], 3000)
        self.assertEqual(d["income_cents"], 500000)
        self.assertEqual(d["balance_cents"], 497000)

    def test_locate_returns_offset(self):
        storage.add("第一笔", 1000, "2026-11-01")
        storage.add("第二笔", 2000, "2026-11-02")
        loc = app.locate_transaction("2026-11-01", "2026-11-01")
        self.assertEqual(loc["offset"], 1)          # 比它新的有 1 条
        self.assertEqual(loc["month"], "2026-11")


class SettingsTest(unittest.TestCase):
    def setUp(self):
        helpers.fresh_db()

    def test_local_asr_roundtrip(self):
        self.assertTrue(app.read_settings()["local_asr"])      # 默认开
        app.write_settings(app.SettingsBody(local_asr=False))
        self.assertFalse(app.read_settings()["local_asr"])
        app.write_settings(app.SettingsBody(local_asr=True))
        self.assertTrue(app.read_settings()["local_asr"])

    def test_local_model_status_shape(self):
        d = app.asr_local_status()
        for key in ("engine", "engine_available", "model_ready", "enabled",
                    "percent", "size", "repo"):
            self.assertIn(key, d)
        self.assertEqual(d["engine"], "faster-whisper")


if __name__ == "__main__":
    unittest.main()

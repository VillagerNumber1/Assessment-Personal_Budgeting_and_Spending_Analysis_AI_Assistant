"""预算：默认预算按「生效区间」存，不篡改历史；单独设某月覆盖默认；改动可撤销。"""
import unittest

import helpers
import storage


class BudgetTest(unittest.TestCase):
    def setUp(self):
        helpers.fresh_db()

    def test_empty_db_has_no_default(self):
        for month in ("2025-01", "2026-11", "2027-06"):
            self.assertIsNone(storage.get_default_budget(month))

    def test_range_starts_now_and_keeps_history(self):
        """2026-11 起 3000，12 月改 4000：11 月必须还是 3000。"""
        storage.set_default_budget(300000, "2026-11")
        self.assertIsNone(storage.get_default_budget("2026-10"))
        self.assertEqual(storage.get_default_budget("2026-11"), 300000)
        self.assertEqual(storage.get_default_budget("2027-03"), 300000)

        storage.set_default_budget(400000, "2026-12")
        self.assertEqual(storage.get_default_budget("2026-11"), 300000)
        self.assertEqual(storage.get_default_budget("2026-12"), 400000)

    def test_same_month_twice_does_not_stack_ranges(self):
        storage.set_default_budget(300000, "2026-11")
        storage.set_default_budget(450000, "2026-12")
        storage.set_default_budget(500000, "2026-12")
        self.assertEqual(storage.get_default_budget("2026-12"), 500000)
        con = storage._connect()
        try:
            rows = con.execute("select count(*) from budget_defaults").fetchone()[0]
        finally:
            con.close()
        self.assertEqual(rows, 2)

    def test_turn_default_off(self):
        storage.set_default_budget(500000, "2026-12")
        storage.set_default_budget(None, "2027-01")
        self.assertEqual(storage.get_default_budget("2026-12"), 500000)
        self.assertIsNone(storage.get_default_budget("2027-01"))
        self.assertIsNone(storage.get_default_budget("2027-08"))

    def test_month_budget_overrides_default(self):
        storage.set_default_budget(300000, "2026-11")
        storage.set_budget("2026-12", 880000)
        self.assertEqual(storage.get_budget("2026-12"), 880000)
        storage.set_budget("2026-12", None)
        self.assertIsNone(storage.get_budget("2026-12"))

    def test_budget_change_is_logged_and_undoable(self):
        before = storage.get_budget("2026-12")
        op_id = storage.log_budget("month", "2026-12", before, 660000)
        storage.set_budget("2026-12", 660000)

        row = [o for o in storage.operations(20) if o["id"] == op_id][0]
        self.assertEqual(row["kind"], "budget")
        self.assertIn("2026 年 12 月", row["tx_text"])
        self.assertIn("6600.00", row["tx_text"])

        storage.undo(op_id)
        self.assertEqual(storage.get_budget("2026-12"), before)
        again = [o for o in storage.operations(20) if o["id"] == op_id][0]
        self.assertEqual(again["undone"], 1)

    def test_budget_does_not_touch_spending(self):
        storage.add("午饭25", 2500, "2026-11-20")
        self.assertEqual(storage.month_total("2026-11"), 2500)
        storage.set_default_budget(123400, "2026-11")
        self.assertEqual(storage.month_total("2026-11"), 2500)


if __name__ == "__main__":
    unittest.main()

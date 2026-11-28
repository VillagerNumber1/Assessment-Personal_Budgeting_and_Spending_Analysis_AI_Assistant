"""账单导入：CSV 三种表头 + 编码 + Excel（.xlsx）+ 读不懂时的报错。"""
import unittest

import helpers
import importer
from xlsx_fixture import build_xlsx

ROOT = helpers.ROOT


class CsvImportTest(unittest.TestCase):
    def test_repo_demo_csv(self):
        """仓库里那份演示数据：225 条、跳过 3 条、认成支付宝。"""
        data = (ROOT / "samples" / "demo.csv").read_bytes()
        d = importer.parse_bytes(data, "demo.csv")
        self.assertEqual(d["source"], "alipay")
        self.assertEqual(d["header_row"], 5)
        self.assertEqual(d["total"], 225)
        self.assertEqual(d["skipped_total"], 3)
        self.assertTrue(any(r["kind"] == "income" for r in d["rows"]))
        self.assertTrue(all(isinstance(r["amount_cents"], int) for r in d["rows"]))

    def test_wechat_gbk_and_skips(self):
        """微信账单：GBK、表头前面有说明文字、不计收支和已退款要跳过。"""
        raw = (
            "微信支付账单明细\n微信昵称：[测试]\n"
            "交易时间,交易类型,交易对方,商品,收/支,金额(元),支付方式,当前状态,交易单号,商户单号,备注\n"
            "2026-11-01 12:00:00,商户消费,美团,外卖,支出,25.00,零钱,支付成功,1,2,\n"
            "2026-11-02 09:00:00,转账,张三,转账,不计收支,100.00,零钱,支付成功,3,4,\n"
            "2026-11-03 18:00:00,商户消费,滴滴,打车,支出,18.50,零钱,已全额退款,5,6,\n"
            "2026-11-04 20:00:00,红包,李四,红包,收入,66.00,零钱,支付成功,7,8,\n"
        ).encode("gbk")
        d = importer.parse_bytes(raw, "wechat.csv")
        self.assertEqual(d["source"], "wechat")
        self.assertEqual(d["encoding"], "gb18030")
        self.assertEqual(d["total"], 2)
        self.assertEqual(d["skipped_total"], 2)

    def test_generic_with_quoted_thousands(self):
        raw = ('时间,金额,收支,备注\n'
               '2026/11/05 08:30,"1,234.56",支出,买电脑\n').encode("utf-8")
        d = importer.parse_bytes(raw, "mine.csv")
        self.assertEqual(d["total"], 1)
        self.assertEqual(d["rows"][0]["amount_cents"], 123456)
        self.assertEqual(d["rows"][0]["occurred_at"], "2026-11-05 08:30")

    def test_broken_files_say_something_human(self):
        for raw, name in ((b"", "empty.csv"),
                          ("随便写点东西\n第二行\n".encode("utf-8"), "noheader.csv"),
                          ("时间,金额,备注\n".encode("utf-8"), "onlyheader.csv")):
            with self.subTest(name=name):
                with self.assertRaises(importer.ImportParseError):
                    importer.parse_bytes(raw, name)


class XlsxImportTest(unittest.TestCase):
    def test_reads_dates_and_numbers(self):
        d = importer.parse_bytes(build_xlsx(), "demo.xlsx")
        self.assertEqual(d["sheet"], "账单")
        self.assertEqual(d["total"], 3)
        self.assertEqual(d["rows"][0]["occurred_at"], "2026-11-25")       # 内置日期格式
        self.assertEqual(d["rows"][1]["occurred_at"], "2026-11-26 12:30")  # 自定义格式带时分
        self.assertEqual(d["rows"][2]["occurred_at"], "2026-11-28")       # 文本日期
        self.assertEqual(d["rows"][2]["amount_cents"], 123456)
        self.assertEqual(d["rows"][2]["kind"], "income")

    def test_bad_xlsx_is_human_readable(self):
        with self.assertRaises(importer.ImportParseError) as ctx:
            importer.parse_bytes(b"not a zip", "bad.xlsx")
        self.assertIn("Excel", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

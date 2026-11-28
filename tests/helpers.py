"""测试共用的小工具：临时库、临时设置、项目根目录。

所有测试都跑在临时目录里，**绝不碰 data/app.db**——评审在自己机器上跑也不会弄脏真数据。
临时目录放在项目里（`.test-tmp/`，已 gitignore），不用系统临时目录：
有些沙箱 / 受限环境对系统临时目录是只读的，sqlite 会直接报「unable to open database file」。
"""
import atexit
import shutil
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TMP = ROOT / ".test-tmp"
if TMP.exists():
    shutil.rmtree(TMP, ignore_errors=True)
TMP.mkdir(parents=True, exist_ok=True)
atexit.register(shutil.rmtree, TMP, ignore_errors=True)
os.environ["BILL_DB"] = str(TMP / "test.db")          # 必须在 import storage 之前设

import settings  # noqa: E402

settings.PATH = TMP / "settings.json"

import storage  # noqa: E402


def fresh_db() -> None:
    """清空临时库再建表。每个测试类开头调一次，用例之间不串数据。"""
    for old in TMP.glob("test.db*"):
        old.unlink(missing_ok=True)
    storage.init()

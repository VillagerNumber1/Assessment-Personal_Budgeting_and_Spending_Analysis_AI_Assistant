"""本地语音模型管理：文件完整性判断、开关缺省、识别器选择。

不联网：只测本地文件和分支，不真的去 ModelScope 下 1.5 GB。
临时目录放在项目 `.test-tmp/`（系统 temp 在沙箱里可能没权限），跑完清掉。
"""
import pathlib
import shutil
import unittest

import asr
import local_model

BASE = pathlib.Path(__file__).resolve().parent.parent / ".test-tmp" / "local-model"


class ModelReadyTest(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(BASE, ignore_errors=True)
        BASE.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(BASE, ignore_errors=True)

    def test_ready_only_when_core_files_exist(self):
        d = BASE / "model"
        d.mkdir()
        self.assertFalse(local_model.model_ready(d))
        (d / "model.bin").write_bytes(b"x")
        (d / "config.json").write_bytes(b"x")
        self.assertFalse(local_model.model_ready(d))      # 还差 tokenizer
        (d / "tokenizer.json").write_bytes(b"x")
        self.assertTrue(local_model.model_ready(d))

    def test_bytes_counts_files(self):
        d = BASE / "model"
        d.mkdir()
        self.assertEqual(local_model.model_bytes(d), 0)
        (d / "a.bin").write_bytes(b"1234")
        (d / "b.json").write_bytes(b"56")
        self.assertEqual(local_model.model_bytes(d), 6)

    def test_best_dir_prefers_medium(self):
        root = BASE / "root"
        small = root / "models" / "faster-whisper-small"
        medium = root / "models" / "faster-whisper-medium"
        for d in (small, medium):
            d.mkdir(parents=True)
            for name in ("model.bin", "config.json", "tokenizer.json"):
                (d / name).write_bytes(b"x")
        old = local_model.BASE_DIR
        local_model.BASE_DIR = root
        try:
            self.assertEqual(local_model.best_model_dir(), medium)
        finally:
            local_model.BASE_DIR = old


class TranscriberChoiceTest(unittest.TestCase):
    def test_local_off_raises(self):
        with self.assertRaises(RuntimeError):
            asr.create_transcriber("local", "sk-test", local_enabled=False)

    def test_auto_without_key_and_local_off_raises(self):
        with self.assertRaises(RuntimeError):
            asr.create_transcriber("auto", "", local_enabled=False)

    def test_cloud_with_key_wins_in_auto(self):
        t = asr.create_transcriber("auto", "sk-test", local_enabled=True)
        self.assertIsInstance(t, asr.CloudASR)

    def test_no_key_falls_back_to_local(self):
        t = asr.create_transcriber("auto", "", local_enabled=True)
        self.assertIsInstance(t, asr.LocalWhisper)


if __name__ == "__main__":
    unittest.main()

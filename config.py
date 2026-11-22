"""项目级常量：路径和参数集中在这里。

好处：以后改采样率、改模型目录、加 .env 配置，只动这一个文件。
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent


def _load_env_file(path: Path) -> None:
    """把 .env 里的 KEY=VALUE 读进环境变量。

    只认最朴素的格式：# 开头是注释，其余一行一个 KEY=VALUE。
    不引 python-dotenv，省一个依赖——这功能 10 行就够。
    已存在的环境变量优先，所以命令行临时设置能覆盖 .env。
    """
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_env_file(BASE_DIR / ".env")

# 密钥只从环境变量（或本地 .env）来，绝不写进代码、绝不提交
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()

# 语音识别走哪条路：local / cloud / auto
ASR_BACKEND = os.environ.get("ASR_BACKEND", "auto").strip().lower()


def save_env(updates: dict[str, str]) -> None:
    """把若干键写回 .env，保留其它行和注释。

    设置窗口点「保存」时调这个。已经存在的键原地替换，没有的追加到末尾。
    """
    path = BASE_DIR / ".env"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    written: set[str] = set()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                out.append(f"{key}={updates[key]}")
                written.add(key)
                continue
        out.append(line)
    for key, value in updates.items():
        if key not in written:
            out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")

# 数据目录：录音、以后的数据库都放这里（已 gitignore）
DATA_DIR = BASE_DIR / "data"
AUDIO_DIR = DATA_DIR / "recordings"

# 模型目录：由 download_model.py 生成（已 gitignore）
MODEL_DIR = BASE_DIR / "models" / "faster-whisper-small"

SAMPLE_RATE = 16_000     # 录音采样率，也是 ASR 要求的采样率
MAX_SECONDS = 60         # 一次录音最长多少秒
MIN_SECONDS = 0.3        # 短于这个就当作误触，不存

# 桌面原型已冻结并搬进 Legacy_OutOfDate/，音效跟着一起搬了
LEGACY_DIR = BASE_DIR / "Legacy_OutOfDate"
SOUND_DIR = LEGACY_DIR / "audio_effects"

# 识别完成后自动发送；改成 False 就退回「填进输入框，等你按回车确认」
AUTO_SEND = True

# 峰值低于这个值就当作「没录到声音」，直接跳过识别（int16 满量程 32767）
SILENCE_PEAK = 100

"""项目级常量：路径和参数集中在这里。

好处：以后改采样率、改模型目录、加 .env 配置，只动这一个文件。
"""
from pathlib import Path

BASE_DIR = Path(__file__).parent

# 数据目录：录音、以后的数据库都放这里（已 gitignore）
DATA_DIR = BASE_DIR / "data"
AUDIO_DIR = DATA_DIR / "recordings"

# 模型目录：由 download_model.py 生成（已 gitignore）
MODEL_DIR = BASE_DIR / "models" / "faster-whisper-small"

SAMPLE_RATE = 16_000     # 录音采样率，也是 ASR 要求的采样率
MAX_SECONDS = 60         # 一次录音最长多少秒
MIN_SECONDS = 0.3        # 短于这个就当作误触，不存

# 界面音效：开始录音、结束录音
SOUND_DIR = BASE_DIR / "audio_effects"

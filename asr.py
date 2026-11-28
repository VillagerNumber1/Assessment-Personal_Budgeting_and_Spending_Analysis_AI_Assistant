"""语音识别组件：把一段 wav 变成一句话。

只剩云端一种实现（DeepSeek `/v1/tools/asr`）。本地 whisper 在 2026-11-28 拆掉了：
模型 1.5 GB、Docker 镜像装不下、还要维护下载器，收益不值当。
没配 key 时就明确报「未配置 API Key」，不再回退到本地。

    接口限制：16 位 PCM WAV 或 Ogg Opus，最大 8 MiB，最长 120 秒。
    我们录的是 16 kHz 单声道 PCM16、60 秒封顶，天然满足。
    计费按音频时长：0.78 元/小时（限时 5 折）。
"""
from pathlib import Path

from config import DEEPSEEK_API_KEY

# whisper 的中文输出经常是繁体（小模型尤其明显），装了 opencc 就顺手转成简体。
# 没装也不影响运行，只是可能拿到「計程車」这种结果。
try:
    import opencc
    _T2S = opencc.OpenCC("t2s")
except Exception:
    _T2S = None


class Transcriber:
    """识别器接口：给一个 wav 路径，还我一句话。"""

    name = "base"

    def transcribe(self, wav: Path, language: str | None = "zh") -> str:
        raise NotImplementedError("子类必须实现 transcribe()")


class CloudASR(Transcriber):
    """DeepSeek 在线识别：POST /v1/tools/asr，multipart 上传一段 wav。"""

    name = "DeepSeek 云端"
    endpoint = "https://api.deepseek.com/v1/tools/asr"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = (api_key if api_key is not None else DEEPSEEK_API_KEY).strip()
        if not self.api_key:
            raise RuntimeError(
                "没有配置 API Key：语音识别要用 DeepSeek 云端，"
                "在「设置 → 模型」里填 key（或写进项目根目录的 .env）")

    def transcribe(self, wav: Path, language: str | None = "zh") -> str:
        import requests                      # 只有走云端才需要它

        with open(wav, "rb") as fh:
            resp = requests.post(
                self.endpoint,
                headers={"Authorization": f"Bearer {self.api_key}"},
                files={"file": (Path(wav).name, fh, "audio/wav")},
                timeout=60,
            )
        if resp.status_code != 200:
            raise RuntimeError(f"云端识别返回 {resp.status_code}：{resp.text[:200]}")

        text = (resp.json().get("text") or "").strip()
        return _T2S.convert(text) if _T2S is not None else text


def create_transcriber(api_key: str | None = None) -> Transcriber:
    """固定用云端。api_key 传 None 表示用 .env 里的。"""
    return CloudASR(api_key if api_key is not None else DEEPSEEK_API_KEY)

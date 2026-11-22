"""语音识别组件：把一段 wav 变成一句话。

现在只有本地 whisper 一种实现。以后要加云端（DeepSeek /v1/tools/asr）时，
照着 Transcriber 的接口再写一个类，界面那边只改「创建哪一行」。

    from asr import LocalWhisper, CloudASR
    self.transcriber = LocalWhisper(MODEL_DIR)      # 现在
    self.transcriber = CloudASR(api_key=...)        # 以后，一行之差
"""
from pathlib import Path

import numpy as np
import soundfile as sf

from config import ASR_BACKEND, DEEPSEEK_API_KEY, MODEL_DIR, SAMPLE_RATE

# whisper 的中文输出经常是繁体（小模型尤其明显），装了 opencc 就顺手转成简体。
# 没装也不影响运行，只是可能拿到「計程車」这种结果。
try:
    import opencc
    _T2S = opencc.OpenCC("t2s")
except Exception:
    _T2S = None


def to_16k(audio: np.ndarray, sr: int) -> np.ndarray:
    """重采样到 16 kHz。我们自己的录音本来就是 16k，这里只是兜底。"""
    if sr == SAMPLE_RATE:
        return audio.astype(np.float32)
    n = int(len(audio) * SAMPLE_RATE / sr)
    old = np.linspace(0, 1, len(audio), endpoint=False)
    new = np.linspace(0, 1, n, endpoint=False)
    return np.interp(new, old, audio).astype(np.float32)


class Transcriber:
    """识别器接口：给一个 wav 路径，还我一句话。"""

    name = "base"

    def transcribe(self, wav: Path, language: str | None = "zh") -> str:
        raise NotImplementedError("子类必须实现 transcribe()")


class LocalWhisper(Transcriber):
    """本地 faster-whisper。第一次识别时加载模型，之后常驻内存。"""

    name = "本地 whisper"

    def __init__(self, model_dir: Path = MODEL_DIR, compute_type: str = "int8") -> None:
        self.model_dir = model_dir
        self.compute_type = compute_type
        self._model = None

    def load(self):
        if self._model is None:
            from faster_whisper import WhisperModel   # 延迟 import，启动快一点
            if not self.model_dir.exists():
                raise FileNotFoundError(
                    f"找不到模型目录：{self.model_dir.name}\n"
                    "先运行：python download_model.py")
            self._model = WhisperModel(str(self.model_dir),
                                       device="cpu", compute_type=self.compute_type)
        return self._model

    def transcribe(self, wav: Path, language: str | None = "zh") -> str:
        model = self.load()
        # 用 soundfile 读成数组再喂模型，绕开 faster-whisper 内部的 PyAV 解码：
        # 新版 PyAV(19) 去掉了 metadata_errors 参数，1.2.1 还在传，会直接报错。
        audio, sr = sf.read(str(wav), dtype="float32")
        if audio.ndim > 1:                 # 双声道就混成单声道
            audio = audio.mean(axis=1)
        audio = to_16k(audio, sr)

        segments, _info = model.transcribe(
            audio, language=language, beam_size=5, vad_filter=True,
            initial_prompt="以下是普通话的句子，可能包含金额、日期和地点。",
        )

        # whisper 在静音/噪声上会「编」出「感谢观看」「谢谢」这类句子。
        # no_speech_prob 是模型自己给的「这段是不是人声」概率，用它挡掉。
        # avg_logprob 太低说明模型自己也没把握，一起挡——但阈值不能太严：
        # 实测 1~2 秒的短句经常只有 -1.4，放宽到 -1.5 才不会误杀真话。
        parts: list[str] = []
        for seg in segments:
            if seg.no_speech_prob > 0.6 or seg.avg_logprob < -1.5:
                continue
            parts.append(seg.text)

        text = "".join(parts).strip()
        return _T2S.convert(text) if _T2S is not None else text


class CloudASR(Transcriber):
    """DeepSeek 在线识别：POST /v1/tools/asr，multipart 上传一段 wav。

    接口限制：16 位 PCM WAV 或 Ogg Opus，最大 8 MiB，最长 120 秒。
    我们录的是 16 kHz 单声道 PCM16、60 秒封顶，天然满足。
    计费按音频时长：0.78 元/小时（限时 5 折）。
    """

    name = "DeepSeek 云端"
    endpoint = "https://api.deepseek.com/v1/tools/asr"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = (api_key if api_key is not None else DEEPSEEK_API_KEY).strip()
        if not self.api_key:
            raise RuntimeError(
                "没有配置 API key：把 DEEPSEEK_API_KEY 写进项目根目录的 .env"
                "（可以先复制 .env.example）")

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


def create_transcriber(backend: str = ASR_BACKEND) -> Transcriber:
    """按配置挑一个识别器，界面只需要调这个函数。

    当前规则（暂定）：
        有 key   → 云端；云端失败直接抛错，由界面弹窗并退出，**不静默回退**
        没有 key → 本地 whisper

    以后想改成"云端失败自动退回本地"，加一个组合类把两个引擎串起来即可。
    """
    if backend == "local":
        return LocalWhisper()
    if backend == "cloud":
        return CloudASR()
    return CloudASR() if DEEPSEEK_API_KEY else LocalWhisper()

"""麦克风录音：只管把声音收进来，不碰界面、不做识别。

界面那边只需要三个动作：start() / elapsed() / stop()。
"""
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

from config import SAMPLE_RATE


class Recorder:
    """录音机。回调在 PortAudio 自己的线程里跑，所以这里绝对不要动 Tk。"""

    def __init__(self, samplerate: int = SAMPLE_RATE) -> None:
        self.samplerate = samplerate
        self.frames: list[np.ndarray] = []
        self.stream: sd.InputStream | None = None
        self.started_at = 0.0

    @property
    def is_recording(self) -> bool:
        return self.stream is not None

    def start(self) -> None:
        """开始录音。设备被占用会直接抛异常，让界面去提示。"""
        self.frames = []
        self.started_at = time.monotonic()
        self.stream = sd.InputStream(
            samplerate=self.samplerate,
            channels=1,
            dtype="int16",
            callback=self._callback,
        )
        self.stream.start()

    def _callback(self, indata, _frames, _time_info, _status) -> None:
        # 这里只做「存起来」这一件事，越快越好
        self.frames.append(indata.copy())

    def elapsed(self) -> float:
        """已经录了多少秒。"""
        return time.monotonic() - self.started_at

    def stop(self) -> np.ndarray:
        """停止录音，返回 (采样点数, 1) 的 int16 数组；没录到东西就是空数组。"""
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        if not self.frames:
            return np.zeros((0, 1), dtype="int16")
        return np.concatenate(self.frames, axis=0)


def save_wav(data: np.ndarray, path: Path, samplerate: int = SAMPLE_RATE) -> None:
    """把录音写成 16 位 PCM WAV —— 识别库只认这个格式。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), data, samplerate, subtype="PCM_16")

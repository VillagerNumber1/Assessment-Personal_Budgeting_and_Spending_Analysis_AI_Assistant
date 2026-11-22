"""界面音效：开始录音响一声，结束再响一声。

用 winsound —— Python 标准库，零安装，Windows 自带，异步播放不卡界面。
（pyautogui 是鼠标键盘自动化，跟放声音无关；sounddevice 我们虽然装了，
  但为了一声 0.6 秒的提示音去建音频流，属于杀鸡用牛刀。）
"""
from pathlib import Path

try:
    import winsound
except ImportError:      # 非 Windows 上就没有，安静跳过
    winsound = None


def play(path: Path) -> None:
    """异步播放，立刻返回。文件不存在或系统不支持就什么都不做。"""
    if winsound is None or not path.exists():
        return
    try:
        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
    except RuntimeError:
        pass


def duration(path: Path) -> float:
    """音效有多长（秒）。读不到就当 0，调用方自己兜底。"""
    try:
        import soundfile as sf
        return float(sf.info(str(path)).duration)
    except Exception:
        return 0.0

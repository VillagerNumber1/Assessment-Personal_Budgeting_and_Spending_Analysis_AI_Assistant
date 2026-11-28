r"""本地语音模型管理：从 ModelScope 拉 faster-whisper-medium，带进度和断点续传。

为什么不装 modelscope SDK：
    这里只需要「按文件下载 + 续传」这一件事。SDK 会带 tqdm / datasets 一串依赖，
    Docker 镜像也跟着变大；直接用它的 HTTP 接口，少一个依赖，效果一样。

仓库：https://www.modelscope.cn/models/Systran/faster-whisper-medium
文件：config.json / model.bin / tokenizer.json / vocabulary.txt（共约 1.46 GB）
"""
import importlib.util
import shutil
import threading
from pathlib import Path

import requests

from config import BASE_DIR

REPO = "Systran/faster-whisper-medium"
API = f"https://www.modelscope.cn/api/v1/models/{REPO}"
TARGET = BASE_DIR / "models" / "faster-whisper-medium"

# 只下推理需要的文件：README / .gitattributes 之类不要
KEEP = {"config.json", "model.bin", "tokenizer.json", "vocabulary.txt",
        "configuration.json", "preprocessor_config.json"}
CHUNK = 1024 * 1024

_state = {
    "running": False, "percent": 0.0, "downloaded": 0, "total": 0,
    "file": "", "error": "", "done": False,
}
_lock = threading.Lock()
_thread: threading.Thread | None = None
_cancel = threading.Event()


def engine_available() -> bool:
    """当前环境装没装 faster-whisper。Docker 镜像里没有它。"""
    return importlib.util.find_spec("faster_whisper") is not None


def model_ready(directory: Path | None = None) -> bool:
    """三个核心文件都在，才算下载完整。"""
    d = Path(directory or TARGET)
    return all((d / name).exists() for name in ("model.bin", "config.json", "tokenizer.json"))


def model_bytes(directory: Path | None = None) -> int:
    d = Path(directory or TARGET)
    if not d.exists():
        return 0
    return sum(p.stat().st_size for p in d.glob("*") if p.is_file())


def best_model_dir() -> Path | None:
    """优先 medium；老用户之前下过 small 的，还能继续用。"""
    for name in ("faster-whisper-medium", "faster-whisper-small"):
        d = BASE_DIR / "models" / name
        if model_ready(d):
            return d
    return None


def status() -> dict:
    with _lock:
        state = dict(_state)
    ready = model_ready()
    if ready and not state["running"]:
        size = model_bytes()
        state.update(done=True, percent=100.0, downloaded=size, total=size)
    active = best_model_dir()          # 老用户可能已经下过 small，先让他能用
    state.update({
        "engine": "faster-whisper",
        "engine_available": engine_available(),
        "model_ready": ready,
        "active_model": active.name if active else "",
        "active_size": model_bytes(active) if active else 0,
        "size": model_bytes(),
        "path": str(TARGET),
        "repo": REPO,
    })
    return state


def start_download() -> dict:
    global _thread
    with _lock:
        if _state["running"]:
            return dict(_state)
        if model_ready():
            return dict(_state)
        _state.update(running=True, percent=0.0, downloaded=0, total=0,
                      file="", error="", done=False)
    _cancel.clear()
    _thread = threading.Thread(target=_download_all, daemon=True)
    _thread.start()
    return status()


def cancel_download() -> dict:
    _cancel.set()
    return status()


def remove_model() -> None:
    if _state["running"]:
        raise RuntimeError("正在下载，先等它结束（或者取消）再删")
    shutil.rmtree(TARGET, ignore_errors=True)
    with _lock:
        _state.update(done=False, percent=0.0, downloaded=0, total=0, error="")


def _fetch_files() -> list[dict]:
    resp = requests.get(f"{API}/repo/files?Revision=master", timeout=25)
    resp.raise_for_status()
    files = resp.json().get("Data", {}).get("Files", [])
    picked = [f for f in files if f.get("Name") in KEEP]
    if not picked:
        raise RuntimeError("ModelScope 没返回可用的模型文件（接口格式可能变了）")
    return picked


def _download_all() -> None:
    try:
        files = _fetch_files()
        total = sum(int(f.get("Size") or 0) for f in files)
        with _lock:
            _state["total"] = total

        TARGET.mkdir(parents=True, exist_ok=True)
        finished = 0
        for meta in files:
            if _cancel.is_set():
                raise RuntimeError("下载已取消")
            finished = _download_one(meta, total, finished)
            with _lock:
                _state.update(downloaded=finished)
                _state["percent"] = round(finished * 100 / total, 1) if total else 0.0

        if not model_ready():
            raise RuntimeError("文件下完了但模型不完整，删掉重试一次")
        with _lock:
            _state.update(running=False, done=True, percent=100.0, file="", error="")
    except Exception as exc:                     # 网络、磁盘、接口格式，全给用户看
        with _lock:
            _state.update(running=False, error=f"{type(exc).__name__}: {exc}")
    finally:
        _cancel.clear()


def _download_one(meta: dict, total: int, finished: int) -> int:
    """下单个文件，返回「已完成的总字节数」。.part + Range 实现断点续传。"""
    name = meta["Name"]
    expected = int(meta.get("Size") or 0)
    target = TARGET / name
    part = TARGET / (name + ".part")

    if target.exists() and (not expected or target.stat().st_size == expected):
        return finished + target.stat().st_size
    target.unlink(missing_ok=True)

    have = part.stat().st_size if part.exists() else 0
    if expected and have > expected:
        part.unlink(missing_ok=True)
        have = 0

    headers = {"Range": f"bytes={have}-"} if have else {}
    with _lock:
        _state.update(file=name)

    url = f"{API}/repo?Revision=master&FilePath={name}"
    with requests.get(url, headers=headers, stream=True, timeout=60) as resp:
        if resp.status_code not in (200, 206):
            raise RuntimeError(f"下载 {name} 失败：HTTP {resp.status_code}")
        if have and resp.status_code != 206:     # 服务端不支持续传，从头来
            have = 0
        mode = "ab" if have else "wb"
        with open(part, mode) as fh:
            for chunk in resp.iter_content(CHUNK):
                if _cancel.is_set():
                    raise RuntimeError("下载已取消")
                fh.write(chunk)
                have += len(chunk)
                with _lock:
                    _state.update(
                        downloaded=finished + have,
                        percent=round((finished + have) * 100 / total, 1) if total else 0.0,
                    )

    size = part.stat().st_size
    if expected and size != expected:
        raise RuntimeError(f"{name} 大小不对：{size} != {expected}")
    part.replace(target)
    return finished + size

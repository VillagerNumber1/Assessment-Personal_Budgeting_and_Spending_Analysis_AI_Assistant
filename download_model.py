r"""下载 faster-whisper 模型（国内走 hf-mirror，不用梯子）。

用法：
    .\.venv\Scripts\python.exe download_model.py          # 默认 small（约 480 MB）
    .\.venv\Scripts\python.exe download_model.py base     # 小一号，约 145 MB
    .\.venv\Scripts\python.exe download_model.py tiny     # 最快，效果一般

为什么不直接用 huggingface_hub：
它默认把缓存写到 C:\\Users\\<你>\\.cache\\huggingface，国内还连不上 HF。
这里直接按 URL 取文件，存成普通文件夹，之后 gui.py 直接读，不再联网。

文件落到 models/faster-whisper-<名字>/，可以整个拷走，换机器也能用。
"""
import sys
import time
import urllib.request
from pathlib import Path

MIRRORS = [
    "https://hf-mirror.com",     # 国内镜像，优先
    "https://huggingface.co",    # 备用
]
FILES = ["config.json", "model.bin", "tokenizer.json", "vocabulary.txt"]
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
ROOT = Path(__file__).parent / "models"


def url_of(base: str, repo: str, name: str) -> str:
    return f"{base}/{repo}/resolve/main/{name}"


def remote_size(base: str, repo: str, name: str) -> int:
    req = urllib.request.Request(url_of(base, repo, name),
                                 headers={"User-Agent": UA}, method="HEAD")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return int(resp.headers.get("Content-Length") or 0)


def fetch(base: str, repo: str, name: str, target: Path) -> None:
    """下到 .part，完成后改名。支持续传。"""
    part = target.parent / (target.name + ".part")
    start = part.stat().st_size if part.exists() else 0
    total = remote_size(base, repo, name)
    if start >= total and total > 0:
        part.replace(target)
        print(f"  {name}  已存在，跳过")
        return

    headers = {"User-Agent": UA}
    if start:
        headers["Range"] = f"bytes={start}-"
        print(f"  {name}  从 {start/1024/1024:.1f} MB 续传")

    req = urllib.request.Request(url_of(base, repo, name), headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp, open(part, "ab") as fh:
        done = start
        t0 = time.time()
        while True:
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            fh.write(chunk)
            done += len(chunk)
            dt = time.time() - t0
            speed = (done - start) / dt / 1024 if dt > 0 else 0
            eta = (total - done) / (speed * 1024) if speed > 0 else 0
            print(f"\r  {name}  {done/1024/1024:7.1f}/{total/1024/1024:.1f} MB"
                  f"  {speed:6.0f} KB/s  ETA {int(eta)//60:02d}:{int(eta)%60:02d}", end="")
    print()
    part.replace(target)


def main() -> None:
    size = sys.argv[1] if len(sys.argv) > 1 else "small"
    repo = f"Systran/faster-whisper-{size}"
    target_dir = ROOT / f"faster-whisper-{size}"
    target_dir.mkdir(parents=True, exist_ok=True)

    print(f"模型 {repo} -> {target_dir}")
    for name in FILES:
        target = target_dir / name
        if target.exists() and target.stat().st_size > 0:
            print(f"  {name}  已存在，跳过")
            continue
        for base in MIRRORS:
            try:
                fetch(base, repo, name, target)
                break
            except Exception as exc:
                print(f"\n  从 {base} 下载失败：{exc}")
        else:
            raise SystemExit(f"{name} 所有源都失败了，检查网络后重跑")

    print("\n完成。文件清单：")
    for name in FILES:
        p = target_dir / name
        print(f"  {p.name:16} {p.stat().st_size/1024/1024:8.1f} MB")


if __name__ == "__main__":
    main()

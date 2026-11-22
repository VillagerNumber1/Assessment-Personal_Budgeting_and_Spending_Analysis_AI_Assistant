"""记账小助手 · 桌面原型（界面部分）。

这个文件只负责界面和流程，具体能力都在别的组件里：
    config.py     常量和路径
    recorder.py   录音、存 wav
    asr.py        语音识别（现在本地 whisper，以后可换云端）

跑法：python gui.py   或者双击 run.bat
"""
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox
from datetime import datetime

from asr import create_transcriber
from config import (ASR_BACKEND, AUDIO_DIR, AUTO_SEND, DEEPSEEK_API_KEY,
                    MAX_SECONDS, MIN_SECONDS, SAMPLE_RATE, SILENCE_PEAK,
                    SOUND_DIR, save_env)
from recorder import Recorder, peak_level, save_wav
from sfx import duration, play


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.lines: list[str] = []
        self.recorder = Recorder()
        self.transcriber = create_transcriber()    # 按 .env 里的 ASR_BACKEND 挑
        self.results: queue.Queue = queue.Queue()  # 后台线程 -> 界面，只走这个队列
        self._starting = False                     # 正在等开始音效放完
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll_results()

    # ---------- 界面 ----------
    def _build_ui(self) -> None:
        self.root.title("记账小助手")
        self.root.geometry("420x560")
        self.root.minsize(360, 460)
        self._build_menu()

        self.log = tk.Text(self.root, wrap="word", state="disabled",
                           bg="#f5f5f5", relief="flat", padx=12, pady=12)
        self.log.pack(fill="both", expand=True, padx=10, pady=(10, 6))

        row = tk.Frame(self.root)
        row.pack(fill="x", padx=10)

        self.entry = tk.Entry(row, font=("Microsoft YaHei UI", 11))
        self.entry.pack(side="left", fill="x", expand=True, ipady=6)
        self.entry.bind("<Return>", lambda _e: self.send())
        self.entry.focus_set()

        tk.Button(row, text="发送", width=6,
                  command=self.send).pack(side="left", padx=(6, 0))

        self.voice_btn = tk.Button(self.root, text="点一下说话（再点结束）",
                                   command=self.toggle_recording)
        self.voice_btn.pack(fill="x", padx=10, pady=6)

        # 退出：Esc 一下，或者输入框里敲 q / quit 回车（沿用命令行时的习惯）
        self.root.bind("<Escape>", lambda _e: self._on_close())

        self.status = tk.StringVar(value="准备就绪")
        tk.Label(self.root, textvariable=self.status, anchor="w",
                 fg="#888").pack(fill="x", padx=12, pady=(0, 8))

    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="设置…", command=self.open_settings)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self._on_close)
        menubar.add_cascade(label="文件", menu=file_menu)
        self.root.config(menu=menubar)

    def open_settings(self) -> None:
        """设置窗口：填 key、选识别方式，保存到 .env。

        注意：保存不会立刻生效——config 是启动时读的，需要重启程序。
        """
        win = tk.Toplevel(self.root)
        win.title("设置")
        win.geometry("440x360")
        win.transient(self.root)
        win.grab_set()

        tk.Label(win, text="DeepSeek API Key", anchor="w",
                 font=("Microsoft YaHei UI", 10, "bold")).pack(fill="x", padx=16, pady=(16, 4))
        key_var = tk.StringVar(value=DEEPSEEK_API_KEY)
        entry = tk.Entry(win, textvariable=key_var, show="*")
        entry.pack(fill="x", padx=16, ipady=4)

        show_var = tk.BooleanVar(value=False)
        tk.Checkbutton(win, text="显示明文", variable=show_var,
                       command=lambda: entry.configure(show="" if show_var.get() else "*")
                       ).pack(anchor="w", padx=12)

        if DEEPSEEK_API_KEY:
            masked = DEEPSEEK_API_KEY[:5] + "****" + DEEPSEEK_API_KEY[-4:]
            hint, color = f"当前已配置：{masked}", "#4a8"
        else:
            hint, color = "当前未配置 —— 会使用本地识别", "#c80"
        tk.Label(win, text=hint, fg=color, anchor="w").pack(fill="x", padx=16, pady=(0, 12))

        tk.Label(win, text="语音识别方式", anchor="w",
                 font=("Microsoft YaHei UI", 10, "bold")).pack(fill="x", padx=16)
        backend_var = tk.StringVar(value=ASR_BACKEND)
        for value, text in (
            ("auto", "自动：有 key 用云端，没有就用本地"),
            ("local", "只用本地 whisper（不需要 key）"),
            ("cloud", "只用云端（失败会弹窗并退出）"),
        ):
            tk.Radiobutton(win, text=text, value=value,
                           variable=backend_var).pack(anchor="w", padx=28)

        tk.Label(win, text="保存后需要重启程序才生效", fg="#888",
                 anchor="w").pack(fill="x", padx=16, pady=(12, 0))

        def save() -> None:
            save_env({"DEEPSEEK_API_KEY": key_var.get().strip(),
                      "ASR_BACKEND": backend_var.get()})
            messagebox.showinfo("已保存", "配置已写入 .env，重启程序后生效。", parent=win)
            win.destroy()

        row = tk.Frame(win)
        row.pack(fill="x", padx=16, side="bottom", pady=14)
        tk.Button(row, text="取消", width=8, command=win.destroy).pack(side="right")
        tk.Button(row, text="保存", width=8, command=save).pack(side="right", padx=(0, 8))

    def _append_log(self, line: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", line + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    # ---------- 打字 ----------
    def send(self) -> None:
        text = self.entry.get().strip()
        if not text:
            return
        if text.lower() in {"q", "quit"}:      # 沿用命令行时的退出习惯
            self._on_close()
            return
        self.entry.delete(0, "end")
        self._commit(text)

    def _commit(self, text: str) -> None:
        """真正记一条。打字和语音都走这里。"""
        self.lines.append(text)
        self._append_log(f"[{len(self.lines)}] {text}")
        self.status.set(f"已记 {len(self.lines)} 条")

    # ---------- 录音 ----------
    def toggle_recording(self) -> None:
        """点一下开始，再点一下结束——同一个按钮负责开和关。"""
        if self.recorder.is_recording:
            self._finish_recording()
        elif self._starting:
            return                      # 音效还没放完，当没点
        else:
            self._start_with_sound()

    def _start_with_sound(self) -> None:
        """先把开始音效放完，再开麦——不然那声「叮」会被一起录进去。"""
        self._starting = True
        self.voice_btn.configure(state="disabled", text="准备中…")
        self.status.set("准备中…")
        sound = SOUND_DIR / "start.wav"
        play(sound)
        wait_ms = int(duration(sound) * 1000) + 50
        self.root.after(wait_ms, self._begin_capture)

    def _begin_capture(self) -> None:
        self._starting = False
        try:
            self.voice_btn.configure(state="normal")
        except tk.TclError:             # 等待期间窗口已经关了
            return
        try:
            self.recorder.start()
        except Exception as exc:
            self.status.set(f"录音失败：{exc}")
            return
        self.voice_btn.configure(relief="sunken", text="● 录音中…再点一下结束")
        self._tick()

    def _tick(self) -> None:
        if not self.recorder.is_recording:
            return
        elapsed = self.recorder.elapsed()
        if elapsed >= MAX_SECONDS:
            self._finish_recording()
            return
        self.status.set(f"● 录音中 {elapsed:4.1f}s / {MAX_SECONDS}s（再点一下结束）")
        self.root.after(100, self._tick)

    def _finish_recording(self) -> None:
        data = self.recorder.stop()
        play(SOUND_DIR / "stop.wav")
        self.voice_btn.configure(relief="raised", text="点一下说话（再点结束）")

        seconds = len(data) / SAMPLE_RATE
        if seconds < MIN_SECONDS:
            self.status.set(f"太短了（{seconds:.1f}s），没存")
            return
        if peak_level(data) < SILENCE_PEAK:
            self.status.set("没录到声音——麦克风可能被静音，或被别的程序占用")
            return

        path = AUDIO_DIR / f"rec_{datetime.now():%Y%m%d_%H%M%S}.wav"
        save_wav(data, path)
        self._append_log(f"[语音] {seconds:.1f}s  {path.name}")
        self._start_transcription(path)

    # ---------- 识别 ----------
    def _start_transcription(self, wav) -> None:
        self.voice_btn.configure(state="disabled")
        self.status.set("识别中…（第一次要加载模型，可能十几秒）")

        def work() -> None:
            t0 = time.time()
            try:
                text = self.transcriber.transcribe(wav)
                self.results.put(("ok", text, time.time() - t0))
            except Exception as exc:
                self.results.put(("err", str(exc), time.time() - t0))

        threading.Thread(target=work, daemon=True).start()

    def _poll_results(self) -> None:
        """主线程每 100 毫秒看一眼队列——Tk 只能主线程动。"""
        try:
            kind, payload, cost = self.results.get_nowait()
        except queue.Empty:
            pass
        else:
            self.voice_btn.configure(state="normal")
            if kind == "ok" and payload:
                if AUTO_SEND:
                    self._commit(payload)          # 识别完直接记一条，不用按回车
                    self.status.set(
                        f"已记 {len(self.lines)} 条（识别 {cost:.1f}s，自动发送）")
                else:
                    self.entry.delete(0, "end")
                    self.entry.insert(0, payload)
                    self.entry.focus_set()
                    self.status.set(f"识别完成（{cost:.1f}s），确认后按回车发送")
            elif kind == "ok":
                self.status.set("没听清，再说一次？")
            else:
                # 暂定策略：识别失败就弹窗并退出程序，方便立刻发现问题。
                # 以后想改成「自动退回本地」，把这里换成降级重试即可。
                self.status.set(f"识别失败：{payload}")
                messagebox.showerror("识别失败", payload)
                self._on_close()
        self.root.after(100, self._poll_results)

    def _on_close(self) -> None:
        if self.recorder.is_recording:
            self.recorder.stop()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()

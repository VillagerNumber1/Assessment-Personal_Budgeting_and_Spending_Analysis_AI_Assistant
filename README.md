# 个人记账与消费分析 AI 助手

> 2026 GDUF 人工智能工作室二轮考核 · 方向一
>
> 目标链路：**一句话 / 一张小票 → 结构化账目 → 数据库 → 统计 / 查账 / 预算**

## 现在到哪了

这是个**开发中的项目**，不是完成品。当前能跑通的是"语音录入"这条支线，记账主链路还没接上。

| 模块 | 状态 |
| --- | --- |
| 桌面界面（Tkinter） | ✅ 可跑 |
| 打字记账 | ✅ 可跑（仅内存，关窗口即失） |
| 点击录音 + 本地语音识别 | ✅ 可跑 |
| SQLite 持久化 | ⏳ 未开始 |
| 自动分类与统计 | ⏳ 未开始 |
| 自然语言查账 | ⏳ 未开始 |
| 预算与超支提醒 | ⏳ 未开始 |
| 小票识别 | ⏳ 未开始 |
| Docker 部署 | ⏳ 未开始 |

## 环境要求

- **Windows 10 / 11**（界面音效用的是 `winsound`；其他平台不会崩，但没测过）
- **Python 3.14**（开发环境 3.14.8，见 `requirements.lock.txt`）
- 麦克风（只有语音录入需要）

## 快速开始

```powershell
# 1. 建虚拟环境
python -m venv .venv

# 2. 装依赖（requirements.txt 首行已指向清华源，境外网络可删掉那行）
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 3. 下载语音模型（约 480 MB，走 hf-mirror，支持断点续传）
.\.venv\Scripts\python.exe download_model.py

# 4. 启动
run.bat
```

等价于 `.\.venv\Scripts\python.exe gui.py`。

第一次点录音会加载模型，约 10 秒；之后常驻内存。

## 怎么用

- **打字**：输入框写一句，回车 → 列表多一条
- **语音**：点「点一下说话」→ 响一声后开始录音 → 再点一次结束（或 60 秒自动停）→ 识别结果自动填进输入框 → 回车确认
- **退出**：`Esc`，或在输入框敲 `q` 回车

## 目录结构

```
config.py            常量：路径、采样率、时长上限
recorder.py          麦克风录音、写 wav
asr.py               语音识别（本地 faster-whisper）
sfx.py               界面音效（winsound，标准库）
gui.py               界面与流程编排
download_model.py    从 hf-mirror 下载模型，断点续传
run.bat              双击启动
requirements.txt     直接依赖，锁版本
requirements.lock.txt 完整依赖快照（pip freeze）
main.py              最早的命令行版，未接入界面
storage.py           存储层占位，下一步重写
```

## 技术说明

### 模型权重不进仓库

`models/faster-whisper-small/` 有 461 MB，用 `download_model.py` 拉取。文件落到本地文件夹（不走 huggingface 默认缓存），可以整个拷走换机器用。

### 为什么不直接把 wav 路径喂给 faster-whisper

PyAV 19 删掉了 `metadata_errors` 参数，而 faster-whisper 1.2.1 还在传，用文件路径直接转写会崩。这里用 `soundfile` 读成数组再喂模型，绕开内部的 PyAV 解码路径。

### 静音会「编」出句子

whisper 在静音/噪声上会凭空生成「感谢观看」这类句子。用模型自己给的 `no_speech_prob > 0.6` 和 `avg_logprob < -1.0` 两道阈值挡掉。

### 音效为什么用 winsound

只为一声音效去建音频流属于杀鸡用牛刀。`winsound` 是标准库、零安装、异步播放不卡界面。

### 钱用整数存

后续入库时金额一律用「分」（`amount_cents`）存整数，避免浮点误差。

## 已知限制

- **数据不持久**：目前记录只在内存里，关掉窗口就没了（`storage.py` 还是占位）
- 单机、单人、无并发
- 语音识别是 CPU int8，约 0.5 倍实时

## 开发路线

见 [ROADMAP.txt](ROADMAP.txt)。

## 附：仓库里的历史文件

- `main.py` — 最早的命令行版，保留作参考，未接入界面
- `storage.py` — 存储层占位（8 行），下一步重写为真正的 SQLite 层
- `SmallestVertify.py` — 作者早期写的批量字幕工具，语音支线的参考实现，**不参与本项目运行**

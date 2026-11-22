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
| 点击录音 + 语音识别 | ✅ 可跑（本地 whisper / DeepSeek 云端，可切换） |
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

# 4.（可选）想用云端识别才需要密钥
copy .env.example .env
#   然后编辑 .env，填上 DEEPSEEK_API_KEY=sk-xxxx

# 5. 启动
run.bat
```

等价于 `.\.venv\Scripts\python.exe gui.py`。

第一次点录音会加载模型，约 10 秒；之后常驻内存。

## 配置密钥

密钥**不进代码、不进仓库**，只放在本地 `.env` 里：

| 文件 | 是否提交 | 内容 |
| --- | --- | --- |
| `.env.example` | ✅ 提交 | 模板，值为空 |
| `.env` | ❌ 已在 `.gitignore` | 你真实的 key |

`.env` 在启动时被 `config.py` 读进环境变量，代码里只用 `os.environ` 取值。
就算环境变量已经存在，也会优先用环境变量（方便临时覆盖）。

两种填法任选：

1. **界面**：菜单「文件 → 设置…」，填 key 选模式，点保存
2. **手改**：复制 `.env.example` 为 `.env`，编辑里面的两行

`ASR_BACKEND` 三档：

| 值 | 行为 |
| --- | --- |
| `local` | 只用本地 whisper，不需要 key |
| `cloud` | 只用 DeepSeek 云端识别，没 key 会直接报错 |
| `auto`（默认） | 有 key 就用云端，**云端失败会弹窗并退出程序**（暂定，方便立刻发现问题）；没 key 就用本地 |

## 怎么用

- **打字**：输入框写一句，回车 → 列表多一条
- **语音**：点「点一下说话」→ 响一声后开始录音 → 再点一次结束（或 60 秒自动停）→ 识别完**自动记一条**，不用按回车
  - 想改成「先填进输入框、确认后回车」，把 `config.py` 里的 `AUTO_SEND` 改成 `False`
- **设置**：菜单「文件 → 设置…」→ 填 API Key、选识别方式 → 保存（写进 `.env`，重启生效）
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
.env.example         密钥模板（真实的 .env 不进仓库）
main.py              最早的命令行版，未接入界面
storage.py           存储层占位，下一步重写
```

## 技术说明

### 模型权重不进仓库

`models/faster-whisper-small/` 有 461 MB，用 `download_model.py` 拉取。文件落到本地文件夹（不走 huggingface 默认缓存），可以整个拷走换机器用。

### 为什么不直接把 wav 路径喂给 faster-whisper

PyAV 19 删掉了 `metadata_errors` 参数，而 faster-whisper 1.2.1 还在传，用文件路径直接转写会崩。这里用 `soundfile` 读成数组再喂模型，绕开内部的 PyAV 解码路径。

### 静音会「编」出句子

whisper 在静音/噪声上会凭空生成「感谢观看」这类句子。挡法有三层：

1. 录音峰值低于 `SILENCE_PEAK`（100）直接判定没录到声音，根本不进模型
2. `no_speech_prob > 0.6` 的片段丢掉
3. `avg_logprob < -1.5` 的片段丢掉

第 3 条的阈值放宽过一次：实测 1~2 秒的短句经常只有 `-1.4`，卡在 `-1.0` 会把真话误杀。

### 两套识别引擎

- **本地**：faster-whisper small，CPU int8，约 0.5 倍实时，不需要 key
- **云端**：DeepSeek `POST /v1/tools/asr`，multipart 上传 wav，按音频时长计费（0.78 元/小时）

两者实现同一个 `Transcriber` 接口（`transcribe(wav) -> str`），`config.ASR_BACKEND` 决定用哪个。
当前是「一个失败就报错退出」，不静默回退——这样云端有问题能立刻看见；以后想改成自动降级，
加一个组合类把两个引擎串起来即可。加第三家（腾讯、阿里等）只需要再写一个子类。

### 音效为什么用 winsound

只为一声音效去建音频流属于杀鸡用牛刀。`winsound` 是标准库、零安装、异步播放不卡界面。

### 钱用整数存

后续入库时金额一律用「分」（`amount_cents`）存整数，避免浮点误差。

## 已知限制

- **数据不持久**：目前记录只在内存里，关掉窗口就没了（`storage.py` 还是占位）
- 单机、单人、无并发
- 语音识别是 CPU int8，约 0.5 倍实时
- **云端识别还没用真实 key 跑过**：代码结构和"没 key 时的报错"已验，真实请求待测
- 界面只在 Windows 上验证过（音效用了 `winsound`）

## 开发路线

见 [ROADMAP.txt](ROADMAP.txt)。

## 附：仓库里的历史文件

- `main.py` — 最早的命令行版，保留作参考，未接入界面
- `storage.py` — 存储层占位（8 行），下一步重写为真正的 SQLite 层
- `SmallestVertify.py` — 作者早期写的批量字幕工具，语音支线的参考实现，**不参与本项目运行**

# 目录结构

一句话版：`app.py` 接 HTTP，几个解析模块把不同输入变成账目草稿，`storage.py` 落 SQLite，
`query.py` 把问题变成 SQL，`templates/index.html` 是整个前端。

```
app.py                    FastAPI 路由：页面、记账、统计、查账、转写、OCR、导入、设置、历史
importer.py               CSV / Excel 账单导入：认编码、找表头、把表读成一条条账目草稿
receipt.py                小票 / 截图 OCR：一张图读成金额 / 商户 / 时间（只读，不入库）
parser.py                 规则解析：一句话 -> 金额 / 日期 / 时刻 / 类别 / 方向 / AA 人数
llm.py                    云端 LLM 兜底解析（规则拿不准时才调用）
query.py                  自然语言查账：问题 -> DSL -> SQL -> 一句话
storage.py                SQLite：建表、增改删、统计、操作历史、撤销
asr.py                    语音识别：本地 whisper + DeepSeek 云端
local_model.py            本地模型管理：ModelScope 下载 faster-whisper、进度、断点续传
settings.py               运行时设置（data/settings.json）
audio_effectsRecovery.py  音效查找（有就播，没有就静音）
config.py                 常量 + .env 读取
templates/index.html      网页界面（单文件 HTML+CSS+JS）
audio_effects/            四个音效：start / stop / success / fail
live2d/                   看板娘实现（waifu.js）和本地预览页
start.bat                 双击启动（自动开浏览器）
Dockerfile                镜像构建（只装网页版依赖）
docker-compose.yml        端口、密钥、数据卷
requirements.txt          本地开发依赖（锁版本）
requirements-web.txt      Docker 用的精简依赖
requirements.lock.txt     完整依赖快照（pip freeze）
samples/demo.csv          演示用模拟账单（225 条，2026-09 ~ 2026-11）
samples/receipt_demo.png  演示用小票图（合成，不含真实信息）
.env.example              密钥模板
ROADMAP.txt               冲刺计划与交付清单
docs/design.md            方案设计、问题与解决、未完成的部分及原因
docs/api.md               接口清单（本文档的邻居）
docs/structure.md         本文件
tests/                    入库的单元测试（标准库 unittest，不联网、不碰真数据）
assets/                   README 截图
Legacy_OutOfDate/         已冻结的桌面原型（见 README 文末）
```

## 为什么这么分

- **输入层分开**：`parser.py` 管一句话，`receipt.py` 管一张图，`importer.py` 管一张表。
  三者的输入形态完全不同，混在一起会互相拖累；它们只共用「类别关键词」和「校验层」。
- **模型层不碰数据库**：`llm.py` / `receipt.py` 只吐字段，落库仍然走 `storage.py`。
- **查询与存储分开**：`query.py` 负责把问题翻译成 SQL 片段（白名单 + 参数绑定），
  `storage.py` 只负责执行。
- **前端单文件是有意的取舍**：Live2D 的生成脚本和页面结构耦合，拆分的风险大于收益，
  理由写在 README 的「已知限制」和 [design.md](design.md) 的 3.7 节。

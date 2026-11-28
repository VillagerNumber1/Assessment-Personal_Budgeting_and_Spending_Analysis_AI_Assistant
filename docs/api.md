# 接口一览

共 27 个接口（下表 25 行：`/api/settings` 和 `/api/queries` 的读、写各合成了一行），
都挂在 FastAPI 上，`/docs` 里能直接点开试。

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/` | 返回单页界面 |
| GET | `/api/summary?month=2026-11` | 某个月的支出 / 收入 / 结余 / 预算 + 最近 10 条 |
| GET | `/api/stats?month=2026-11` | 分类聚合 + 近 6 个月趋势 |
| POST | `/api/budget` | 给某个月单独设预算；传 0 或 null 就是清除，回到跟随默认 |
| GET | `/api/transactions?limit=10&offset=0` | 流水分页，返回 `{items, total, limit, offset}` |
| GET | `/api/transactions/locate?start=…&end=…` | 这段账在流水第几页（查账后自动跳页用） |
| POST | `/api/transactions` | 记一笔，支持一句话多个分句 |
| PATCH | `/api/transactions/{id}` | 改「做了什么 + 金额」 |
| DELETE | `/api/transactions/{id}` | 软删除 |
| POST | `/api/transactions/{id}/restore` | 撤回删除 |
| POST | `/api/query` | 自然语言查账，返回结果和 DSL |
| GET | `/api/operations?limit=60` | 操作历史 |
| POST | `/api/operations/{id}/undo` | 撤销一次操作 |
| DELETE | `/api/operations/{id}` | 清掉一条历史记录 |
| GET / POST | `/api/queries` | 查账历史：问过些什么 |
| DELETE | `/api/queries/{id}` | 删掉一条查账历史 |
| POST | `/api/transcribe` | 音频转文字（multipart） |
| POST | `/api/receipt` | 小票 / 截图 OCR：读成一条草稿，不入库（multipart，8 MB 以内） |
| POST | `/api/import/preview` | 上传 CSV / xlsx，只解析不入库（multipart，8 MB 以内） |
| POST | `/api/import/commit` | 确认导入：重新校验每一行，去重后写库 |
| GET / POST | `/api/settings` | 读写运行时设置 |
| GET | `/api/background` | 当前卡片背景图；没设过返回 404，前端回退默认渐变 |
| POST | `/api/background` | 上传 / 替换卡片背景（multipart，png / jpg / webp / gif，8 MB 以内） |
| DELETE | `/api/background` | 恢复默认渐变，顺手把图删掉 |
| GET | `/api/sounds` | 哪些音效可用 |

## 几个约定

- **写接口都过校验层**：金额、日期、月份、类别在 `app.py` 里统一检查，手打、语音、OCR、导入的数据一视同仁
- **删除是软删除**：`DELETE` 只打时间戳，`POST .../restore` 或操作历史的撤销都能恢复
- **错误返回人话**：4xx 的 `detail` 直接给页面展示，例如「日期格式不对：写 2026-11-23 或 2026-11-23 14:30」
- **模型只出结构**：`/api/query` 返回 DSL，SQL 由 `query.py` 拼，用户输入不进 SQL 字符串

具体请求体字段可以直接看 `/docs`；设计上的取舍见 [design.md](design.md)。

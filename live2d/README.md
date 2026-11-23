# Live2D 看板娘 · `live2d/`

> 已接进记账页：`templates/index.html` 里 `<!-- live2d:begin/end -->` 之间那段，
> 开关在 **设置 → 通用设置 → Live2D 看板娘**。**它挂了不影响记账 / 语音 / 查账 / 统计。**

---

## 0. 先说结论：文章里的写法现在不完整（2026-11 实测）

文章给的一行式是：

```html
<script src="https://fastly.jsdelivr.net/gh/stevenjoezhang/live2d-widget@latest/autoload.js"></script>
```

脚本本身还能加载，但它的默认模型源 `cdnPath` 指向
`https://fastly.jsdelivr.net/gh/fghrsh/live2d_api/`，实测已经废了：

| 请求 | 结果 |
| --- | --- |
| `.../gh/fghrsh/live2d_api/model_list.json` | 200（jsDelivr 有缓存，偶尔能过） |
| `.../gh/fghrsh/live2d_api/model/Potion-Maker/Pio/Pio.model.json` | **403 `Package size exceeded the configured limit of 50 MB`** |
| `https://live2d.fghrsh.net/api/get/?id=1`（老后端） | 200，JSON 还在 |
| `https://live2d.fghrsh.net/model/Potion-Maker/Pio/model.moc`（老后端的静态文件） | **404** |

原因：那个模型仓库总量远超 jsDelivr 单仓库 50 MB 上限，被整包拒绝；原作者站点的静态模型也已经撤了。
**照文章原样抄，结果就是"人出来了，没模型"或者干脆空白。**

本项目改用一套实测可用的组合：

| 用途 | 包 | 说明 |
| --- | --- | --- |
| 渲染代码 | `live2d-widget@3.1.4`（npm） | `lib/L2Dwidget.min.js` 26 KB，运行时会自动拉同目录的 `L2Dwidget.0.min.js`（151 KB，含 Cubism 2 内核） |
| 模型 | `live2d-widget-model-haru@1.0.5`（npm） | `01/assets/haru01.model.json` 实测 200，moc + 3 张纹理 + 动作都在同包 |
| 分发 | jsDelivr / unpkg 的 **npm** 路径 | npm 包不受 GitHub 仓库 50 MB 限制，也不会 302 到 raw.githubusercontent |

> 沙箱里 `git clone` 和二进制下载都被 TLS 拦了（`SEC_E_NO_CREDENTIALS`），所以这里不去"把仓库拉下来"，
> 而是**运行时从 CDN 取**。好处正好对上考核红线：模型不进仓库、不进 Docker 镜像。

## 1. 目录里有什么

| 文件 | 作用 |
| --- | --- |
| `waifu.js` | **唯一实现**：开关、多镜像回退、拖动、位置记忆、对话气泡、诊断接口 |
| `demo.html` | 本地预览页：照抄记账页外壳 + 右下角看板娘 + 调试面板 |
| `preview.bat` | 一键起本地服务器（8137）并打开 `demo.html` |
| `snippet.html` | 独立片段版（给人复制到别的页面用） |
| `build-snippet.js` | 把 `waifu.js` 同步到 `snippet.html` **和** `templates/index.html` 的标记区块 |

`templates/index.html` 里那份是**生成出来的副本**，别手改；改了 `waifu.js` 就跑一次同步，见 §3。

## 2. 先看效果（不动主页面）

双击 `preview.bat`，浏览器会打开 `http://127.0.0.1:8137/demo.html`。

- **可以用鼠标拖着小人走**，松手就记住；位置存在这个浏览器的 `localStorage`（键 `live2d.offset.v1`），刷新还在
- 左下调试面板：状态、台词按钮、X/Y 滑块、「复制位置配置」、「重置位置」、四个镜像体检
- 预览页不受正式页的宽度门槛限制，窗口小也会强制显示（会在顶部提示）

`file://` 直接打开 `demo.html` 不行：模型 JSON 会被浏览器 CORS 拦。必须走 `preview.bat` 起的 http。

## 3. 接进主页面（已经接好了）

两处，都在 `templates/index.html`：

1. `</body>` 前的标记区块 `<!-- live2d:begin ... --> ... <!-- live2d:end -->`：由脚本生成，内容就是 `waifu.js`
2. `paintLive2d(on)`（设置面板的开关）里多一行：

```js
if (window.Live2DWaifu) window.Live2DWaifu.setEnabled(on);
```

链路是：`GET /api/settings` → `d.live2d` → `paintLive2d(on)` → `setEnabled(on)`。
**`waifu.js` 默认不自启**，所以开关关掉时一个 CDN 脚本都不会加载；重新打开会立刻加载。

改了 `waifu.js` 之后同步（需要本机有 Node）：

```powershell
node live2d\build-snippet.js
```

> 别用 PowerShell 的 `Get-Content / Set-Content` 拼这些文件：Windows PowerShell 5.1 默认按 GBK 读，
> 中文字符串会被写坏，已经踩过一次。

## 4. 让它说话

气泡和模型是解耦的，业务代码只需要一行 `WaifuSay("...")`。

最省事的挂法（一处管住所有报错）：在 `hint()` 里加

```js
if (isError) WaifuSay("这个我算不出来");
```

更有针对性的三处（按函数名找，别按行号）：

| 位置 | 加什么 |
| --- | --- |
| `create()` 成功分支，`hint("理解为：...")` 之后 | `WaifuSay("记好啦，¥" + p.amount_yuan)` |
| `create()` 失败分支，`hint("存不进：...")` 之后 | `WaifuSay("这个我存不进去…")` |
| 查账拿到 `d.answer` 之后 | `WaifuSay("我看看…")` |

气泡默认 4.2 秒消失，要改时长用 `Live2DWaifu.say(text, 6000)`。单击模型也会随机说一句
（`waifu.js` 里的 `tapLines`）。

## 5. 位置 / 拖动 / 尺寸 / 层级

| 项 | 值 | 说明 |
| --- | --- | --- |
| 默认位置 | `offset: { x: -120, y: 0 }` | 让开右下角的设置按钮（`.fab`） |
| 拖动 | 鼠标按住小人拖 | 拖动阈值 4px，拖动过程不吃点击 |
| 记忆 | `localStorage["live2d.offset.v1"]` | `{"x":-120,"y":0}`；重置用 `Live2DWaifu.resetOffset()`；**关掉开关时会一起清掉**（避免再打开时"先回默认、一拖又跳回旧位置"） |
| 尺寸 | 150 × 300 | `display.width/height` |
| 宽度门槛 | < 1240px 不加载 | 680 正文栏居中，1240 时正文右边缘 960、小人左边缘 970，正好不压按钮 |
| 层级 | 小人 3，气泡 4，设置按钮 50，弹层 9000 | `.mask` 原来没写 z-index，由 `waifu.js` 注入补上 |
| 气泡 | 跟着小人走 | 每次说话 / 拖动 / 窗口缩放都按 `#live2d-widget` 的实时位置重算，贴头顶居中，左右各留 8px 不出屏 |
| 气泡高度 | `CFG.bubbleDropY`（默认 60） | 从画布顶部往下挪多少像素再贴头顶；觉得高/低改这个数，控制台改完点一下小人立即生效 |

**拖动和单击的区分**：位移 < 4px 算单击（照常触发 L2Dwidget 的点击动作 + 随机台词）；
超过 4px 算拖动，松手时把位置写进 localStorage，并吞掉这一次 click，不会再触发动作。

## 6. 换模型

只改 `waifu.js` 顶部的 `CFG.modelPath`，然后跑一次 `node live2d\build-snippet.js`。
注意**包内路径不统一**，换之前先把那个 JSON 地址丢浏览器里看是不是 404：

| 模型 | 路径 |
| --- | --- |
| Haru（默认，校服少女） | `live2d-widget-model-haru@1.0.5/01/assets/haru01.model.json` |
| Haru 02（另一套衣服） | `live2d-widget-model-haru@1.0.5/02/assets/haru02.model.json` |
| 黑猫（Hijiki） | `live2d-widget-model-hijiki@1.0.5/assets/hijiki.model.json` |
| 白猫（Tororo） | `live2d-widget-model-tororo@1.0.5/assets/tororo.model.json` |
| 小春 | `live2d-widget-model-koharu@1.0.5/assets/koharu.model.json` |

前缀还是 `CFG.mirrors` 里那个（`https://fastly.jsdelivr.net/npm/`）。
这些都是 **Cubism 2**（`.model.json` + `.moc`），不能换成 Cubism 3/4 的 `.model3.json`——运行时是老的 Cubism 2 内核。
评估场合如果想稳一点，猫比少女更不挑评审口味。

## 7. 降级与验收清单

> 已经用 Node 模拟 DOM 跑过的部分：开关（关掉零请求）、宽度门槛、镜像回退、`init` 参数、拖动、
> 位置持久化、拖完吞点击、单击仍说话、重置、全失败降级——全部通过。
> **真实渲染没在沙箱里验过**（浏览器进程起不来），下面这些要在真浏览器里过一遍。

- [ ] 打开首页（窗口拉宽到 ≥ 1240），右下角出现小人，会眨眼、跟着鼠标看
- [ ] 按住小人拖动：实时跟随，松手后刷新页面位置还在；说话时气泡跟着人跑
- [ ] 把小人拖到最左边：气泡贴着屏幕左边，不跑出去
- [ ] 拖完那一下不会触发点击动作、不冒台词；正常单击会冒一句
- [ ] 拖动时页面文字不会被选中
- [ ] 设置里关掉开关 → 刷新 → Network 过滤 `jsdelivr` 应该**一条请求都没有**
- [ ] 关掉再打开：回到默认位置（`-120,0`），拖动从默认位置开始，**不会再跳回上次的位置**
- [ ] 关掉再打开：Network 里不会重新拉一遍 `L2Dwidget.min.js`（不会多出一个画布）
- [ ] 打开设置面板 / 编辑弹窗 / 金额确认弹窗：弹层盖在小人上面
- [ ] 深色主题下气泡是深色的，不刺眼
- [ ] 把窗口缩到 < 1240：小人消失（Console 有一条 info），页面功能正常
- [ ] 把 `CFG.mirrors` 全改错：页面照常，Console 只有 `[live2d] 镜像不可用` 这类 warn
- [ ] `git status` 里没有任何模型文件

## 8. 已知坑

| 现象 | 先查什么 |
| --- | --- |
| 完全看不见小人 | 先看窗口宽度是否 < 1240（Console 有 info）；再看 Network 里 `L2Dwidget.min.js` 是否失败 |
| 有 canvas 但看不见人 | Network 里 `haru01.model.json` / `texture_00.png` 是不是 404 或被拦 |
| 拖不动 | 开关是不是关着；窗口是不是 < 1240；画布只有 150×300，点在透明区域外没用 |
| 位置记不住 | 无痕/隐私模式禁用 localStorage；换端口（8137 vs 8000）是另一个 origin，各记各的 |
| 拖到屏幕外找不回来 | `clampOffset` 保底：至少留 90px 宽 / 140px 高在屏内；实在乱了用 `Live2DWaifu.resetOffset()` |
| Console 报 `Loading chunk 0 failed` | 只镜像了主 js、没镜像同目录的 `L2Dwidget.0.min.js`；两个必须同一来源，**别自己下一份放本地** |
| 挡住输入框 | 调 `offset.y`，或把 `display.width` 调小 |
| 弹窗被盖住 | 别把 `.mask` 的 z-index 改回 auto |
| 点模型有音效太吵 | 模型包自带的 `snd/*.mp3`；要去掉只能自托管模型，不建议为这个折腾 |

## 9. 许可证 / 版权（答辩要能答）

- 渲染库 `live2d-widget` 3.1.4：**GPL-2.0**，以独立 `<script>` 引入，未修改其源码
- 文章用的 `stevenjoezhang/live2d-widget` 1.x 是 **GPL-3.0**，本项目**没有**采用（原因见 §0）
- 模型 Haru 是 **Live2D 官方示例模型**，版权归 Live2D / 原作者，npm 包只是第三方分发；仅用于学习演示、**不得商用**
- 说话口径：「用了开源 live2d-widget 和 Live2D 官方示例模型，只做展示」，不要说成自己画的

## 10. 和 `HANDOFF_LIVE2D.md` 的关系

那份交接文档的**红线全部保留**（模型不进仓库、不进 Docker 镜像、不改后端、不宣称自绘），
只是方案从"一行 CDN"换成了"npm 镜像 + 本地台词层"，因为原方案默认模型源已失效（§0）。
现在文档里的"页面里已经留好的开关"已经真正接上：`paintLive2d()` → `setEnabled()`。

# Live2D 看板娘 · `live2d/`

> 独立目录，可选装饰。**它挂了，记账 / 语音 / 查账 / 统计都照常。**
> 目前还没有接进 `templates/index.html`，先在本地预览页里验收，见 §3。

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

本目录改用一套实测可用的组合：

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
| `waifu.js` | **唯一实现**：多镜像回退加载、对话气泡、位置微调、诊断接口 |
| `demo.html` | 本地预览页：照抄记账页外壳 + 右下角看板娘 + 调试面板 |
| `preview.bat` | 一键起本地服务器（8137）并打开 `demo.html` |
| `snippet.html` | 接进主页面的成品片段（由 `waifu.js` 生成，已含 `<script>` 标签） |
| `build-snippet.js` | 把 `waifu.js` 重新生成成 `snippet.html`（Node 脚本） |

## 2. 先看效果

双击 `preview.bat`，浏览器会打开 `http://127.0.0.1:8137/demo.html`。

**不要双击 `demo.html`**：`file://` 下模型 JSON 会被浏览器 CORS 拦，页面会空。
脚本会在 8137 端口起一个 `python -m http.server`，关掉那个最小化的窗口就停了。

调试面板（左下角）能干这些事：

- 看状态：`未启动 / 正在尝试 xxx / 已加载 / 失败`，失败原因也写在这
- 点「记好啦 / 我看看 / 算不出来」试台词气泡
- 拖动 X / Y 调位置，**「复制位置配置」** 会把 `offset: { x, y }` 拷到剪贴板，直接贴回 `waifu.js`
- 「测一下四个镜像」：逐个 `HEAD` 模型文件，给出 OK / HTTP 码 / 超时和耗时

## 3. 接进主页面（还没做，等确认）

把 `snippet.html` 的**全部内容**粘到 `templates/index.html` 的 `</body>` 前（当前在第 570 行）。

- 不需要改 `app.py`，不新增接口，不新增静态挂载
- 不需要动 Dockerfile：模型和代码都从 CDN 来，镜像体积不变
- 加载失败只会在 Console 里留一条 `[live2d] ...` warn，不会抛红错，也不阻塞页面

改了 `waifu.js` 之后别手工同步，跑一次（需要本机有 Node）：

```powershell
node live2d\build-snippet.js
```

> 别用 PowerShell 的 `Get-Content / Set-Content` 拼这个文件：Windows PowerShell 5.1 默认按 GBK 读，
> 中文字符串会被写坏，已经踩过一次。

## 4. 让它说话

气泡和模型是解耦的，业务代码只需要一行 `WaifuSay("...")`。

最省事的挂法（一处管住所有报错）：在 `templates/index.html` 的 `hint()` 里加

```js
if (isError) WaifuSay("这个我算不出来");
```

更有针对性的三处：

| 位置 | 加什么 |
| --- | --- |
| `create()` 成功分支（`hint("理解为：...")` 之后，约 318 行） | `WaifuSay("记好啦，¥" + p.amount_yuan)` |
| `create()` 失败分支（约 314 行） | `WaifuSay("这个我存不进去…")` |
| 查账拿到答案之后 | `WaifuSay("我看看…")` |

气泡默认 4.2 秒消失，要改时长用 `Live2DWaifu.say(text, 6000)`。模型自己也会在被点的时候随机说一句
（`waifu.js` 里的 `tapLines`）。

## 5. 位置 / 尺寸 / 层级

- 默认右下角，150 × 300，`display.position: "right"`
- 细调只用 `offset: { x, y }`（像素，正数往右/往下），不碰 CSS
- 视口 < 768px 直接不加载（`start()` 里判断 + CSS 兜底），免得挡输入框
- 层级：看板娘 `z-index: 3`，弹层 `.mask` 被抬到 `9000`。
  原页面 `.mask` 没写 z-index，不抬的话看板娘会盖住编辑弹窗——这两条都在 `waifu.js` 注入的 `<style>` 里

## 6. 换模型

只改 `waifu.js` 顶部的 `CFG.modelPath`。注意**包内路径不统一**，换之前先把那个 JSON 地址丢浏览器里看是不是 404：

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

> 已经用 Node 模拟 DOM 跑过的部分：镜像回退顺序、`init` 参数、气泡、位置微调、全失败降级、窄屏跳过，
> 全部通过。**真实渲染（模型会不会动、位置好不好看）没在浏览器里验过**——沙箱不让起浏览器进程，
> 第一次打开预览页要人眼看一眼。

- [ ] 打开预览页，右下角出现小人，会眨眼、跟着鼠标看
- [ ] 点模型会冒一句台词；三个台词按钮都正常
- [ ] 断网刷新：页面照常，Console 里只有 `[live2d] 镜像不可用` 这类 warn
- [ ] 把 `CFG.mirrors` 全改错：页面照常，只是没有小人
- [ ] 拉到窄窗口（< 768px）：不显示
- [ ] 编辑弹窗 / 金额确认弹窗打开时，盖在看板娘上面
- [ ] 底部输入框、🎤、列表的点击都不被挡
- [ ] `git status` 里没有任何模型文件、没有新增静态资源

## 8. 已知坑

| 现象 | 先查什么 |
| --- | --- |
| 完全空白，没有任何 canvas | Network 里 `L2Dwidget.min.js` 是否失败；换 `CFG.mirrors` 顺序（校园网/公司网常拦 jsDelivr） |
| 有 canvas 但看不见人 | Network 里 `haru01.model.json` / `texture_00.png` 是不是 404 或被拦 |
| Console 报 `Loading chunk 0 failed` | 只镜像了主 js、没镜像同目录的 `L2Dwidget.0.min.js`；两个必须同一来源，**别自己下一份放本地** |
| 挡住输入框 | 调 `offset.y`，或把 `display.width` 调小 |
| 弹窗被盖住 | 别把 `.mask` 的 z-index 改回 auto |
| 点模型有音效太吵 | 模型包自带的 `snd/*.mp3`；要去掉只能自托管模型，不建议为这个折腾 |
| 想手机上也显示 | 去掉 `start()` 里的 `innerWidth` 判断，以及 CSS 里那条 `@media` |

## 9. 许可证 / 版权（答辩要能答）

- 渲染库 `live2d-widget` 3.1.4：**GPL-2.0**，以独立 `<script>` 引入，未修改其源码
- 文章用的 `stevenjoezhang/live2d-widget` 1.x 是 **GPL-3.0**，本项目**没有**采用（原因见 §0）
- 模型 Haru 是 **Live2D 官方示例模型**，版权归 Live2D / 原作者，npm 包只是第三方分发；仅用于学习演示、**不得商用**
- 说话口径：「用了开源 live2d-widget 和 Live2D 官方示例模型，只做展示」，不要说成自己画的

## 10. 和 `HANDOFF_LIVE2D.md` 的关系

那份交接文档的**红线全部保留**（模型不进仓库、不进 Docker 镜像、不改后端、不宣称自绘），
只是方案从"一行 CDN"换成了"npm 镜像 + 本地台词层"，因为原方案默认模型源已失效（§0）。
`HANDOFF_LIVE2D.md` 里"必须等三件事做完"的顺序建议也仍然有效：预算提醒 → Docker 验证 → README/PPT，
看板娘排在这三件之后。

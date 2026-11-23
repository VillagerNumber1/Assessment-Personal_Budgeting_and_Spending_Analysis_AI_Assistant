#!/usr/bin/env node
/*
 * 由 waifu.js 生成 snippet.html。
 * 以后只改 waifu.js，再跑一次这个脚本，别手工同步两份代码：
 *
 *     node live2d/build-snippet.js
 *
 * 用 Node 而不是 PowerShell，是因为 Windows PowerShell 5.1 默认按 GBK 读文件，
 * 会把中文字符串写坏（这个坑已经踩过一次了）。
 */
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const here = __dirname;
const source = path.join(here, "waifu.js");
const target = path.join(here, "snippet.html");

const js = fs.readFileSync(source, "utf8");

// 先做语法体检，写坏的文件不要流出去
new vm.Script(js, { filename: "waifu.js" });

const header = `<!-- ============================================================
  Live2D 看板娘（可选装饰）
  用法：把下面整段粘贴到 templates/index.html 的 </body> 前面，一行都不用改。
  特点：
    - 纯前端，不碰 app.py、不新增接口、不进 Docker 镜像
    - 只从 CDN 拉代码和模型，仓库体积不变
    - 任何一步失败都只 warn 一条，不影响记账、语音、查账、统计
    - 想让它说话：在业务代码里调 WaifuSay("记好啦")
  下面是 live2d/waifu.js 的完整内容，由 build-snippet.js 生成，不要在这里改。
   ============================================================ -->
<script>
`;

const footer = `</script>
`;

const out = header + js + footer;
fs.writeFileSync(target, out, "utf8");
console.log("已生成 " + target + "（" + Buffer.byteLength(out, "utf8") + " 字节，源文件 " + source + "）");

#!/usr/bin/env node
/*
 * 把 waifu.js 同步到两个地方：
 *   1. live2d/snippet.html            —— 给人看的、可以直接复制的片段
 *   2. templates/index.html           —— 只替换 <!-- live2d:begin/end --> 之间的内容
 *
 * 以后只改 waifu.js，再跑一次：
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
const indexHtml = path.join(here, "..", "templates", "index.html");

const BEGIN = "<!-- live2d:begin 由 live2d/build-snippet.js 生成，别手改 -->";
const END = "<!-- live2d:end -->";

const js = fs.readFileSync(source, "utf8");

// 先做语法体检，写坏的文件不要流出去
new vm.Script(js, { filename: "waifu.js" });

const header = `<!-- ============================================================
  Live2D 看板娘（可选装饰）
  用法：把下面整段粘贴到 templates/index.html 的 </body> 前面，一行都不用改。
  特点：
    - 纯前端，不碰 app.py、不新增接口、不进 Docker 镜像
    - 只从 CDN 拉代码和模型，仓库体积不变
    - 默认不自启：由页面的 paintLive2d() 调 Live2DWaifu.setEnabled(true) 才加载
    - 任何一步失败都只 warn 一条，不影响记账、语音、查账、统计
    - 想让它说话：在业务代码里调 WaifuSay("记好啦")
  下面是 live2d/waifu.js 的完整内容，由 build-snippet.js 生成，不要在这里改。
   ============================================================ -->
`;

const block = header + "<script>\n" + js + "</script>\n";

fs.writeFileSync(target, block, "utf8");
console.log("已生成 " + target + "（" + Buffer.byteLength(block, "utf8") + " 字节）");

// 顺手更新主页面里被标记包住的那一段
if (fs.existsSync(indexHtml)) {
  const html = fs.readFileSync(indexHtml, "utf8");
  const start = html.indexOf(BEGIN);
  const end = html.indexOf(END);

  if (start < 0 || end < 0 || end < start) {
    console.log("跳过 templates/index.html：没找到 live2d:begin / live2d:end 标记");
  } else {
    const head = html.slice(0, start + BEGIN.length);
    const tail = html.slice(end - 1); // 从 END 前一行的换行开始保留
    const next = head + "\n" + block + tail;
    if (next === html) {
      console.log("templates/index.html 已是最新，没有改动");
    } else {
      fs.writeFileSync(indexHtml, next, "utf8");
      console.log("已更新 templates/index.html 的 live2d 区块");
    }
  }
}

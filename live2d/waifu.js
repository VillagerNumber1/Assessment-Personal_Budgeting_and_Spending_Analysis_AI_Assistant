/*!
 * Live2D 看板娘 —— 记账小助手
 * ---------------------------------------------------------------
 * 一个文件干完三件事：
 *   1. 从 CDN 依次尝试多个镜像，加载 L2Dwidget 3.x（Cubism 2 运行时）
 *   2. 挂上 npm 上的模型包（haru01），失败就安静地放弃
 *   3. 提供一个跟模型解耦的对话气泡：window.WaifuSay("记好啦")
 *
 * 依赖（全部走 CDN，不进仓库、不进 Docker 镜像）：
 *   代码  live2d-widget@3.1.4            GPL-2.0
 *   模型  live2d-widget-model-haru@1.0.5 模型版权归原作者，仅学习演示用
 *
 * 对外接口：
 *   Live2DWaifu.start()        手动启动（默认自动启动）
 *   Live2DWaifu.say(text, ms)  说话（WaifuSay 是它的别名）
 *   Live2DWaifu.hide() / show() / toggle()
 *   Live2DWaifu.nudge(dx, dy)  微调位置（像素），不影响其他样式
 *   Live2DWaifu.state()        当前状态快照
 *   Live2DWaifu.onStatus(fn)   订阅状态变化，返回取消订阅函数
 *   Live2DWaifu.diagnose()     逐个探测镜像，返回 Promise<Array>
 */
(function () {
  "use strict";

  if (window.Live2DWaifu) return;

  var CFG = {
    // 按顺序尝试；同一个 base 既取代码也取模型，命中哪个就整套用哪个
    mirrors: [
      "https://fastly.jsdelivr.net/npm/",
      "https://gcore.jsdelivr.net/npm/",
      "https://testingcf.jsdelivr.net/npm/",
      "https://unpkg.com/"
    ],
    scriptPath: "live2d-widget@3.1.4/lib/L2Dwidget.min.js",
    modelPath: "live2d-widget-model-haru@1.0.5/01/assets/haru01.model.json",
    display: { position: "right", width: 150, height: 300, hOffset: 0, vOffset: 0 },
    // 在 display 之上再整体平移（像素，正数向右/向下）。调位置只改这里。
    offset: { x: 0, y: 0 },
    mirrorTimeout: 7000,
    bubbleMs: 4200,
    autoStart: true,
    // 点模型时随机说一句
    tapLines: ["别戳啦，快去记一笔～", "记账小助手在此！", "记不清花了多少？问我呀。"]
  };

  var state = { status: "idle", base: null, message: "未启动" };
  var listeners = [];
  var bubble = null;
  var hideTimer = 0;

  /* ---------------- 状态 ---------------- */

  function emit() {
    listeners.forEach(function (fn) {
      try { fn(state); } catch (err) { console.warn("[live2d] status listener failed", err); }
    });
  }

  function setStatus(status, message, base) {
    state.status = status;
    state.message = message;
    if (base) state.base = base;
    emit();
  }

  function hostOf(url) {
    try { return new URL(url).host; } catch (err) { return url; }
  }

  /* ---------------- 样式（只注入一次） ---------------- */

  function injectStyle() {
    if (document.getElementById("waifu-style")) return;
    var css = document.createElement("style");
    css.id = "waifu-style";
    css.textContent = [
      /* 看板娘压在页面内容之上，但必须低于弹层 */
      "#live2d-widget{position:fixed!important;z-index:3!important;pointer-events:none!important;}",
      "#live2d-widget canvas{pointer-events:auto!important;cursor:pointer;}",
      /* 编辑 / 金额确认弹层要盖住看板娘（原样式没写 z-index，这里补上） */
      ".mask{z-index:9000!important;}",
      /* 对话气泡：不吃点击、不挡输入框 */
      "#waifu-bubble{position:fixed;right:168px;bottom:196px;z-index:4;max-width:230px;",
      "padding:9px 12px;border-radius:12px;background:#fff;color:#1f2933;font-size:13px;line-height:1.55;",
      "box-shadow:0 6px 20px rgba(16,24,40,.14);border:1px solid #e6ebf3;pointer-events:none;",
      "opacity:0;transform:translateY(6px);transition:opacity .18s ease,transform .18s ease;}",
      "#waifu-bubble.on{opacity:1;transform:translateY(0);}",
      "#waifu-bubble::after{content:'';position:absolute;right:26px;bottom:-6px;width:12px;height:12px;",
      "background:#fff;border-right:1px solid #e6ebf3;border-bottom:1px solid #e6ebf3;transform:rotate(45deg);}",
      /* 窄屏直接不显示，避免挡住输入框 */
      "@media (max-width:767px){#live2d-widget,#waifu-bubble{display:none!important;}}"
    ].join("");
    (document.head || document.documentElement).appendChild(css);
  }

  /* ---------------- 气泡 ---------------- */

  function ensureBubble() {
    if (bubble && document.body.contains(bubble)) return bubble;
    bubble = document.createElement("div");
    bubble.id = "waifu-bubble";
    bubble.setAttribute("role", "status");
    bubble.setAttribute("aria-live", "polite");
    document.body.appendChild(bubble);
    return bubble;
  }

  function say(text, ms) {
    if (!text) return;
    if (state.status === "failed") return; // 模型都没了，别留一句孤零零的话
    var el = ensureBubble();
    el.textContent = String(text);
    el.classList.add("on");
    clearTimeout(hideTimer);
    hideTimer = setTimeout(function () { el.classList.remove("on"); }, ms || CFG.bubbleMs);
  }

  /* ---------------- 载入 ---------------- */

  function loadScript(url, timeout) {
    return new Promise(function (resolve, reject) {
      var script = document.createElement("script");
      var settled = false;
      var timer = setTimeout(function () {
        if (settled) return;
        settled = true;
        reject(new Error("timeout"));
      }, timeout);

      script.src = url;
      script.async = true;
      script.onload = function () {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        resolve(url);
      };
      script.onerror = function () {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        reject(new Error("network"));
      };
      (document.head || document.documentElement).appendChild(script);
    });
  }

  function boot(base) {
    if (!window.L2Dwidget) throw new Error("L2Dwidget 未挂载");

    var modelUrl = base + CFG.modelPath;
    var result = null;
    try {
      result = window.L2Dwidget.init({
        model: { jsonPath: modelUrl, scale: 1 },
        display: {
          position: CFG.display.position,
          width: CFG.display.width,
          height: CFG.display.height,
          hOffset: CFG.display.hOffset,
          vOffset: CFG.display.vOffset
        },
        mobile: { show: false },
        react: { opacityDefault: 0.95, opacityOnHover: 0.75 },
        dialog: { enable: false }, // 自带对话框要连一言接口，这里不用
        dev: { border: false }
      });
    } catch (err) {
      throw new Error("init 抛错：" + err.message);
    }

    setStatus("running", "已加载 · " + hostOf(base), base);
    applyOffset(CFG.offset.x, CFG.offset.y);

    // L2Dwidget.init 有的版本不返回 Promise，所以两种都兼容
    if (result && typeof result.then === "function") {
      result.catch(function (err) {
        setStatus("failed", "渲染失败：" + (err && err.message ? err.message : err), base);
      });
    }
  }

  function tryMirror(index) {
    if (index >= CFG.mirrors.length) {
      setStatus("failed", "所有镜像都没连上，看板娘已跳过");
      return;
    }
    var base = CFG.mirrors[index];
    setStatus("loading", "正在尝试 " + hostOf(base));

    loadScript(base + CFG.scriptPath, CFG.mirrorTimeout)
      .then(function () { boot(base); })
      .catch(function (err) {
        console.warn("[live2d] 镜像不可用：" + base + "（" + err.message + "）");
        tryMirror(index + 1);
      });
  }

  function start() {
    // 加载中 / 已加载就不重复点，失败后允许再试一次
    if (state.status === "loading" || state.status === "running") return;
    injectStyle();
    if (window.innerWidth < 768) {
      setStatus("idle", "窄屏，看板娘不加载");
      return;
    }
    if (!window.WebGLRenderingContext) {
      setStatus("failed", "浏览器不支持 WebGL");
      return;
    }
    tryMirror(0);
  }

  /* ---------------- 位置 / 显隐 ---------------- */

  function moveTo(el, dx, dy) {
    el.style.transform = "translate(" + (dx || 0) + "px," + (dy || 0) + "px)";
  }

  // init 之后容器是同步创建的，但保险起见还是等一下
  function applyOffset(x, y) {
    var tries = 0;
    (function attempt() {
      var el = document.getElementById("live2d-widget");
      if (el) { moveTo(el, x, y); return; }
      if (tries++ < 20) setTimeout(attempt, 100);
    })();
  }

  function nudge(dx, dy) {
    var el = document.getElementById("live2d-widget");
    if (!el) return;
    moveTo(el, dx, dy);
  }

  function setVisible(visible) {
    var el = document.getElementById("live2d-widget");
    if (el) el.style.display = visible ? "" : "none";
    if (!visible && bubble) bubble.classList.remove("on");
  }

  /* ---------------- 诊断 ---------------- */

  function probe(base, timeout) {
    var url = base + CFG.modelPath;
    var startedAt = Date.now();
    return new Promise(function (resolve) {
      var done = false;
      var ctrl = typeof AbortController === "function" ? new AbortController() : null;
      var timer = setTimeout(function () {
        if (done) return;
        done = true;
        resolve({ base: base, ok: false, ms: Date.now() - startedAt, reason: "超时" });
      }, timeout || 5000);

      fetch(url, { method: "HEAD", mode: "cors", signal: ctrl ? ctrl.signal : undefined })
        .then(function (res) {
          if (done) return;
          done = true;
          clearTimeout(timer);
          resolve({ base: base, ok: res.ok, ms: Date.now() - startedAt, reason: res.ok ? "OK" : "HTTP " + res.status });
        })
        .catch(function (err) {
          if (done) return;
          done = true;
          clearTimeout(timer);
          resolve({ base: base, ok: false, ms: Date.now() - startedAt, reason: (err && err.message) || "失败" });
        });
    });
  }

  function diagnose() {
    return Promise.all(CFG.mirrors.map(function (base) { return probe(base); }));
  }

  /* ---------------- 对外 ---------------- */

  window.Live2DWaifu = {
    config: CFG,
    start: start,
    say: say,
    hide: function () { setVisible(false); },
    show: function () { setVisible(true); },
    toggle: function () {
      var el = document.getElementById("live2d-widget");
      setVisible(!el || el.style.display === "none");
    },
    nudge: nudge,
    state: function () { return { status: state.status, base: state.base, message: state.message }; },
    onStatus: function (fn) {
      listeners.push(fn);
      try { fn(state); } catch (err) { console.warn(err); }
      return function () {
        var i = listeners.indexOf(fn);
        if (i >= 0) listeners.splice(i, 1);
      };
    },
    diagnose: diagnose
  };

  // 业务代码里更短的名字
  window.WaifuSay = say;

  // 点一下模型，说句话
  document.addEventListener("click", function (event) {
    if (!event.target || event.target.tagName !== "CANVAS" || event.target.id !== "live2dcanvas") return;
    var lines = CFG.tapLines;
    say(lines[Math.floor(Math.random() * lines.length)]);
  });

  if (CFG.autoStart) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", start);
    } else {
      start();
    }
  }
})();

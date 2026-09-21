// ============================================================================
// window-manager.js 前端回归测试（零依赖，Node 直接跑）
//
// 无前端测试框架（零构建链决策）→ 这里自带一个 mini-DOM shim，只覆盖 WM
// 用到的 DOM API 面：createElement / classList / style / dataset /
// innerHTML（轻量解析）/ querySelector（.class 与 tag 列表）/ appendChild /
// insertBefore / remove / contains / addEventListener / closest。
//
// 跑法：node tests/js/wm-test.js   （pytest 包装：tests/test_window_manager_js.py）
// ============================================================================
"use strict";
const fs = require("fs");
const path = require("path");

// ---------------------------------------------------------------------------
// mini-DOM shim
// ---------------------------------------------------------------------------
const VOID = new Set(["img", "input", "br", "hr", "path", "rect", "circle",
                      "line", "polyline", "polygon", "source", "track"]);

function makeEl(tag) {
  const el = {
    tagName: String(tag).toUpperCase(),
    children: [],
    parentNode: null,
    _cls: new Set(),
    style: {},
    dataset: {},
    attributes: {},
    listeners: {},
    _innerHTML: "",
    textContent: "",
    type: "", title: "", src: "", alt: "",
    controls: false, preload: "",
    currentTime: 0,
    paused: true,
    setAttribute(k, v) { this.attributes[k] = String(v); },
    appendChild(child) {
      if (child.parentNode) {
        const i = child.parentNode.children.indexOf(child);
        if (i >= 0) child.parentNode.children.splice(i, 1);
      }
      child.parentNode = this;
      this.children.push(child);
      return child;
    },
    insertBefore(child, ref) {
      if (child.parentNode) {
        const i = child.parentNode.children.indexOf(child);
        if (i >= 0) child.parentNode.children.splice(i, 1);
      }
      const at = ref ? this.children.indexOf(ref) : this.children.length;
      child.parentNode = this;
      this.children.splice(at < 0 ? this.children.length : at, 0, child);
      return child;
    },
    remove() {
      if (!this.parentNode) return;
      const i = this.parentNode.children.indexOf(this);
      if (i >= 0) this.parentNode.children.splice(i, 1);
      this.parentNode = null;
    },
    contains(node) {
      for (let c = node; c; c = c.parentNode) if (c === this) return true;
      return false;
    },
    addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); },
    removeEventListener(t, fn) {
      const ls = this.listeners[t];
      if (!ls) return;
      const i = ls.indexOf(fn);
      if (i >= 0) ls.splice(i, 1);
    },
    fire(t, ev) { (this.listeners[t] || []).slice().forEach((fn) => fn(ev || {})); },
    dispatchEvent(ev) { this.fire(ev && ev.type, ev); return true; },
    click() { this.fire("click", {}); },
    closest(sel) {
      const cls = sel.replace(".", "");
      for (let n = this; n; n = n.parentNode) {
        if (n._cls && n._cls.has(cls)) return n;
      }
      return null;
    },
    querySelector(sel) {
      for (const part of String(sel).split(",")) {
        const m = part.trim().match(/^([a-zA-Z]*)\.?([a-zA-Z-]*)$/);
        if (!m) continue;
        const tag = (m[1] || "").toLowerCase();
        const cls = m[2] || "";
        if (!tag && !cls) continue;
        const found = [];
        (function walk(n) {
          for (const c of n.children) {
            const tagOk = !tag || c.tagName.toLowerCase() === tag;
            const clsOk = !cls || c._cls.has(cls);
            if (tagOk && clsOk) found.push(c);
            walk(c);
          }
        })(this);
        if (found.length) return found[0];
      }
      return null;
    },
    get className() { return Array.from(this._cls).join(" "); },
    set className(v) { this._cls = new Set(String(v).split(/\s+/).filter(Boolean)); },
    get innerHTML() {
      if (!this.children.length && !this._text) return this._innerHTML;
      return this._innerHTML || serialize(this);
    },
    set innerHTML(html) {
      this._innerHTML = String(html);
      this.children = this._innerHTML ? parseHtml(this._innerHTML, this) : [];
    },
  };
  el.classList = {
    add: (...cs) => cs.forEach((c) => el._cls.add(c)),
    remove: (...cs) => cs.forEach((c) => el._cls.delete(c)),
    toggle: (c, force) => {
      const want = force === undefined ? !el._cls.has(c) : !!force;
      if (want) el._cls.add(c); else el._cls.delete(c);
      return want;
    },
    contains: (c) => el._cls.has(c),
  };
  // offset* 与 style 同步（浏览器语义）：WM 的拖拽/八向缩放边界全靠它们
  for (const [prop, styleKey] of [["offsetLeft", "left"], ["offsetTop", "top"],
                                  ["offsetWidth", "width"], ["offsetHeight", "height"]]) {
    Object.defineProperty(el, prop, {
      get() { return parseInt(this.style[styleKey], 10) || 0; },
      set(v) { this.style[styleKey] = v + "px"; },
    });
  }
  el.pause = function () { this.paused = true; };
  el.play = function () { this.paused = false; };
  return el;
}

function serialize(el) {
  const tag = el.tagName.toLowerCase();
  let s = "<" + tag;
  for (const k of Object.keys(el.attributes)) s += " " + k + '="' + el.attributes[k] + '"';
  if (el._cls.size) s += ' class="' + Array.from(el._cls).join(" ") + '"';
  s += ">" + (el._text || "");
  for (const c of el.children) s += serialize(c);
  return s + "</" + tag + ">";
}

// 轻量 HTML 解析：只处理 WM 用到的标签/属性形态（属性值不含引号）；文本节点保留在父元素 _text
function parseHtml(html, parent) {
  const root = [];
  const stack = [root];
  const elStack = [null];
  const re = /<(\/?)([a-zA-Z][a-zA-Z0-9]*)((?:\s+[a-zA-Z-]+="[^"]*")*)\s*(\/?)>/g;
  let m, lastIdx = 0;
  while ((m = re.exec(html))) {
    const text = html.slice(lastIdx, m.index);
    lastIdx = re.lastIndex;
    const textParent = elStack[elStack.length - 1];
    if (text && text.trim() && textParent) textParent._text = (textParent._text || "") + text;
    if (m[1] === "/") {
      if (stack.length > 1) stack.pop();
      if (elStack.length > 1) elStack.pop();
      continue;
    }
    const el = makeEl(m[2]);
    const attrStr = m[3] || "";
    const are = /([a-zA-Z-]+)="([^"]*)"/g;
    let am;
    while ((am = are.exec(attrStr))) {
      if (am[1] === "class") am[2].split(/\s+/).filter(Boolean).forEach((c) => el._cls.add(c));
      else el.attributes[am[1]] = am[2];
    }
    el.parentNode = parent;
    stack[stack.length - 1].push(el);
    if (!m[4] && !VOID.has(m[2].toLowerCase())) {
      stack.push(el.children);
      elStack.push(el);
    }
  }
  return root;
}

function makeDocument() {
  const body = makeEl("body");
  const head = makeEl("head");
  const doc = {
    body,
    head,
    _listeners: {},
    createElement: (t) => makeEl(t),
    addEventListener(t, fn) { (this._listeners[t] = this._listeners[t] || []).push(fn); },
    removeEventListener(t, fn) {
      const ls = this._listeners[t];
      if (!ls) return;
      const i = ls.indexOf(fn);
      if (i >= 0) ls.splice(i, 1);
    },
    fire(t, ev) { (this._listeners[t] || []).slice().forEach((fn) => fn(ev || {})); },
  };
  return doc;
}

function makeWindow() {
  const win = {
    innerWidth: 1280,
    innerHeight: 720,
    _alerts: [],
    _listeners: {},
    fetch: null,
    addEventListener() {},
    alert(msg) { this._alerts.push(String(msg)); },
    _confirms: [],
    _confirmRet: true,
    confirm(msg) { this._confirms.push(String(msg)); return this._confirmRet; },
  };
  return win;
}

// ---------------------------------------------------------------------------
// 载入 WM（IIFE 内部用裸 window / document / fetch → 以参数注入）
// ---------------------------------------------------------------------------
function CustomEventStub(type, opts) {
  this.type = type;
  this.detail = (opts && opts.detail) || {};
  this.bubbles = !!(opts && opts.bubbles);
}
function loadWM(doc, win, fetchStub) {
  const code = fs.readFileSync(
    path.join(__dirname, "..", "..", "app", "web", "static", "js", "window-manager.js"),
    "utf8");
  const fn = new Function("window", "document", "fetch", "CustomEvent",
    code + "\nreturn window.WM;");
  return fn(win, doc, fetchStub, CustomEventStub);
}

// ---------------------------------------------------------------------------
// 断言
// ---------------------------------------------------------------------------
let passed = 0;
const failed = [];
function ok(cond, name, extra) {
  if (cond) { passed++; }
  else { failed.push(name); console.error("FAIL: " + name + (extra ? " — " + extra : "")); }
}
function eq(a, b, name) { ok(a === b, name, "got " + JSON.stringify(a) + ", want " + JSON.stringify(b)); }

function fresh(fetchStub) {
  const doc = makeDocument();
  const win = makeWindow();
  win.fetch = fetchStub || (() => Promise.resolve({ ok: true, status: 200, json: () => ({}) }));
  const WM = loadWM(doc, win, win.fetch);
  return { doc, win, WM };
}

function taskbarOf(doc) {
  const r = doc.body.children.find((c) => c.id === "wm-root");
  return r && r.children.find((c) => c.id === "wm-taskbar");
}
function openText(WM, over) {
  return WM.open(Object.assign({
    title: "A.md", kind: "text", rel: "A.md", name: "A.md",
    size: 100, text: "hello",
  }, over || {}));
}

async function main() {
  // 1. API 面完整
  {
    const { WM } = fresh();
    ["open", "openFile", "register", "viewers", "minimize", "restore",
     "fullscreen", "close", "state"].forEach((k) =>
      ok(typeof WM[k] === "function" || (k === "viewers" && typeof WM[k] === "object"),
        "API: WM." + k + " 存在"));
  }

  // 2. open：窗口进 #wm-root，chip 必须挂在 #wm-taskbar 下（回归：曾误挂 root）
  {
    const { doc, WM } = fresh();
    const w = openText(WM);
    const r = doc.body.children.find((c) => c.id === "wm-root");
    ok(r, "open: #wm-root 挂在 body");
    const el = r.children.find((c) => c._cls.has("wm-win"));
    ok(el, "open: .wm-win 在 root 内");
    const tb = r.children.find((c) => c.id === "wm-taskbar");
    ok(tb, "open: #wm-taskbar 在 root 内");
    const chip = tb.querySelector(".wm-task");
    ok(chip, "open: chip 在任务条内（回归）");
    ok(chip && tb.contains(chip), "open: chip 被 #wm-taskbar 包含（含 chip 容器）（回归）");
    eq(wmState(WM).length, 1, "open: state 有一个窗口");

    // 标题转义（XSS）：titlebar 的原始 innerHTML 必须是转义后的
    const w2 = WM.open({ title: '<b onclick=x>&"t"</b>', kind: "text", text: "", rel: "x", name: "x", size: 1 });
    const el2 = doc.body.children.find((c) => c.id === "wm-root").children
      .find((c) => c._cls.has("wm-win") && c !== el);
    const bar2 = el2.children.find((c) => c._cls.has("wm-titlebar"));
    ok(bar2 && bar2.innerHTML.indexOf("&lt;b") !== -1 &&
       bar2.innerHTML.indexOf("<b onclick") === -1,
       "open: 标题 HTML 转义");
    WM.close(w.id); WM.close(w2.id);
  }

  // 3. minimize：非媒体类内容保留（0.8.32 修订：销毁重建 = 编辑内容丢失，实机反馈）
  {
    const { doc, WM } = fresh();
    const w = openText(WM);
    ok(w.contentEl.children.length > 0, "minimize 前: 内容已构建");
    WM.minimize(w.id);
    eq(wmState(WM)[0].min, true, "minimize: state.min === true");
    ok(wmState(WM)[0].content > 0, "minimize: 文本内容 DOM 保留（不再销毁）");
    ok(w.el._cls.has("wm-min"), "minimize: wm-min 类已加");
    const tb = taskbarOf(doc);
    ok(!!tb.querySelector(".wm-task"), "minimize: chip 保留在任务条");

    // 媒体类仍销毁（释放解码内存）
    const v = WM.open({ title: "a.mp4", kind: "video", rel: "a.mp4", name: "a.mp4", size: 10 });
    ok(v.contentEl.children.length > 0, "minimize 前: 视频内容已构建");
    WM.minimize(v.id);
    eq(wmState(WM).find((s) => s.id === String(v.id)).content, 0,
      "minimize: 媒体类内容仍销毁（解码内存）");
    WM.restore(v.id);
    ok(wmState(WM).find((s) => s.id === String(v.id)).content > 0, "restore: 媒体类按指针重建");
  }

  // 3b. 文本编辑未保存 → 最小化再还原，内容必须还在（数据丢失回归）
  {
    const { WM } = fresh();
    const w = openText(WM);
    const ta = w.contentEl.querySelector("textarea");
    ok(ta, "3b: 文本编辑框存在");
    ta.value = "用户敲到一半的草稿";
    WM.minimize(w.id);
    WM.restore(w.id);
    const ta2 = w.contentEl.querySelector("textarea");
    ok(ta2 && ta2.value === "用户敲到一半的草稿",
      "3b: 未保存的编辑跨最小化保留（实机数据丢失回归）");
  }

  // 4. restore：内容重建 + 类移除
  {
    const { WM } = fresh();
    const w = openText(WM);
    WM.minimize(w.id);
    WM.restore(w.id);
    eq(wmState(WM)[0].min, false, "restore: state.min === false");
    ok(wmState(WM)[0].content > 0, "restore: 内容在");
    ok(!w.el._cls.has("wm-min"), "restore: wm-min 类已移除");
  }

  // 4b. 八向缩放手柄：8 个手柄存在；se/nw 拖拽按桌面语义改尺寸/位置
  {
    const { doc, WM } = fresh();
    const w = openText(WM);
    const r = doc.body.children.find((c) => c.id === "wm-root");
    const el = r.children.find((c) => c._cls.has("wm-win"));
    const handles = el.children.filter((c) => c._cls.has("wm-rz"));
    eq(handles.length, 8, "4b: 八个缩放手柄");
    ["wm-rz-n", "wm-rz-s", "wm-rz-e", "wm-rz-w", "wm-rz-ne", "wm-rz-nw",
     "wm-rz-se", "wm-rz-sw"].forEach((cls) =>
      ok(handles.some((h) => h._cls.has(cls)), "4b: 手柄 " + cls + " 存在"));

    // se：向右下拖 → 变大，位置不动
    const se = handles.find((h) => h._cls.has("wm-rz-se"));
    el.offsetWidth = 800; el.offsetHeight = 500;
    el.style.left = "100px"; el.style.top = "80px";
    se.fire("pointerdown", { clientX: 900, clientY: 580, preventDefault() {} });
    doc.fire("pointermove", { clientX: 960, clientY: 620 });
    doc.fire("pointerup", {});
    eq(el.style.width, "860px", "4b: se 拖拽变宽");
    eq(el.style.height, "540px", "4b: se 拖拽变高");
    eq(el.style.left, "100px", "4b: se 不动 left");
    eq(el.style.top, "80px", "4b: se 不动 top");

    // nw：向左上拖 → 变大且 left/top 跟移（右下角钉住）
    const nw = handles.find((h) => h._cls.has("wm-rz-nw"));
    el.offsetWidth = 800; el.offsetHeight = 500;
    nw.fire("pointerdown", { clientX: 100, clientY: 80, preventDefault() {} });
    doc.fire("pointermove", { clientX: 40, clientY: 30 });
    doc.fire("pointerup", {});
    eq(el.style.width, "860px", "4b: nw 反向拖变宽");
    eq(el.style.height, "550px", "4b: nw 反向拖变高");
    eq(el.style.left, "40px", "4b: nw left 跟移");
    eq(el.style.top, "30px", "4b: nw top 跟移");

    // 最小尺寸钳制：se 往回拖过头 → 不小于 320x200
    se.fire("pointerdown", { clientX: 900, clientY: 610, preventDefault() {} });
    doc.fire("pointermove", { clientX: 10, clientY: 10 });
    doc.fire("pointerup", {});
    ok(parseInt(el.style.width, 10) >= 320 && parseInt(el.style.height, 10) >= 200,
      "4b: 最小尺寸钳制 320x200");

    // 全屏时手柄不响应
    WM.fullscreen(el.dataset.wmid);
    const n = handles.find((h) => h._cls.has("wm-rz-n"));
    const before = el.style.height;
    n.fire("pointerdown", { clientX: 500, clientY: 0, preventDefault() {} });
    doc.fire("pointermove", { clientX: 500, clientY: 300 });
    doc.fire("pointerup", {});
    eq(el.style.height, before, "4b: 全屏时手柄不缩放");
  }

  // 5. close：窗口 + chip 全销毁，任务条隐藏
  {
    const { doc, WM } = fresh();
    const w = openText(WM);
    WM.close(w.id);
    eq(wmState(WM).length, 0, "close: state 为空");
    const r = doc.body.children.find((c) => c.id === "wm-root");
    ok(!r.children.some((c) => c._cls.has("wm-win")), "close: 窗口 DOM 已移除");
    const tb = r.children.find((c) => c.id === "wm-taskbar");
    const chips = tb.children.find((c) => c._cls.has("wm-task-chips"));
    eq(chips.children.length, 0, "close: chip 已移除");
    eq(tb.style.display, "none", "close: 任务条隐藏");
  }

  // 6. fullscreen：类切换
  {
    const { WM } = fresh();
    const w = openText(WM);
    WM.fullscreen(w.id);
    ok(w.el._cls.has("wm-fs"), "fullscreen: wm-fs 类已加");
    WM.fullscreen(w.id);
    ok(!w.el._cls.has("wm-fs"), "fullscreen: 再切一次移除");
  }

  // 7. 多窗口 z-order：后来的窗口在上
  {
    const { WM } = fresh();
    const a = openText(WM, { title: "a" });
    const b = openText(WM, { title: "b" });
    ok(Number(b.el.style.zIndex) > Number(a.el.style.zIndex),
      "z-order: 后开窗口 zIndex 更高");
    eq(wmState(WM).length, 2, "多窗口: state 有两个窗口");
  }

  // 8. text viewer：pre 内容 + 截断提示
  {
    const { WM } = fresh();
    const w = openText(WM, { truncated: true, text: "abc" });
    const pre = w.contentEl.querySelector("pre");
    ok(pre && pre.textContent === "abc", "text viewer: pre 文本正确");
    const note = w.contentEl.children.find((c) => c._cls.has("vw-note"));
    ok(note, "text viewer: 截断提示存在");
  }

  // 9. none viewer：不支持提示 + 下载链接
  {
    const { WM } = fresh();
    const w = WM.open({ title: "a.exe", kind: "none", rel: "a.exe", name: "a.exe", size: 1 });
    const a = w.contentEl.querySelector("a");
    ok(a && a.attributes.href === "/files/raw?path=a.exe&dl=1",
      "none viewer: 下载链接 raw?dl=1");
  }

  // 10. image viewer：img src + meta
  {
    const { WM } = fresh();
    const w = WM.open({ title: "p.png", kind: "image", rel: "pictures/p.png", name: "p.png", size: 2048 });
    const img = w.contentEl.querySelector("img");
    ok(img && img.src === "/files/raw?path=" + encodeURIComponent("pictures/p.png"),
      "image viewer: img src 拼 /files/raw");
    const meta = w.contentEl.querySelector(".vw-meta");
    ok(meta && meta.textContent.indexOf("2.0 KB") !== -1, "image viewer: meta 含大小");
  }

  // 11. 媒体续播：minimize 抓 currentTime，restore 后 loadedmetadata 回放
  {
    const { WM } = fresh();
    const w = WM.open({ title: "v.mp4", kind: "video", rel: "v.mp4", name: "v.mp4", size: 99 });
    const v1 = w.contentEl.querySelector("video");
    ok(v1, "video viewer: <video> 存在");
    v1.currentTime = 5.5;
    WM.minimize(w.id);
    eq(w.state && w.state.t, 5.5, "minimize: 播放进度已抓取");
    WM.restore(w.id);
    const v2 = w.contentEl.querySelector("video");
    ok(v2 && v2 !== v1, "restore: 新的 <video> 元素");
    v2.fire("loadedmetadata");
    eq(v2.currentTime, 5.5, "restore: 续播到保存位置");
  }

  // 12. openFile 成功：扁平描述符 → 开窗
  {
    const { WM } = fresh(() => Promise.resolve({
      ok: true, status: 200,
      json: () => ({ ok: true, kind: "text", rel: "README.md", name: "README.md",
                     size: 10, text: "x" }),
    }));
    WM.openFile({ rel: "README.md" });
    await tick();
    eq(wmState(WM).length, 1, "openFile: 窗口已开");
    eq(wmState(WM)[0].title, "README.md", "openFile: 标题取 d.name");
  }

  // 13. openFile 失败：alert 提示
  {
    const { win, WM } = fresh(() => Promise.resolve({ ok: false, status: 404, json: () => ({}) }));
    WM.openFile({ rel: "missing.md" });
    await tick();
    eq(wmState(WM).length, 0, "openFile 失败: 未开窗");
    ok(win._alerts.length === 1 && win._alerts[0].indexOf("打开失败") !== -1,
      "openFile 失败: alert 提示");
  }

  // 14. viewer 工厂抛异常 → 降级错误框（不炸窗口）
  {
    const { WM } = fresh();
    WM.register("boom", function () { throw new Error("kaput"); });
    const w = WM.open({ title: "b", kind: "boom", rel: "b", name: "b", size: 1 });
    ok(w.contentEl.querySelector(".vw-err"), "factory 异常: 降级错误框");
    ok(w.contentEl.querySelector(".vw-err").innerHTML.indexOf("kaput") !== -1,
      "factory 异常: 错误信息展示");
  }

  // 15. 按需加载：同 src 只注入一次；失败摘除并可重试
  {
    const { WM, doc } = fresh();
    const p1 = WM.loadScript("/static/js/pdf.js");
    const p2 = WM.loadScript("/static/js/pdf.js");
    ok(p1 === p2, "loadScript: 同 src 返回同一 promise（不重复注入）");
    const tags = doc.head.children.filter((c) => c.tagName === "SCRIPT");
    eq(tags.length, 1, "loadScript: 只注入一个 script 标签");
    eq(tags[0].src, "/static/js/pdf.js", "loadScript: src 正确");
    tags[0].onload();
    await p1;
    ok(true, "loadScript: onload 后 resolve");
    const p3 = WM.loadScript("/static/js/bad.js");
    const bad = doc.head.children.filter((c) => c.tagName === "SCRIPT" && c.src === "/static/js/bad.js")[0];
    ok(!!bad, "loadScript: 失败前标签已注入");
    bad.onerror();
    let rejected = false;
    try { await p3; } catch (e) { rejected = true; }
    ok(rejected, "loadScript: onerror 后 reject");
    ok(doc.head.children.filter((c) => c.tagName === "SCRIPT" && c.src === "/static/js/bad.js").length === 0,
      "loadScript: 失败摘除标签");
    const p4 = WM.loadScript("/static/js/bad.js");
    ok(p4 !== p3, "loadScript: 失败后可重试（缓存已清）");
  }

  // 16. 多任务栏：计数 + 全部还原/关闭 + 同文件单例
  {
    const { doc, WM } = fresh();
    const tb = () => taskbarOf(doc);
    const status = () => tb().children.find((c) => c._cls.has("wm-task-status"))._text
      || tb().children.find((c) => c._cls.has("wm-task-status")).textContent;
    const w1 = openText(WM, { rel: "a.txt" });
    const w2 = openText(WM, { rel: "b.txt" });
    ok(status().indexOf("2 个窗口") !== -1, "任务条: 2 个窗口计数");
    WM.minimize(w1.id); WM.minimize(w2.id);
    ok(status().indexOf("2 个已最小化") !== -1, "任务条: 最小化计数");
    WM.restoreAll();
    eq(wmState(WM).filter((w) => w.min).length, 0, "全部还原: 无最小化窗口");
    ok(status().indexOf("已最小化") === -1, "任务条: 还原后计数清零");
    WM.closeAll();
    eq(wmState(WM).length, 0, "全部关闭: state 为空");
    eq(tb().style.display, "none", "全部关闭: 任务条隐藏");
  }
  // 17. openFile 同文件单例：重复打开聚焦已有窗口，不新增
  {
    const desc = { ok: true, kind: "text", rel: "same.txt", name: "same.txt",
                   size: 3, text: "hi", truncated: false };
    const { WM } = fresh(() => Promise.resolve({
      ok: true, status: 200, json: () => desc,
    }));
    WM.openFile({ rel: "same.txt" });
    await tick(); await tick();
    eq(wmState(WM).length, 1, "openFile: 首次打开建窗");
    const first = wmState(WM)[0].id;
    WM.openFile({ rel: "same.txt" });
    await tick(); await tick();
    eq(wmState(WM).length, 1, "openFile: 重复打开不新增窗口");
    eq(wmState(WM)[0].id, first, "openFile: 聚焦的是同一窗口");
    WM.openFile({ rel: "other.txt" });
    await tick(); await tick();
    eq(wmState(WM).length, 2, "openFile: 不同文件正常多开");
  }

  // 18. 不可预览类型：不建窗 + toast + 直接下载
  {
    const desc = { ok: false, kind: "none", rel: "scratch/a.exe", name: "a.exe", size: 10 };
    const seen = [];
    const { doc, win, WM } = fresh(() => Promise.resolve({
      ok: true, status: 200, json: () => desc,
    }));
    // 拦截锚点点击，记录下载 href（真实浏览器里走原生下载）
    const _append = doc.body.appendChild.bind(doc.body);
    doc.body.appendChild = (c) => {
      if (c.tagName === "A" && String(c.href).indexOf("dl=1") !== -1) seen.push(c.href);
      return _append(c);
    };
    let toasted = null;
    doc.body.addEventListener("console:toast", (e) => { toasted = e.detail; });
    WM.openFile({ rel: "scratch/a.exe" });
    await tick(); await tick();
    eq(wmState(WM).length, 0, "不可预览: 不建窗口");
    ok(toasted && toasted.message.indexOf("不支持在线预览") !== -1,
      "不可预览: toast 提示");
    eq(seen.length, 1, "不可预览: 触发一次下载");
    ok(seen[0].indexOf(encodeURIComponent("scratch/a.exe")) !== -1,
      "不可预览: 下载指向原文件");
    eq(win._alerts.length, 0, "不可预览: 无错误弹窗");
  }

  // 19. HTML 沙盒预览三档：纯静态 → 脚本开 → 完整预览（确认）→ 纯静态
  {
    const { win, WM } = fresh();
    const w = WM.open({ title: "p.html", kind: "html", rel: "p.html",
                        name: "p.html", size: 100 });
    const frame = w.contentEl.querySelector("iframe.vw-html");
    ok(frame, "html: 沙盒 iframe 已构建");
    eq(frame.attributes.sandbox, "", "html: 默认纯静态（sandbox 空）");
    ok(frame.src.indexOf("/files/raw?path=") !== -1 && frame.src.indexOf("raw-html") === -1,
      "html: 默认源走严格 raw");
    const tgl = w.contentEl.querySelector("button.vw-tbtn");
    ok(tgl, "html: 模式开关按钮存在");
    tgl.click();
    eq(frame.attributes.sandbox, "allow-scripts allow-forms", "html: 脚本开");
    eq(tgl.textContent, "脚本开", "html: 二档文案");
    ok(frame.src.indexOf("/files/raw?path=") !== -1, "html: 脚本开仍走严格 raw");
    tgl.click();
    eq(win._confirms.length, 1, "html: 进完整预览前显式确认");
    eq(tgl.textContent, "完整预览", "html: 三档文案");
    ok(frame.src.indexOf("/files/raw-html?path=") !== -1, "html: 完整预览走宽松源");
    // 拒绝确认 → 停在二档
    win._confirmRet = false;
    tgl.click();  // 回纯静态（无需确认）
    eq(tgl.textContent, "纯静态", "html: 可回纯静态");
    tgl.click();  // 进脚本开
    tgl.click();  // 进完整预览 → 被拒
    eq(tgl.textContent, "脚本开", "html: 拒绝确认则停在脚本开");
    const open = w.contentEl.querySelector("a.vw-tbtn");
    ok(open && open.href.indexOf("/files/raw-html?path=") !== -1,
      "html: 新标签页打开走宽松源（顶层同样被沙盒）");
    WM.close(w.id);
  }

  console.log(passed + " passed, " + failed.length + " failed");
  process.exit(failed.length ? 1 : 0);
}

function wmState(WM) { return WM.state(); }
function tick() { return new Promise((r) => setTimeout(r, 0)); }

main().catch((e) => { console.error("HARNESS ERROR:", e); process.exit(2); });

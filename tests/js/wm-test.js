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
    offsetLeft: 0, offsetTop: 0,
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
  const doc = {
    body,
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
  };
  return win;
}

// ---------------------------------------------------------------------------
// 载入 WM（IIFE 内部用裸 window / document / fetch → 以参数注入）
// ---------------------------------------------------------------------------
function loadWM(doc, win, fetchStub) {
  const code = fs.readFileSync(
    path.join(__dirname, "..", "..", "app", "web", "static", "js", "window-manager.js"),
    "utf8");
  const fn = new Function("window", "document", "fetch", code + "\nreturn window.WM;");
  return fn(win, doc, fetchStub);
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
    const chip = tb.children.find((c) => c._cls.has("wm-task"));
    ok(chip, "open: chip 在任务条内（回归）");
    ok(chip && chip.parentNode === tb, "open: chip.parentNode === #wm-taskbar（回归）");
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

  // 3. minimize：内容 DOM 销毁 + wm-min + chip 保留
  {
    const { doc, WM } = fresh();
    const w = openText(WM);
    ok(w.contentEl.children.length > 0, "minimize 前: 内容已构建");
    WM.minimize(w.id);
    eq(wmState(WM)[0].min, true, "minimize: state.min === true");
    eq(wmState(WM)[0].content, 0, "minimize: 内容 DOM 已销毁");
    ok(w.el._cls.has("wm-min"), "minimize: wm-min 类已加");
    const tb = taskbarOf(doc);
    ok(tb.children.some((c) => c._cls.has("wm-task")), "minimize: chip 保留在任务条");
  }

  // 4. restore：内容重建 + 类移除
  {
    const { WM } = fresh();
    const w = openText(WM);
    WM.minimize(w.id);
    WM.restore(w.id);
    eq(wmState(WM)[0].min, false, "restore: state.min === false");
    ok(wmState(WM)[0].content > 0, "restore: 内容已重建");
    ok(!w.el._cls.has("wm-min"), "restore: wm-min 类已移除");
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
    eq(tb.children.length, 0, "close: chip 已移除");
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

  console.log(passed + " passed, " + failed.length + " failed");
  process.exit(failed.length ? 1 : 0);
}

function wmState(WM) { return WM.state(); }
function tick() { return new Promise((r) => setTimeout(r, 0)); }

main().catch((e) => { console.error("HARNESS ERROR:", e); process.exit(2); });

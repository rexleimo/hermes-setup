// ============================================================================
// 文件工作台窗口化前端回归（0.8.38：列表首块缺 .wb-pblock-list）。
//
// 用 jsdom 实际加载真实 app.js，做 3 组受控断言：
//   G1 网格块 0 仍为网格样式 .wb-pblock（修复不得波及网格）；
//   L1 列表块 0 必须为 .wb-pblock wb-pblock-list，且卡片全部包入块（0.8.38 回归项）；
//   B1 整 body boost 换目录后，旧目录块消失、新目录块正确挂出（无 stale 残留）。
//
// 跑法: node tests/js/wb-virt-test.js   （需 node_modules/jsdom；缺则打印 SKIP）
// pytest 包装: tests/test_wb_virt_js.py
// ============================================================================
"use strict";
const fs = require("fs");
const path = require("path");

let PASS = 0, FAIL = 0, FAILS = [];
function check(name, cond, detail) {
  if (cond) { PASS++; }
  else { FAIL++; FAILS.push(name + (detail ? " :: " + detail : "")); }
}

let jsdom;
try { jsdom = require("jsdom"); } catch (e) {
  console.log("SKIP(no jsdom): 需 node_modules/jsdom 才能运行此前端回归");
  process.exit(0);
}
const { JSDOM } = jsdom;
const APP_JS = path.resolve(__dirname, "../../app/web/static/js/app.js");
if (!fs.existsSync(APP_JS)) { console.log("FAIL(app.js missing): " + APP_JS); process.exit(1); }
const APP_SRC = fs.readFileSync(APP_JS, "utf8");

// 镜像 files.html 内容区的 workbench 外壳（含 #osfm-root data-here、#osfm-content）。
function win11(inner) {
  return `<div class="win11" id="osfm-root" data-here="view=grid" data-admin="0">
    <div class="e-content" id="osfm-content">${inner}</div>
  </div>`;
}
function gridContent(total, cards) {
  let body = "";
  for (let i = 0; i < cards; i++)
    body += `<div class="osfm-card-item"><div class="e-fname">f${i}</div></div>`;
  return `<div class="e-grid" id="osfm-items" data-total="${total}" data-view="grid">${body}</div>`;
}
function listContent(total, cards) {
  let rows = "";
  for (let i = 0; i < cards; i++)
    rows += `<tr class="osfm-card-item" data-rel="f${i}"><td>f${i}</td><td>x</td><td>y</td><td>z</td></tr>`;
  return `<table class="e-table" id="osfm-items" data-total="${total}" data-view="list">
    <thead><tr><th>名称</th><th>日期</th><th>类型</th><th>大小</th></tr></thead><tbody>${rows}</tbody></table>`;
}

function mount(jsSrc) {
  const dom = new JSDOM(`<!DOCTYPE html><html><head></head><body></body></html>`,
    { url: "http://localhost/", runScripts: "outside-only" });
  const w = dom.window;
  w.htmx = w.htmx || {};
  // fetch 一律返回空（不触发异步取块），保证断言只针对已渲染首屏，结果确定。
  w.fetch = () => Promise.resolve({ ok: true, text: () => Promise.resolve("") });
  w.requestAnimationFrame = (cb) => cb();
  const realGet = w.getComputedStyle;
  w.getComputedStyle = function (el) {
    const s = realGet ? realGet.call(w, el) : {};
    const o = { overflowY: s.overflowY || "", overflowX: s.overflowX || "" };
    o.getBoundingClientRect = () => ({ top: 0, left: 0, width: 1000, height: 800, bottom: 800, right: 1000 });
    return o;
  };
  Object.defineProperty(w.Element.prototype, "clientHeight", { get() { return 800; }, configurable: true });
  Object.defineProperty(w.Element.prototype, "offsetHeight", { get() { return 120; }, configurable: true });
  Object.defineProperty(w.Element.prototype, "scrollTop", { get() { return 0; }, configurable: true });
  // 一次性注入并执行真实 app.js（事件监听挂在 document 上，跨场景保留）。
  w.eval(jsSrc);
  return w;
}
function setContent(w, inner) { w.document.body.innerHTML = win11(inner); }
function fire(w) {
  w.document.dispatchEvent(new w.Event("htmx:afterSwap", { bubbles: true }));
  w.document.dispatchEvent(new w.Event("DOMContentLoaded", { bubbles: true }));
}
const firstBlock = (w) => {
  const items = w.document.getElementById("osfm-items");
  return items ? items.querySelector(".wb-pblock") : null;
};

// ---------- G1 网格 ----------
(function grid() {
  const w = mount(APP_SRC);
  setContent(w, gridContent(30, 3));
  fire(w);
  const items = w.document.getElementById("osfm-items");
  const b0 = firstBlock(w);
  check("G1 网格完成窗口化(.wb-virt)", !!items && items.classList.contains("wb-virt"));
  check("G1 网格块0存在", !!b0);
  check("G1 网格块0为网格样式(.wb-pblock)", b0 && b0.className === "wb-pblock", b0 && b0.className);
  check("G1 网格卡片块内渲染", !!b0 && b0.querySelectorAll(".osfm-card-item").length === 3,
    b0 && String(b0.querySelectorAll(".osfm-card-item").length));
})();

// ---------- L1 列表（0.8.38 回归项）----------
(function list() {
  const w = mount(APP_SRC);
  setContent(w, listContent(30, 6));
  fire(w);
  const blocks = w.document.querySelectorAll(".wb-pblock-list");
  check("L1 列表至少生成一个列表块", blocks.length >= 1, "blocks=" + blocks.length);
  const first = blocks[0];
  check("L1 列表块0精确类名 .wb-pblock wb-pblock-list", !!first && first.className === "wb-pblock wb-pblock-list",
    first && first.className);
  const cardsInBlocks = first ? first.querySelectorAll(".osfm-card-item").length : 0;
  check("L1 列表首块包入全部卡片", cardsInBlocks === 6, "cards=" + cardsInBlocks);
  const rawLeft = w.document.querySelectorAll("#osfm-items > table.e-table .osfm-card-item").length;
  check("L1 表内无散落行", rawLeft === 0, "raw=" + rawLeft);
})();

// ---------- B1 整 body boost 换目录 ----------
(function boost() {
  const w = mount(APP_SRC);
  setContent(w, gridContent(30, 3));   // 目录 A
  fire(w);
  check("B1 首屏块数=1", w.document.querySelectorAll(".wb-pblock").length === 1);
  setContent(w, gridContent(80, 4));   // boost 到目录 B（整 body 替换 = 新容器）
  fire(w);
  check("B1 boost 后仅含新目录块(1)", w.document.querySelectorAll(".wb-pblock").length === 1);
  check("B1 boost 后新目录卡片挂出(4)",
    w.document.querySelectorAll("#osfm-items .osfm-card-item").length === 4);
})();

// ---------- 结果 ----------
console.log(`${PASS} passed, ${FAIL} failed`);
if (FAIL) { console.log("FAILURES:"); FAILS.forEach((f) => console.log("  - " + f)); }
process.exit(FAIL ? 1 : 0);

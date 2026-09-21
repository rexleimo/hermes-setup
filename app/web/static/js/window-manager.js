// ============================================================================
// WebOS 窗口管理器（通用层）+ 文件 Viewer 注册表
//
// 设计（面向扩展）：
//   1. WM 核心只管窗口生命周期：打开 / 拖拽 / 置顶 / 最小化 / 全屏 / 关闭，
//      与"文件"无关——任何页面都可以 WM.open(spec) 开一个窗口。
//   2. 内容由 Viewer 注册表构建：WM.register(kind, factory)。
//      新增文件类型 = 后端 preview_class 加分支 + 这里 register 一个条目。
//   3. 内存语义（0.8.32 修订）：
//      - 关闭：窗口框架 + 内容 DOM 全部销毁；
//      - 最小化：只有 video/audio 销毁内容（释放解码内存，还原按进度续播）；
//        其余类型只隐藏——文本未保存的编辑 / HTML 预览档位 / 图片查看模式 /
//        PDF 滚动位置都长在内容 DOM 上，销毁重建 =「最小化回来数据不对」
//        （实机反馈：编辑中的文字最小化再还原就丢了），绝不接受；
//      - 全屏：仅 CSS 切换，内容保留。
//   4. #wm-root 容器挂在 body 上；hx-boost 换页后自动挂回，
//      因此窗口/任务条跨页面导航存活（OS 语义）。
// ============================================================================
(function () {
  "use strict";

  var wins = {};        // id -> 窗口指针（最小化后仍保留，内容已销毁）
  var nextId = 1;
  var zTop = 100;
  var focusedId = null;
  var root = null;      // #wm-root
  var taskbar = null;
  var cascade = 0;

  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }

  // 与 app.js WB_SVG 同源的缩略图标（任务条/标题栏用）
  var ICO = {
    image: '<rect x="3" y="5" width="18" height="14" rx="2" fill="#c7e5f8"/><circle cx="8.5" cy="10" r="1.8" fill="#f0a92e"/><path d="M4.5 17.5 10 12l3.3 3.3L16 12.5l3.5 5z" fill="#3aa061"/>',
    video: '<rect x="3" y="5" width="18" height="14" rx="2" fill="#ddd0f5"/><path d="M10 9v6l5-3z" fill="#6d3fc0"/>',
    audio: '<path d="M9.2 17.5V6.8L18.5 5v10.7" fill="none" stroke="#d6538f" stroke-width="1.8" stroke-linejoin="round"/><circle cx="6.7" cy="17.5" r="2.6" fill="#f2a6c8"/><circle cx="16" cy="15.7" r="2.6" fill="#f2a6c8"/>',
    document: '<path fill="#d3e3f8" d="M6 2h8l5 5v13a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z"/><path fill="#9dbde3" d="M14 2l5 5h-5z"/><rect x="7" y="11" width="9.5" height="1.6" rx=".8" fill="#3b6fb5"/><rect x="7" y="14.4" width="9.5" height="1.6" rx=".8" fill="#3b6fb5"/>',
    code: '<path fill="#f9e7c4" d="M6 2h8l5 5v13a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z"/><path fill="#e3bd7d" d="M14 2l5 5h-5z"/><path d="m10.2 12-2.2 2.2 2.2 2.2M13.8 12l2.2 2.2-2.2 2.2" fill="none" stroke="#b57d2a" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>',
    file: '<path fill="#e3e6ea" d="M6 2h8l5 5v13a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z"/><path fill="#c3c9d1" d="M14 2l5 5h-5z"/>'
  };
  var KIND_ICO = { image: "image", video: "video", audio: "audio",
                   pdf: "document", text: "code", html: "code", none: "file" };

  function svg(name, cls) {
    return '<svg class="' + (cls || "wm-ico") + '" viewBox="0 0 24 24" aria-hidden="true">' +
           (ICO[name] || ICO.file) + "</svg>";
  }
  function rawUrl(rel) { return "/files/raw?path=" + encodeURIComponent(rel); }
  function rawHtmlUrl(rel) { return "/files/raw-html?path=" + encodeURIComponent(rel); }

  // 按需加载：重型 viewer 依赖（PDF.js / 编辑器 / HTML 净化器）只在首次
  // 打开对应类型时注入，promise 缓存保证同一依赖只加载一次；失败时
  // reject，由 buildContent 统一降级为错误面板（不白屏、不污染窗口）。
  var scriptCache = {};
  function loadScriptOnce(src) {
    if (scriptCache[src]) return scriptCache[src];
    var p = new Promise(function (resolve, reject) {
      var s = document.createElement("script");
      s.src = src;
      s.async = true;
      s.onload = function () { resolve(); };
      s.onerror = function () {
        if (s.remove) s.remove();
        else if (s.parentNode) s.parentNode.removeChild(s);
        delete scriptCache[src];
        reject(new Error("脚本加载失败：" + src));
      };
      document.head.appendChild(s);
    });
    scriptCache[src] = p;
    return p;
  }

  // ------------------------------------------------------------------
  // 根容器 / 任务条（跨 hx-boost 换页存活）
  // ------------------------------------------------------------------
  var taskStatus = null;   // 状态计数：N 个窗口（M 个已最小化）
  var taskChips = null;    // chip 容器（多窗口横向滚动）
  var taskRestoreAll = null;
  var taskCloseAll = null;
  function ensureRoot() {
    if (root && document.body.contains(root)) return root;
    if (!root) {
      root = document.createElement("div");
      root.id = "wm-root";
      taskbar = document.createElement("div");
      taskbar.id = "wm-taskbar";
      taskStatus = document.createElement("span");
      taskStatus.className = "wm-task-status";
      taskChips = document.createElement("span");
      taskChips.className = "wm-task-chips";
      var acts = document.createElement("span");
      acts.className = "wm-task-acts";
      taskRestoreAll = document.createElement("button");
      taskRestoreAll.type = "button";
      taskRestoreAll.className = "wm-task-act";
      taskRestoreAll.textContent = "全部还原";
      taskRestoreAll.addEventListener("click", restoreAll);
      taskCloseAll = document.createElement("button");
      taskCloseAll.type = "button";
      taskCloseAll.className = "wm-task-act";
      taskCloseAll.textContent = "全部关闭";
      taskCloseAll.addEventListener("click", closeAll);
      acts.appendChild(taskRestoreAll);
      acts.appendChild(taskCloseAll);
      taskbar.appendChild(taskStatus);
      taskbar.appendChild(taskChips);
      taskbar.appendChild(acts);
      root.appendChild(taskbar);
    }
    document.body.appendChild(root);
    return root;
  }
  document.addEventListener("htmx:afterSwap", function () {
    if (root && !root.parentNode) document.body.appendChild(root);
  });

  function taskChip(win) {
    if (win.chip) return win.chip;
    var b = document.createElement("button");
    b.type = "button";
    b.className = "wm-task";
    b.title = win.title;
    b.innerHTML = svg(KIND_ICO[win.kind] || "file", "wm-task-ico") +
      '<span class="wm-task-name">' + esc(win.title) + "</span>";
    b.addEventListener("click", function () {
      if (win.min) restore(win.id);
      else if (focusedId === win.id) minimize(win.id);
      else focus(win.id);
    });
    win.chip = b;
    ensureRoot();
    taskChips.appendChild(b);
    return b;
  }
  function taskRefresh() {
    ensureRoot();
    var ids = Object.keys(wins);
    taskbar.style.display = ids.length ? "" : "none";
    var minCount = ids.filter(function (id) { return wins[id].min; }).length;
    taskStatus.textContent = ids.length + " 个窗口"
      + (minCount ? "（" + minCount + " 个已最小化）" : "");
    taskRestoreAll.disabled = !minCount;
    ids.forEach(function (id) {
      var w = wins[id];
      if (!w.chip) taskChip(w);
      w.chip.classList.toggle("active", focusedId === id && !w.min);
    });
  }
  function restoreAll() {
    Object.keys(wins).forEach(function (id) { restore(id); });
  }
  function closeAll() {
    var ids = Object.keys(wins);
    for (var i = 0; i < ids.length; i++) {
      if (!wins[ids[i]]) continue;
      closeWin(ids[i]);
      // 某个窗口取消了关闭确认（未保存提醒）→ 停下，不再关后面的
      if (wins[ids[i]]) return;
    }
  }

  // ------------------------------------------------------------------
  // 窗口生命周期
  // ------------------------------------------------------------------
  function focus(id) {
    var w = wins[id];
    if (!w || !w.el || w.min) return;
    focusedId = id;
    w.el.style.zIndex = String(++zTop);
    taskRefresh();
  }

  function open(spec) {
    ensureRoot();
    var id = nextId++;
    var vw = window.innerWidth, vh = window.innerHeight;
    var w = Math.min(Math.max(spec.width || Math.round(vw * 0.7), 360), Math.round(vw * 0.92));
    var h = Math.min(Math.max(spec.height || Math.round(vh * 0.72), 280), Math.round(vh * 0.86));
    var off = (cascade++ % 6) * 32;
    var x = Math.max(8, Math.round((vw - w) / 2) - 40 + off);
    var y = Math.max(8, Math.round((vh - h) / 2) - 24 + off);

    var el = document.createElement("section");
    el.className = "wm-win";
    el.dataset.wmid = String(id);
    el.style.left = x + "px"; el.style.top = y + "px";
    el.style.width = w + "px"; el.style.height = h + "px";

    var bar = document.createElement("header");
    bar.className = "wm-titlebar";
    bar.innerHTML =
      svg(KIND_ICO[spec.kind] || "file", "wm-ico") +
      '<span class="wm-title">' + esc(spec.title) + "</span>" +
      '<span class="wm-spacer"></span>' +
      '<button type="button" class="wm-btn wm-min" title="最小化（任务条保留）">' +
        '<svg viewBox="0 0 16 16"><path d="M3 8.5h10" stroke="currentColor" stroke-width="1.4"/></svg></button>' +
      '<button type="button" class="wm-btn wm-fs" title="全屏">' +
        '<svg viewBox="0 0 16 16"><path d="M3 6V3h3M13 6V3h-3M3 10v3h3M13 10v3h-3" fill="none" stroke="currentColor" stroke-width="1.4"/></svg></button>' +
      '<button type="button" class="wm-btn wm-close" title="关闭（销毁窗口）">' +
        '<svg viewBox="0 0 16 16"><path d="m4 4 8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.4"/></svg></button>';

    var content = document.createElement("div");
    content.className = "wm-content";

    el.appendChild(bar);
    el.appendChild(content);

    // 桌面式八向缩放（n/s/e/w + 四角）：CSS resize 只有右下角一个手柄，
    // 实机反馈不友好。手柄贴边内嵌（.wm-win overflow:hidden 会裁掉越界部分），
    // 拖拽边界与最小尺寸同 open() 一致；全屏时 CSS 隐藏。
    ["n", "s", "e", "w", "ne", "nw", "se", "sw"].forEach(function (dir) {
      var h = document.createElement("div");
      h.className = "wm-rz wm-rz-" + dir;
      h.addEventListener("pointerdown", function (ev) {
        if (win.fs) return;
        focus(id);
        ev.preventDefault();
        var sx = ev.clientX, sy = ev.clientY;
        var ow = el.offsetWidth, oh = el.offsetHeight;
        var ox = el.offsetLeft, oy = el.offsetTop;
        var MINW = 320, MINH = 200;
        function mv(e2) {
          var dx = e2.clientX - sx, dy = e2.clientY - sy;
          var nw = ow, nh = oh, nl = ox, nt = oy;
          if (dir.indexOf("e") >= 0)
            nw = Math.max(MINW, Math.min(ow + dx, vw - ox));
          if (dir.indexOf("s") >= 0)
            nh = Math.max(MINH, Math.min(oh + dy, vh - oy));
          if (dir.indexOf("w") >= 0) {
            nw = Math.max(MINW, Math.min(ow - dx, ox + ow));
            nl = ox + (ow - nw);
          }
          if (dir.indexOf("n") >= 0) {
            nt = Math.max(0, oy + (oh - Math.max(MINH, oh - dy)));
            nh = oh + (oy - nt);
          }
          el.style.width = nw + "px";
          el.style.height = nh + "px";
          el.style.left = nl + "px";
          el.style.top = nt + "px";
        }
        function up() {
          document.removeEventListener("pointermove", mv);
          document.removeEventListener("pointerup", up);
        }
        document.addEventListener("pointermove", mv);
        document.addEventListener("pointerup", up);
      });
      el.appendChild(h);
    });

    root.insertBefore(el, taskbar);

    var win = {
      id: id, title: spec.title, kind: spec.kind || "none",
      el: el, contentEl: content, chip: null,
      min: false, fs: false,
      spec: spec            // 指针：重建内容所需的全部信息
    };
    wins[id] = win;
    taskRefresh();

    // 标题栏拖拽（按钮区除外；全屏时不拖）
    bar.addEventListener("pointerdown", function (ev) {
      if (ev.target.closest(".wm-btn") || win.fs) return;
      focus(id);
      var sx = ev.clientX, sy = ev.clientY;
      var ox = el.offsetLeft, oy = el.offsetTop;
      // 边界按拖拽开始时的当下尺寸/视口算：缩放之后还用 open() 的旧值会锁错范围
      var cw = el.offsetWidth, vwNow = window.innerWidth, vhNow = window.innerHeight;
      function mv(e2) {
        el.style.left = Math.min(Math.max(ox + e2.clientX - sx, -cw + 120), vwNow - 120) + "px";
        el.style.top = Math.min(Math.max(oy + e2.clientY - sy, 0), vhNow - 60) + "px";
      }
      function up() {
        document.removeEventListener("pointermove", mv);
        document.removeEventListener("pointerup", up);
      }
      document.addEventListener("pointermove", mv);
      document.addEventListener("pointerup", up);
      ev.preventDefault();
    });
    bar.addEventListener("dblclick", function (ev) {
      if (!ev.target.closest(".wm-btn")) toggleFs(id);
    });
    el.addEventListener("pointerdown", function () { focus(id); });

    bar.querySelector(".wm-min").addEventListener("click", function () { minimize(id); });
    bar.querySelector(".wm-fs").addEventListener("click", function () { toggleFs(id); });
    bar.querySelector(".wm-close").addEventListener("click", function () { closeWin(id); });

    buildContent(win);
    focus(id);
    return win;
  }

  // 内容构建（打开 / 还原共用）：factory 异常时降级提示
  // 工厂签名 (spec, box, state, win)：第 4 个参数供编辑型 viewer
  // 注册关闭前检查（win.onBeforeClose）等窗口级能力，老工厂忽略即可。
  function buildContent(win) {
    win.contentEl.innerHTML = "";
    var factory = viewers[win.spec.kind] || viewers.none;
    try {
      factory(win.spec, win.contentEl, win.state || {}, win);
    } catch (err) {
      win.contentEl.innerHTML =
        '<div class="vw-err"><p>内容加载失败：' + esc(err && err.message) + "</p>" +
        '<a class="btn btn-secondary btn-sm" href="' + rawUrl(win.spec.rel) + '" target="_blank">直接打开</a></div>';
    }
  }

  // 销毁前抓媒体进度（还原时续播）
  function captureState(win) {
    var m = win.contentEl.querySelector("video, audio");
    return { t: m && m.currentTime ? m.currentTime : 0 };
  }
  function destroyContent(win) {
    var m = win.contentEl.querySelector("video, audio");
    if (m) { try { m.pause(); } catch (e) {} }
    win.contentEl.innerHTML = "";   // DOM 直接销毁，释放解码内存
  }

  function minimize(id) {
    var win = wins[id];
    if (!win || win.min) return;
    // 只有媒体类销毁内容（释放解码内存，还原按进度续播）。文本未保存的编辑、
    // HTML 预览档位、图片查看模式、PDF 滚动位置都长在内容 DOM 上，销毁重建
    // 就是「最小化回来数据不对」——这是实机反馈的数据丢失根因。
    if (win.kind === "video" || win.kind === "audio") {
      win.state = captureState(win);
      destroyContent(win);
    }
    win.min = true;
    win.el.classList.add("wm-min");
    focusedId = null;
    taskRefresh();
  }

  function restore(id) {
    var win = wins[id];
    if (!win || !win.min) return;
    win.min = false;
    win.el.classList.remove("wm-min");
    // 媒体类被销毁过才重建；其余类型内容原样还在，怎么最小化就怎么回来
    if (!win.contentEl.children.length) buildContent(win);
    focus(id);
  }

  function toggleFs(id) {
    var win = wins[id];
    if (!win) return;
    win.fs = !win.fs;
    win.el.classList.toggle("wm-fs", win.fs);
    focus(id);
  }

  function closeWin(id) {
    var win = wins[id];
    if (!win) return;
    // 编辑器等可注册关闭前检查：返回 false 即取消关闭（未保存提醒）
    if (win.onBeforeClose) {
      try { if (!win.onBeforeClose()) return; } catch (e) {}
    }
    destroyContent(win);
    win.el.remove();
    if (win.chip) win.chip.remove();
    delete wins[id];
    if (focusedId === id) focusedId = null;
    taskRefresh();
  }

  // ------------------------------------------------------------------
  // Viewer 注册表（扩展点：新文件类型 = register 一个条目）
  // ------------------------------------------------------------------
  var viewers = {};
  function register(kind, factory) { viewers[kind] = factory; }

  function mediaEl(tag, rel) {
    var el = document.createElement(tag);
    el.src = rawUrl(rel);
    if (tag === "video") { el.controls = true; el.preload = "metadata"; }
    if (tag === "audio") el.controls = true;
    return el;
  }
  function resumeMedia(box, state) {
    if (!state || !state.t) return;
    var m = box.querySelector("video, audio");
    if (!m) return;
    m.addEventListener("loadedmetadata", function () {
      try { m.currentTime = state.t; } catch (e) {}
    }, { once: true });
  }

  // 图片：适配 / 原始大小切换
  viewers.image = function (d, box) {
    var bar = document.createElement("div");
    bar.className = "vw-toolbar";
    var fit = document.createElement("button");
    fit.type = "button"; fit.className = "vw-tbtn on"; fit.textContent = "适配";
    var real = document.createElement("button");
    real.type = "button"; real.className = "vw-tbtn"; real.textContent = "原始大小";
    var img = document.createElement("img");
    img.src = rawUrl(d.rel);
    img.alt = d.name;
    function setMode(which) {
      fit.classList.toggle("on", which === "fit");
      real.classList.toggle("on", which === "real");
      img.style.maxWidth = which === "fit" ? "100%" : "none";
      img.style.maxHeight = which === "fit" ? "100%" : "none";
    }
    fit.addEventListener("click", function () { setMode("fit"); });
    real.addEventListener("click", function () { setMode("real"); });
    bar.appendChild(fit); bar.appendChild(real);
    var flex = document.createElement("span");
    flex.className = "vw-flex";
    bar.appendChild(flex);
    var meta = document.createElement("span");
    meta.className = "vw-meta";
    meta.textContent = d.name + " · " + (d.size / 1024).toFixed(1) + " KB";
    bar.appendChild(meta);

    var wrap = document.createElement("div");
    wrap.className = "vw-media vw-image";
    wrap.appendChild(img);
    box.appendChild(bar);
    box.appendChild(wrap);
  };

  // 视频
  viewers.video = function (d, box, state) {
    var wrap = document.createElement("div");
    wrap.className = "vw-media vw-video";
    wrap.appendChild(mediaEl("video", d.rel));
    box.appendChild(wrap);
    resumeMedia(box, state);
  };

  // 音频
  viewers.audio = function (d, box, state) {
    var wrap = document.createElement("div");
    wrap.className = "vw-audio";
    var head = document.createElement("div");
    head.innerHTML = svg("audio", "vw-audio-ico") +
      '<div class="vw-audio-meta"><div class="vw-audio-name">' + esc(d.name) + "</div>" +
      '<div class="vw-audio-sub">' + (d.size / 1024).toFixed(1) + " KB</div></div>";
    wrap.appendChild(head);
    var a = mediaEl("audio", d.rel);
    a.style.width = "100%";
    wrap.appendChild(a);
    box.appendChild(wrap);
    resumeMedia(box, state);
  };

  // PDF（浏览器原生渲染）
  viewers.pdf = function (d, box) {
    var f = document.createElement("iframe");
    f.className = "vw-pdf";
    f.src = rawUrl(d.rel);
    f.setAttribute("title", d.name);
    box.appendChild(f);
  };

  // HTML 沙盒预览三档（WI-19/B）：
  //   纯静态 —— sandbox=""，脚本/表单/弹窗全禁；
  //   脚本开 —— allow-scripts + allow-forms，本域脚本可跑，CDN 仍被 CSP 拦；
  //   完整预览 —— 同上 + 源换 /files/raw-html（短名单 CDN 放行），进档前显式确认。
  // 纵深安全：永远不加 allow-same-origin → 不透明源，够不到父页面
  // DOM/Cookie/存储；不加 allow-top-navigation → 跳不出窗口。
  // 框嵌放行依赖 WI-15 的 frame-ancestors 'self' 豁免；切档重建 iframe
  //（sandbox 运行时修改对已加载文档不完全生效，同 URL 加时间戳防导航优化）。
  var HTML_MODES = ["纯静态", "脚本开", "完整预览"];
  viewers.html = function (d, box) {
    var mode = 0;
    var bar = document.createElement("div");
    bar.className = "vw-toolbar";
    var tgl = document.createElement("button");
    tgl.type = "button"; tgl.className = "vw-tbtn";
    var flex = document.createElement("span");
    flex.className = "vw-flex";
    var meta = document.createElement("span");
    meta.className = "vw-meta";
    meta.textContent = d.name + " · " + (d.size / 1024).toFixed(1) + " KB";
    var open = document.createElement("a");
    open.className = "vw-tbtn";
    open.href = rawHtmlUrl(d.rel);
    open.target = "_blank";
    open.rel = "noopener";
    open.textContent = "新标签页打开";
    open.title = "顶层文档同样被沙盒（无身份），完整渲染";
    bar.appendChild(tgl); bar.appendChild(flex); bar.appendChild(meta); bar.appendChild(open);
    var f = document.createElement("iframe");
    f.className = "vw-html";
    f.setAttribute("title", d.name);
    function paint(bust) {
      f.setAttribute("sandbox", mode === 0 ? "" : "allow-scripts allow-forms");
      var base = mode === 2 ? rawHtmlUrl(d.rel) : rawUrl(d.rel);
      f.src = bust ? base + "&v=" + Date.now() : base;
      tgl.textContent = HTML_MODES[mode];
      tgl.classList.toggle("on", mode !== 0);
      tgl.title = mode === 2 ? "完整预览：已放行公共 CDN，仍在沙盒内" : "点击切换预览模式";
    }
    tgl.addEventListener("click", function () {
      if (mode === 0) { mode = 1; paint(true); }
      else if (mode === 1) {
        if (!window.confirm("完整预览将放行公共 CDN 资源（样式/字体/图表等），" +
            "页面仍在沙盒内与控制台隔离。继续？")) return;
        mode = 2; paint(true);
      }
      else { mode = 0; paint(true); }
    });
    box.appendChild(bar);
    box.appendChild(f);
    paint(false);
  };

  function csrfToken() {
    var cf = document.querySelector(".osfm-upload input[name=_csrf]");
    var meta = document.querySelector('meta[name="csrf"]');
    return cf ? cf.value : (meta ? meta.content : "");
  }
  // 非 htmx 请求的 toast：冒泡到 body 上的 console:toast 监听（app.js）
  function toastMsg(box, message, level) {
    box.dispatchEvent(new CustomEvent("console:toast", {
      bubbles: true, detail: { message: message, level: level || "success" }
    }));
  }
  function postSave(rel, content, mode, name) {
    var body = new URLSearchParams();
    body.append("path", rel);
    body.append("content", content);
    body.append("mode", mode);
    if (name) body.append("name", name);
    return fetch("/files/save", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                 "X-CSRF-Token": csrfToken() },
      body: body.toString()
    }).then(function (r) {
      if (!r.ok) return r.json().catch(function () { return {}; }).then(function (j) {
        throw new Error((j && j.detail) || ("HTTP " + r.status));
      });
      return r.json();
    });
  }

  // 文本 / Markdown / 代码：查看 + 编辑（保存覆盖 / 另存副本）
  viewers.text = function (d, box, state, win) {
    var editing = false;
    function dirty() { return editing && ta.value !== (d.text || ""); }

    var bar = document.createElement("div");
    bar.className = "vw-toolbar";
    var editBtn = document.createElement("button");
    editBtn.type = "button"; editBtn.className = "vw-tbtn"; editBtn.textContent = "编辑";
    var saveBtn = document.createElement("button");
    saveBtn.type = "button"; saveBtn.className = "vw-tbtn"; saveBtn.textContent = "保存";
    saveBtn.style.display = "none";
    var copyBtn = document.createElement("button");
    copyBtn.type = "button"; copyBtn.className = "vw-tbtn"; copyBtn.textContent = "另存副本";
    copyBtn.style.display = "none";
    var flex = document.createElement("span");
    flex.className = "vw-flex";
    var meta = document.createElement("span");
    meta.className = "vw-meta";
    function paintMeta() {
      meta.textContent = d.name + " · " + (d.size / 1024).toFixed(1) + " KB"
        + (editing && dirty() ? " · 未保存" : "");
    }
    bar.appendChild(editBtn); bar.appendChild(saveBtn); bar.appendChild(copyBtn);
    bar.appendChild(flex); bar.appendChild(meta);

    var pre = document.createElement("pre");
    pre.className = "code-view vw-text";
    pre.textContent = d.text || "";
    var ta = document.createElement("textarea");
    ta.className = "vw-edit";
    ta.value = d.text || "";
    ta.style.display = "none";
    ta.setAttribute("aria-label", d.name);
    ta.addEventListener("input", paintMeta);

    function setEditing(on) {
      editing = on;
      pre.style.display = on ? "none" : "";
      ta.style.display = on ? "" : "none";
      saveBtn.style.display = on ? "" : "none";
      copyBtn.style.display = on ? "" : "none";
      editBtn.textContent = on ? "预览" : "编辑";
      if (on) ta.focus();
      else { ta.value = d.text || ""; pre.textContent = d.text || ""; }
      paintMeta();
    }
    editBtn.addEventListener("click", function () {
      if (editing && dirty() && !window.confirm("有未保存的修改，放弃并返回预览？")) return;
      setEditing(!editing);
    });
    saveBtn.addEventListener("click", function () {
      saveBtn.disabled = true;
      postSave(d.rel, ta.value, "overwrite", "").then(function (j) {
        d.text = ta.value;
        try { d.size = new Blob([ta.value]).size; } catch (e) {}
        setEditing(false);
        toastMsg(box, "已保存" + (j && j.rel ? "：" + j.rel : ""));
      }).catch(function (err) {
        toastMsg(box, "保存失败：" + (err && err.message || err), "error");
      }).then(function () { saveBtn.disabled = false; });
    });
    copyBtn.addEventListener("click", function () {
      var dot = d.name.lastIndexOf(".");
      var defName = (dot > 0 ? d.name.slice(0, dot) : d.name) + "-副本"
        + (dot > 0 ? d.name.slice(dot) : "");
      var name = window.prompt("副本文件名（保存在同目录）", defName);
      if (name === null) return;
      copyBtn.disabled = true;
      postSave(d.rel, ta.value, "copy", name).then(function (j) {
        toastMsg(box, "已另存为" + (j && j.rel ? "：" + j.rel : ""));
      }).catch(function (err) {
        toastMsg(box, "另存失败：" + (err && err.message || err), "error");
      }).then(function () { copyBtn.disabled = false; });
    });
    if (win) win.onBeforeClose = function () {
      return !dirty() || window.confirm("有未保存的修改，确定关闭？");
    };

    box.appendChild(bar);
    box.appendChild(pre);
    box.appendChild(ta);
    if (d.truncated) {
      editBtn.disabled = true;
      editBtn.title = "文件过大（预览已截断），只读";
      var note = document.createElement("p");
      note.className = "vw-note";
      note.textContent = "已截断预览（前 512KB），只读：完整内容请下载";
      box.appendChild(note);
    }
    paintMeta();
  };

  // 暂不支持在线打开的类型
  viewers.none = function (d, box) {
    var div = document.createElement("div");
    div.className = "vw-unsupported";
    div.innerHTML =
      svg("file", "vw-uns-ico") +
      "<p><strong>" + esc(d.name) + "</strong></p>" +
      '<p class="vw-uns-hint">此类型暂不支持在线打开</p>' +
      '<a class="btn btn-primary" href="' + rawUrl(d.rel) + '&dl=1">下载</a>';
    box.appendChild(div);
  };

  // ------------------------------------------------------------------
  // 文件入口：拉描述符 → 查注册表开窗
  // ------------------------------------------------------------------
  function openFile(opts) {
    var rel = opts.rel;
    // 同文件单例：已有未关闭窗口 → 还原并聚焦，不重复开窗
    if (rel) {
      var ids = Object.keys(wins);
      for (var i = 0; i < ids.length; i++) {
        var w = wins[ids[i]];
        if (w.spec && w.spec.rel === rel) {
          if (w.min) restore(w.id);
          else focus(w.id);
          return;
        }
      }
    }
    fetch("/files/viewer?path=" + encodeURIComponent(rel),
          { headers: { "Accept": "application/json" } })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (d) {
        if (!d) throw new Error("bad response");
        if (!d.ok || d.kind === "none") {
          // 不可预览（exe 等）：不建窗口、不尝试渲染，toast + 直接下载
          toastMsg(document.body, "此类型不支持在线预览，已开始下载：" + (d.name || rel));
          var a = document.createElement("a");
          a.href = rawUrl(d.rel || rel) + "&dl=1";
          a.setAttribute("download", "");
          document.body.appendChild(a);
          a.click();
          a.remove();
          return;
        }
        // 扁平描述符：viewer 工厂直接读 d.name / d.size / d.text
        open({
          title: d.name, kind: d.kind, rel: d.rel, name: d.name, size: d.size,
          text: d.text, truncated: d.truncated,
          width: d.kind === "video" ? Math.round(window.innerWidth * 0.78) : undefined
        });
      })
      .catch(function (err) {
        window.alert("打开失败：" + (err && err.message || err));
      });
  }

  window.WM = {
    open: open,
    openFile: openFile,
    register: register,
    loadScript: loadScriptOnce,
    viewers: viewers,
    minimize: minimize,
    restore: restore,
    restoreAll: restoreAll,
    closeAll: closeAll,
    fullscreen: toggleFs,
    close: closeWin,
    // 测试/诊断用
    state: function () {
      return Object.keys(wins).map(function (id) {
        var w = wins[id];
        return { id: id, title: w.title, kind: w.kind, min: w.min, fs: w.fs,
                 content: w.contentEl.children.length };
      });
    }
  };
})();

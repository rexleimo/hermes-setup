// ============================================================================
// WebOS 窗口管理器（通用层）+ 文件 Viewer 注册表
//
// 设计（面向扩展）：
//   1. WM 核心只管窗口生命周期：打开 / 拖拽 / 置顶 / 最小化 / 全屏 / 关闭，
//      与"文件"无关——任何页面都可以 WM.open(spec) 开一个窗口。
//   2. 内容由 Viewer 注册表构建：WM.register(kind, factory)。
//      新增文件类型 = 后端 preview_class 加分支 + 这里 register 一个条目。
//   3. 内存语义（硬要求）：
//      - 关闭：窗口框架 + 内容 DOM 全部销毁；
//      - 最小化：内容 DOM 直接 remove（释放 <video> 解码器 / <img> 解码内存），
//        只在 wins 里留一个"指针"描述符（含媒体播放进度），任务条显示条目；
//      - 还原：按指针重建内容（恢复播放进度）；
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
                   pdf: "document", text: "code", none: "file" };

  function svg(name, cls) {
    return '<svg class="' + (cls || "wm-ico") + '" viewBox="0 0 24 24" aria-hidden="true">' +
           (ICO[name] || ICO.file) + "</svg>";
  }
  function rawUrl(rel) { return "/files/raw?path=" + encodeURIComponent(rel); }

  // ------------------------------------------------------------------
  // 根容器 / 任务条（跨 hx-boost 换页存活）
  // ------------------------------------------------------------------
  function ensureRoot() {
    if (root && document.body.contains(root)) return root;
    if (!root) {
      root = document.createElement("div");
      root.id = "wm-root";
      taskbar = document.createElement("div");
      taskbar.id = "wm-taskbar";
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
    taskbar.appendChild(b);
    return b;
  }
  function taskRefresh() {
    ensureRoot();
    taskbar.style.display = Object.keys(wins).length ? "" : "none";
    Object.keys(wins).forEach(function (id) {
      var w = wins[id];
      if (!w.chip) taskChip(w);
      w.chip.classList.toggle("active", focusedId === id && !w.min);
    });
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
      '<button type="button" class="wm-btn wm-min" title="最小化（销毁内容，任务条保留）">' +
        '<svg viewBox="0 0 16 16"><path d="M3 8.5h10" stroke="currentColor" stroke-width="1.4"/></svg></button>' +
      '<button type="button" class="wm-btn wm-fs" title="全屏">' +
        '<svg viewBox="0 0 16 16"><path d="M3 6V3h3M13 6V3h-3M3 10v3h3M13 10v3h-3" fill="none" stroke="currentColor" stroke-width="1.4"/></svg></button>' +
      '<button type="button" class="wm-btn wm-close" title="关闭（销毁窗口）">' +
        '<svg viewBox="0 0 16 16"><path d="m4 4 8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.4"/></svg></button>';

    var content = document.createElement("div");
    content.className = "wm-content";

    el.appendChild(bar);
    el.appendChild(content);
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
      function mv(e2) {
        el.style.left = Math.min(Math.max(ox + e2.clientX - sx, -w + 120), vw - 120) + "px";
        el.style.top = Math.min(Math.max(oy + e2.clientY - sy, 0), vh - 60) + "px";
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
  function buildContent(win) {
    win.contentEl.innerHTML = "";
    var factory = viewers[win.spec.kind] || viewers.none;
    try {
      factory(win.spec, win.contentEl, win.state || {});
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
    win.state = captureState(win);
    destroyContent(win);
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
    buildContent(win);
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

  // 文本 / Markdown / 代码
  viewers.text = function (d, box) {
    var pre = document.createElement("pre");
    pre.className = "code-view vw-text";
    pre.textContent = d.text || "";
    box.appendChild(pre);
    if (d.truncated) {
      var note = document.createElement("p");
      note.className = "vw-note";
      note.textContent = "已截断预览（前 512KB），完整内容请下载";
      box.appendChild(note);
    }
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
    fetch("/files/viewer?path=" + encodeURIComponent(rel),
          { headers: { "Accept": "application/json" } })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (d) {
        if (!d || !d.ok) throw new Error("bad response");
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
    viewers: viewers,
    minimize: minimize,
    restore: restore,
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

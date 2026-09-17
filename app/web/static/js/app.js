/* Hermes Console 前端交互（无框架，配合 HTMX） */
(function () {
  "use strict";

  // ------------------------------------------------------------------
  // Toast
  // ------------------------------------------------------------------
  var stack = document.getElementById("toast-stack");

  var ICONS = {
    success: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>',
    error: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="m15 9-6 6M9 9l6 6"/></svg>',
    warning: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
    info: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg>'
  };

  function showToast(message, level) {
    level = level || "success";
    var el = document.createElement("div");
    el.className = "toast toast-" + level;
    el.innerHTML = (ICONS[level] || ICONS.info) + "<span></span>";
    el.querySelector("span").textContent = message;
    stack.appendChild(el);
    setTimeout(function () {
      el.classList.add("leaving");
      setTimeout(function () { el.remove(); }, 260);
    }, 4200);
  }

  document.body.addEventListener("console:toast", function (e) {
    var d = e.detail || {};
    showToast(d.message || "操作完成", d.level);
  });

  // ------------------------------------------------------------------
  // 任务日志自动滚到底（安装 / 更新进度实时可见）
  // ------------------------------------------------------------------
  document.body.addEventListener("htmx:afterSwap", function () {
    var log = document.getElementById("job-log");
    if (log) log.scrollTop = log.scrollHeight;
  });

  // ------------------------------------------------------------------
  // 任务日志 SSE 实时推送（新行即时上屏；替代轮询）
  // ------------------------------------------------------------------
  (function initJobStream() {
    var logEl = document.getElementById("job-log");
    if (!logEl || logEl.dataset.stream !== "1" || !window.EventSource) return;
    var stick = true;
    logEl.addEventListener("scroll", function () {
      stick = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 40;
    });
    function appendLines(lines) {
      var empty = logEl.querySelector(".log-empty");
      if (empty) empty.remove();
      for (var i = 0; i < lines.length; i++) {
        var div = document.createElement("div");
        div.className = "log-line";
        div.textContent = lines[i];
        logEl.appendChild(div);
      }
      if (stick) logEl.scrollTop = logEl.scrollHeight;
    }
    var es = new EventSource("/service/job/stream");
    es.onmessage = function (e) {
      var d;
      try { d = JSON.parse(e.data); } catch (err) { return; }
      if (d.type === "lines") {
        appendLines(d.lines || []);
      } else if (d.type === "job") {
        var title = document.getElementById("job-title");
        if (title && d.label) title.textContent = d.label;
      } else if (d.type === "done") {
        es.close();
        var tag = document.getElementById("job-status-tag");
        if (tag) {
          tag.className = "tag " + (d.status === "ok" ? "tag-ok" : "tag-error");
          tag.textContent = d.status === "ok" ? "成功" : ("失败 (exit " + d.exit_code + ")");
        }
        var cancelWrap = document.getElementById("job-cancel-wrap");
        if (cancelWrap) cancelWrap.remove();
      }
    };
    es.onerror = function () { es.close(); };
  })();

  // ------------------------------------------------------------------
  // 确认弹窗（data-confirm：纯文案确认；data-confirm-word：输词确认）
  // ------------------------------------------------------------------
  var modal = document.getElementById("confirm-modal");
  var pendingForm = null;
  var confirmWord = "";

  function openConfirm(opts) {
    document.getElementById("confirm-message").textContent = opts.message;
    document.getElementById("confirm-accept").className =
      "btn " + (opts.danger === false ? "btn-primary" : "btn-danger");
    var formWrap = document.getElementById("confirm-form");
    var input = document.getElementById("confirm-input");
    confirmWord = opts.word || "";
    if (confirmWord) {
      formWrap.classList.remove("hidden");
      document.getElementById("confirm-word").textContent = confirmWord;
      input.value = "";
    } else {
      formWrap.classList.add("hidden");
    }
    pendingForm = opts.submit;
    modal.showModal();
    if (confirmWord) input.focus();
  }

  document.getElementById("confirm-accept").addEventListener("click", function () {
    var typed = "";
    if (confirmWord) {
      typed = document.getElementById("confirm-input").value.trim();
      if (typed !== confirmWord) {
        showToast("确认词不匹配，未执行操作", "warning");
        return;
      }
    }
    modal.close();
    if (pendingForm) {
      var field = pendingForm.querySelector("[data-confirm-field]");
      if (field) field.value = typed;
      pendingForm();
    }
  });

  // 拦截带 data-confirm 的表单与按钮
  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!(form instanceof HTMLFormElement) || form.dataset.confirm === undefined) return;
    if (form.dataset.confirmed === "1") { delete form.dataset.confirmed; return; }
    e.preventDefault();
    openConfirm({
      message: form.dataset.confirm,
      word: form.dataset.confirmWord || "",
      submit: function () {
        form.dataset.confirmed = "1";
        if (form.requestSubmit) form.requestSubmit();
        else form.submit();
      }
    });
  }, true);

  // ------------------------------------------------------------------
  // 弹窗开关（data-modal-open / data-modal-close）
  // ------------------------------------------------------------------
  document.addEventListener("click", function (e) {
    var opener = e.target.closest("[data-modal-open]");
    if (opener) {
      var target = document.querySelector(opener.dataset.modalOpen);
      if (target && target.showModal) target.showModal();
      return;
    }
    if (e.target.closest("[data-modal-close]")) {
      var dlg = e.target.closest("dialog");
      if (dlg) dlg.close();
    }
  });

  // 点击 backdrop 关闭
  document.querySelectorAll("dialog.modal").forEach(function (dlg) {
    dlg.addEventListener("click", function (e) {
      if (e.target === dlg) dlg.close();
    });
  });

  // ------------------------------------------------------------------
  // 复制按钮 [data-copy]
  // ------------------------------------------------------------------
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-copy]");
    if (!btn) return;
    var text = btn.getAttribute("data-copy") || "";
    var done = function () { showToast("已复制到剪贴板"); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done);
    } else {
      var ta = document.createElement("textarea");
      ta.value = text; document.body.appendChild(ta);
      ta.select(); document.execCommand("copy"); ta.remove(); done();
    }
  });

  // ------------------------------------------------------------------
  // 侧边栏（窄屏抽屉）
  // ------------------------------------------------------------------
  var sidebar = document.getElementById("sidebar");
  var toggleBtn = document.querySelector("[data-sidebar-toggle]");
  if (toggleBtn && sidebar) {
    toggleBtn.addEventListener("click", function () {
      var open = sidebar.classList.toggle("open");
      document.body.classList.toggle("drawer-open", open);
    });
    document.addEventListener("click", function (e) {
      if (!sidebar.classList.contains("open")) return;
      if (e.target.closest("[data-drawer-close]") ||
          (!sidebar.contains(e.target) && !toggleBtn.contains(e.target))) {
        sidebar.classList.remove("open");
        document.body.classList.remove("drawer-open");
      }
    });
    // 抽屉里点了导航项后自动收起
    sidebar.querySelectorAll("a.nav-item").forEach(function (a) {
      a.addEventListener("click", function () {
        sidebar.classList.remove("open");
        document.body.classList.remove("drawer-open");
      });
    });
  }

  // ------------------------------------------------------------------
  // 页内 Tab（data-tab-group 容器 + data-tab 按钮 + data-tab-panel 面板）
  // 支持 location.hash 深链
  // ------------------------------------------------------------------
  function activateTab(group, name) {
    var names = Array.prototype.map.call(
      group.querySelectorAll("[data-tab]"), function (b) { return b.dataset.tab; });
    group.querySelectorAll("[data-tab]").forEach(function (b) {
      b.classList.toggle("active", b.dataset.tab === name);
    });
    // 面板以 Tab 名标记，可以在组容器之外的任意位置
    document.querySelectorAll("[data-tab-panel]").forEach(function (p) {
      if (names.indexOf(p.dataset.tabPanel) === -1) return; // 不属于本组
      p.classList.toggle("hidden", p.dataset.tabPanel !== name);
    });
  }

  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-tab]");
    if (!btn) return;
    var group = btn.closest("[data-tab-group]");
    if (!group) return;
    activateTab(group, btn.dataset.tab);
    if (btn.tagName === "BUTTON") {
      history.replaceState(null, "", "#" + btn.dataset.tab);
    }
  });

  // 载入时按 hash 激活
  document.querySelectorAll("[data-tab-group]").forEach(function (group) {
    var hash = location.hash.replace("#", "");
    if (hash && group.querySelector('[data-tab="' + hash + '"]')) {
      activateTab(group, hash);
    }
  });

  // ------------------------------------------------------------------
  // 两步引导（新建供应商等）：data-wizard 容器 + data-wizard-step 面板
  // 可选 data-wizard-form="name"：切换到某步时同步切换对应表单
  // ------------------------------------------------------------------
  function showWizardForm(name) {
    document.querySelectorAll("[data-wizard] .wizard-form").forEach(function (f) {
      f.classList.toggle("hidden", f.dataset.wizardFormName !== name);
    });
  }
  window.showWizardForm = showWizardForm;

  document.addEventListener("click", function (e) {
    var goto = e.target.closest("[data-wizard-goto]");
    if (!goto) return;
    e.preventDefault();
    var wizard = document.querySelector(goto.dataset.wizardGoto);
    if (!wizard) return;
    var step = goto.dataset.wizardStep;
    wizard.querySelectorAll("[data-wizard-step]").forEach(function (p) {
      p.classList.toggle("hidden", p.dataset.wizardStep !== step);
    });
    wizard.querySelectorAll(".step").forEach(function (s) {
      var n = s.dataset.stepNum;
      s.classList.toggle("active", n === step);
      s.classList.toggle("done", parseInt(n, 10) < parseInt(step, 10));
    });
    if (goto.dataset.wizardForm) showWizardForm(goto.dataset.wizardForm);
    wizard.scrollIntoView({ behavior: "smooth", block: "start" });
  });

  // 错误回显：向导带 data-wizard-init-form 时直接落到对应步与表单
  document.querySelectorAll("[data-wizard][data-wizard-init-form]").forEach(function (wizard) {
    var form = wizard.dataset.wizardInitForm;
    if (!form) return;
    var btn = wizard.querySelector('[data-wizard-form="' + form + '"]');
    if (btn) btn.click();
  });

  // ------------------------------------------------------------------
  // 条件字段：data-visible-if-field / data-visible-if-value
  // ------------------------------------------------------------------
  function applyConditionalFields() {
    document.querySelectorAll("[data-visible-if-field]").forEach(function (f) {
      var src = document.querySelector('[name="' + f.dataset.visibleIfField + '"]');
      if (!src) return;
      var item = src.closest(".form-item");
      var srcHidden = item && item.classList.contains("hidden");
      f.classList.toggle("hidden",
        srcHidden || src.value !== f.dataset.visibleIfValue);
    });
  }
  document.addEventListener("change", function (e) {
    if (e.target.name && document.querySelector(
        '[data-visible-if-field="' + e.target.name + '"]')) {
      applyConditionalFields();
    }
  });
  applyConditionalFields();

  // ------------------------------------------------------------------
  // 批量赋值按钮：data-set='{"#fieldId": "value", ...}'（如容量预设）
  // ------------------------------------------------------------------
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-set]");
    if (!btn) return;
    try {
      var mapping = JSON.parse(btn.dataset.set);
      Object.keys(mapping).forEach(function (sel) {
        var el = document.querySelector(sel);
        if (el) el.value = mapping[sel];
      });
    } catch (err) { /* 非法 JSON 忽略 */ }
  });

  // ------------------------------------------------------------------
  // 提示输入后提交表单：data-prompt-submit="#formId"
  // 表单内以 data-prompt-field 标记接收输入的字段
  // ------------------------------------------------------------------
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-prompt-submit]");
    if (!btn) return;
    var form = document.querySelector(btn.dataset.promptSubmit);
    if (!form) return;
    var field = form.querySelector("[data-prompt-field]");
    var input = window.prompt(btn.dataset.promptMessage || "请输入：", "");
    if (input === null) return;
    if (field) field.value = input;
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  });

  // ------------------------------------------------------------------
  // 杂项：details 下拉点外部收起；日志区滚到底部
  // ------------------------------------------------------------------
  document.addEventListener("click", function (e) {
    document.querySelectorAll("details.user-menu[open]").forEach(function (d) {
      if (!d.contains(e.target)) d.removeAttribute("open");
    });
  });

  document.addEventListener("htmx:afterSwap", function (e) {
    var logs = e.target && e.target.querySelectorAll ? e.target.querySelectorAll(".log-view") : [];
    logs.forEach(function (lv) { lv.scrollTop = lv.scrollHeight; });
    var single = e.target && e.target.classList && e.target.classList.contains("log-view")
      ? e.target : null;
    if (single) single.scrollTop = single.scrollHeight;
    // HTMX 换入的动态面板（表单/结果）滚动到可视区，避免"点了没反应"的错觉
    var scrollables = ["provider-config", "fetch-result", "test-result", "probe-result",
                       "job-panel"];
    if (e.target && scrollables.indexOf(e.target.id) !== -1) {
      e.target.scrollIntoView({ behavior: "smooth", block: "start" });
    }
    applyConditionalFields();
  });
})();

  // ------------------------------------------------------------------
  // 文件工作台（Windows 11 资源管理器风格）
  // 选择 / 详细信息面板 / 状态栏 / 双击 / 右键菜单 / 搜索 / 上传即传
  // 事件全部委托在 document 上，hx-boost 换 body 后依然有效。
  // ------------------------------------------------------------------
  (function () {
  "use strict";
  var sel = null, wbDefault = null;

  function $id(x) { return document.getElementById(x); }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function wbItem(ev) { return ev.target.closest(".osfm-card-item"); }
  function wbClearSel() {
    var old = document.querySelectorAll(".osfm-card-item.selected");
    for (var i = 0; i < old.length; i++) old[i].classList.remove("selected");
  }
  function wbStatus() {
    var c = $id("e-status-count"), s = $id("e-status-sel");
    if (c) c.textContent = document.querySelectorAll("#osfm-items .osfm-card-item").length + " 个项目";
    if (s) s.textContent = sel ? "选中 1 个项目  " + sel.dataset.size : "";
  }
  function wbCmdbar(item) {
    var op = $id("osfm-act-open"), arc = $id("osfm-act-archive");
    var dl = $id("osfm-act-dl"), zip = $id("osfm-act-zip");
    if (op) op.disabled = !item;
    if (arc) arc.disabled = !(item && item.dataset.bucket !== "1");
    var isDir = item && item.dataset.dir === "1";
    if (dl) { dl.style.display = item && !isDir ? "" : "none";
      if (item) dl.href = "/files/raw?path=" + encodeURIComponent(item.dataset.rel) + "&dl=1"; }
    if (zip) { zip.style.display = item && isDir ? "" : "none";
      if (item) zip.href = "/files/zip?path=" + encodeURIComponent(item.dataset.rel); }
  }
  // 与 macros.html wbicon 同源的内联 SVG（跨平台一致，不用 emoji 字体）
  var WB_SVG = {
    folder: '<path fill="#e3a92f" d="M2.5 5.5c0-1.1.9-2 2-2h4.6c.6 0 1.2.3 1.5.8l1 1.4h6.9c1.1 0 2 .9 2 2v1.3H2.5z"/><path fill="#f7cf6a" d="M2.5 8.5h19V18c0 1.1-.9 2-2 2H4.5c-1.1 0-2-.9-2-2z"/>',
    image: '<rect x="3" y="5" width="18" height="14" rx="2" fill="#c7e5f8"/><circle cx="8.5" cy="10" r="1.8" fill="#f0a92e"/><path d="M4.5 17.5 10 12l3.3 3.3L16 12.5l3.5 5z" fill="#3aa061"/>',
    video: '<rect x="3" y="5" width="18" height="14" rx="2" fill="#ddd0f5"/><path d="M10 9v6l5-3z" fill="#6d3fc0"/>',
    audio: '<path d="M9.2 17.5V6.8L18.5 5v10.7" fill="none" stroke="#d6538f" stroke-width="1.8" stroke-linejoin="round"/><circle cx="6.7" cy="17.5" r="2.6" fill="#f2a6c8"/><circle cx="16" cy="15.7" r="2.6" fill="#f2a6c8"/>',
    document: '<path fill="#d3e3f8" d="M6 2h8l5 5v13a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z"/><path fill="#9dbde3" d="M14 2l5 5h-5z"/><rect x="7" y="11" width="9.5" height="1.6" rx=".8" fill="#3b6fb5"/><rect x="7" y="14.4" width="9.5" height="1.6" rx=".8" fill="#3b6fb5"/><rect x="7" y="17.8" width="6" height="1.6" rx=".8" fill="#3b6fb5"/>',
    code: '<path fill="#f9e7c4" d="M6 2h8l5 5v13a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z"/><path fill="#e3bd7d" d="M14 2l5 5h-5z"/><path d="m10.2 12-2.2 2.2 2.2 2.2M13.8 12l2.2 2.2-2.2 2.2" fill="none" stroke="#b57d2a" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>',
    archive: '<rect x="3" y="8" width="18" height="12" rx="2" fill="#eccf98"/><rect x="3" y="4.5" width="18" height="4.5" rx="1.5" fill="#b5854a"/><rect x="10.4" y="8.5" width="3.2" height="5.5" rx=".8" fill="#7d5a2e"/>',
    file: '<path fill="#e3e6ea" d="M6 2h8l5 5v13a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z"/><path fill="#c3c9d1" d="M14 2l5 5h-5z"/>'
  };
  function wbSvg(kind, cls) {
    return '<svg class="wb-i ' + (cls || "") + '" viewBox="0 0 24 24" aria-hidden="true">'
      + (WB_SVG[kind] || WB_SVG.file) + "</svg>";
  }
  function wbDetails(item) {
    var pane = $id("wb-preview");
    if (!pane) return;
    if (wbDefault === null) wbDefault = pane.innerHTML;
    if (!item) { pane.innerHTML = wbDefault; wbStatus(); return; }
    var d = item.dataset, root = $id("osfm-root");
    var thumb;
    if (d.dir === "1") thumb = wbSvg("folder", "is-file");
    else if (d.cat === "images") thumb = '<img src="/files/raw?path=' + encodeURIComponent(d.rel) + '" alt="">';
    else if (d.cat === "videos") thumb = '<video src="/files/raw?path=' + encodeURIComponent(d.rel) + '#t=0.5" preload="metadata" muted playsinline></video>';
    else thumb = wbSvg(d.cat || "file", "is-file");
    var acts = '<div class="e-dacts"><button type="button" class="e-dbtn" data-wb="open">打开</button>'
      + (d.dir === "0"
          ? '<a class="e-dbtn" href="/files/raw?path=' + encodeURIComponent(d.rel) + '&dl=1">下载</a>'
          : '<a class="e-dbtn" href="/files/zip?path=' + encodeURIComponent(d.rel) + '">打包</a>')
      + (root && root.dataset.admin === "1" && d.bucket !== "1"
          ? '<button type="button" class="e-dbtn" data-wb="archive">归档</button>' : "")
      + "</div>";
    pane.innerHTML = '<div class="e-dthumb">' + thumb + "</div>"
      + '<div class="e-dname">' + esc(d.name0) + "</div>" + acts
      + '<div class="e-dtitle">详细信息</div><dl class="e-dl">'
      + "<div><dt>类型</dt><dd>" + esc(d.type) + "</dd></div>"
      + "<div><dt>大小</dt><dd>" + esc(d.size) + "</dd></div>"
      + "<div><dt>文件位置</dt><dd>" + esc(d.parent) + "</dd></div>"
      + "<div><dt>修改日期</dt><dd>" + esc(d.mtime) + "</dd></div></dl>";
    wbStatus();
  }
  function wbPreviewContent(rel) {
    if (window.htmx) {
      htmx.ajax("GET", "/files/preview?path=" + encodeURIComponent(rel),
                { target: "#wb-preview", swap: "innerHTML" });
    }
  }
  function wbOpen(item) {
    if (item.dataset.dir === "1") window.location.href = item.dataset.href;
    else wbPreviewContent(item.dataset.rel);
  }
  function wbArchive(rel) {
    if (!window.confirm("归档 " + rel + " → archive/？（移动，不留副本）")) return;
    var meta = document.querySelector('meta[name="csrf"]');
    var fd = new FormData();
    fd.append("path", rel);
    fetch("/files/move", { method: "POST",
      headers: { "X-CSRF-Token": meta ? meta.content : "" }, body: fd })
      .then(function (r) { if (!r.ok) throw new Error(r.status); window.location.reload(); })
      .catch(function () { window.alert("归档失败"); });
  }
  function wbSelect(item) {
    wbClearSel(); sel = item;
    if (item) { item.classList.add("selected"); item.focus({ preventScroll: true }); }
    wbCmdbar(item); wbDetails(item);
  }

  document.addEventListener("click", function (ev) {
    if (wbMenu && !ev.target.closest(".osfm-menu")) wbHideMenu();
    var act = ev.target.closest && ev.target.closest("[id^='osfm-act-'],[data-wb]");
    if (act) {
      if ((act.id === "osfm-act-open" || act.dataset.wb === "open") && sel) { wbOpen(sel); return; }
      if ((act.id === "osfm-act-archive" || act.dataset.wb === "archive") && sel) { wbArchive(sel.dataset.rel); return; }
    }
    if (ev.target.closest && ev.target.closest("#e-back")) { history.back(); return; }
    if (ev.target.closest && ev.target.closest("#e-fwd")) { history.forward(); return; }
    if (ev.target.closest && ev.target.closest("#e-details-toggle")) {
      var root = $id("osfm-root"); if (root) root.classList.toggle("e-nodetails"); return;
    }
    var item = wbItem(ev);
    if (!item) { if (ev.target.closest && !ev.target.closest(".e-side,.e-menu,.osfm-upload,input")) wbSelect(null); return; }
    wbSelect(item);
  });
  document.addEventListener("dblclick", function (ev) {
    var item = wbItem(ev);
    if (!item) return;
    wbOpen(item);
  });

  // 右键上下文菜单
  var wbMenu = null;
  function wbHideMenu() { if (wbMenu) wbMenu.style.display = "none"; }
  function wbMenuAdd(label, fn) {
    var b = document.createElement("button");
    b.type = "button"; b.textContent = label;
    b.addEventListener("click", function () { wbHideMenu(); fn(); });
    wbMenu.appendChild(b);
  }
  document.addEventListener("contextmenu", function (ev) {
    var item = wbItem(ev);
    if (!item) return;
    ev.preventDefault();
    wbSelect(item);
    if (!wbMenu) { wbMenu = document.createElement("div"); wbMenu.className = "osfm-menu"; document.body.appendChild(wbMenu); }
    wbMenu.innerHTML = "";
    wbMenuAdd("打开", function () { wbOpen(item); });
    if (item.dataset.dir === "0")
      wbMenuAdd("下载", function () { location.href = "/files/raw?path=" + encodeURIComponent(item.dataset.rel) + "&dl=1"; });
    if (item.dataset.dir === "1")
      wbMenuAdd("打包下载（zip）", function () { location.href = "/files/zip?path=" + encodeURIComponent(item.dataset.rel); });
    var root = $id("osfm-root");
    if (root && root.dataset.admin === "1" && item.dataset.bucket !== "1")
      wbMenuAdd("归档到 archive/", function () { wbArchive(item.dataset.rel); });
    wbMenu.style.display = "block";
    wbMenu.style.left = Math.min(ev.clientX, window.innerWidth - wbMenu.offsetWidth - 8) + "px";
    wbMenu.style.top = Math.min(ev.clientY, window.innerHeight - wbMenu.offsetHeight - 8) + "px";
  });

  // 键盘：Esc 取消 / Enter 打开 / Backspace 上一级
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape") { wbHideMenu(); wbSelect(null); return; }
    var tag = (document.activeElement || {}).tagName || "";
    if (/INPUT|TEXTAREA|SELECT/i.test(tag)) return;
    if (ev.key === "Enter" && sel) { wbOpen(sel); return; }
    if (ev.key === "Backspace") {
      var up = document.querySelector(".e-nav a.e-navbtn");
      if (up) { ev.preventDefault(); window.location.href = up.getAttribute("href"); }
    }
  });
  // 搜索：即时过滤当前视图
  document.addEventListener("input", function (ev) {
    if (!ev.target || ev.target.id !== "osfm-search") return;
    var q = ev.target.value.toLowerCase();
    var items = document.querySelectorAll(".osfm-card-item");
    for (var i = 0; i < items.length; i++) {
      items[i].style.display = items[i].dataset.name.indexOf(q) >= 0 ? "" : "none";
    }
  });
  // 选完文件即上传
  document.addEventListener("change", function (ev) {
    if (!ev.target || !ev.target.matches || !ev.target.matches(".osfm-upload input[type=file]")) return;
    if (ev.target.files && ev.target.files.length) ev.target.closest("form").submit();
  });
  // 初次渲染：状态栏
  document.addEventListener("DOMContentLoaded", wbStatus);
  if (document.readyState !== "loading") wbStatus();
  })();

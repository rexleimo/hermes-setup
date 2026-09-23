/* Hermes Console 前端交互（无框架，配合 HTMX） */
(function () {
  "use strict";

  // ------------------------------------------------------------------
  // Toast
  // ------------------------------------------------------------------
  var ICONS = {
    success: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>',
    error: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="m15 9-6 6M9 9l6 6"/></svg>',
    warning: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
    info: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg>'
  };

  // toast 队列：boosted 整页换页会换掉 #toast-stack 的 DOM，
  // 换页后把被毁掉的 toast 在新页面上重放（片段换页不动 #toast-stack，不会重复弹）。
  var toastQueue = [];
  function toastStack() {
    return document.getElementById("toast-stack");
  }
  function showToast(message, level) {
    level = level || "success";
    var stack = toastStack();
    if (!stack) return;
    var el = document.createElement("div");
    el.className = "toast toast-" + level;
    el.innerHTML = (ICONS[level] || ICONS.info) + "<span></span>";
    el.querySelector("span").textContent = message;
    stack.appendChild(el);
    toastQueue.push({ message: message, level: level, el: el, at: Date.now() });
    setTimeout(function () {
      el.classList.add("leaving");
      setTimeout(function () { el.remove(); }, 260);
    }, 4200);
  }

  document.body.addEventListener("console:toast", function (e) {
    var d = e.detail || {};
    showToast(d.message || "操作完成", d.level);
  });

  document.body.addEventListener("htmx:afterSwap", function () {
    var now = Date.now();
    toastQueue = toastQueue.filter(function (t) {
      var alive = !!(t.el && t.el.isConnected);
      if (!alive && now - t.at < 4000) showToast(t.message, t.level);
      return alive;
    });
  });

  // 服务端拒绝（400/403/404）时 htmx 默认什么都不换——不补这条 toast，
  // 重命名撞名 / 上传超限 / 删除保护目录这类失败就是"点了没反应"（实机观感）。
  document.body.addEventListener("htmx:responseError", function (ev) {
    var xhr = (ev.detail && ev.detail.xhr) || {};
    var msg = "";
    try {
      var data = JSON.parse(xhr.responseText || "");
      if (data && data.detail) msg = String(data.detail);
    } catch (e) { /* 非 JSON（如 CSRF 的 HTML 提示）走通用文案 */ }
    showToast(msg || "操作失败（HTTP " + (xhr.status || "?") + "）", "error");
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
  // 任务面板是可被 HTMX 换入的片段：每次 afterSwap 都重新探测并挂载，
  // 否则动作按钮局部提交后，新面板的实时日志会静默丢失（只看到首屏快照）。
  // ------------------------------------------------------------------
  var jobEs = null;
  var jobLogEl = null;
  function attachJobStream() {
    var logEl = document.getElementById("job-log");
    if (!logEl || logEl.dataset.stream !== "1" || !window.EventSource) return;
    if (jobLogEl === logEl && jobEs) return;   // 同一片段已挂载，不重复开流
    if (jobEs) { jobEs.close(); jobEs = null; }
    jobLogEl = logEl;
    var stick = true;
    logEl.addEventListener("scroll", function () {
      stick = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 40;
    });
    var firstLines = true;
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
    jobEs = es;
    es.onmessage = function (e) {
      var d;
      try { d = JSON.parse(e.data); } catch (err) { return; }
      if (d.type === "lines") {
        if (firstLines) {
          // SSE 从位移 0 推送；首屏已由服务端渲染过同一批行——首帧先清空再追加，避免重复
          logEl.innerHTML = "";
          firstLines = false;
        }
        appendLines(d.lines || []);
      } else if (d.type === "job") {
        var title = document.getElementById("job-title");
        if (title && d.label) title.textContent = d.label;
      } else if (d.type === "done") {
        es.close();
        jobEs = null; jobLogEl = null;   // 允许后续重新挂载（新任务 / 新面板）
        var tag = document.getElementById("job-status-tag");
        if (tag) {
          tag.className = "tag " + (d.status === "ok" ? "tag-ok" : "tag-error");
          tag.textContent = d.status === "ok" ? "成功" : ("失败 (exit " + d.exit_code + ")");
        }
        var cancelWrap = document.getElementById("job-cancel-wrap");
        if (cancelWrap) cancelWrap.remove();
      }
    };
    es.onerror = function () { es.close(); jobEs = null; jobLogEl = null; };
  }
  attachJobStream();
  document.body.addEventListener("htmx:afterSwap", attachJobStream);

  // ------------------------------------------------------------------
  // 确认弹窗（data-confirm：纯文案确认；data-confirm-word：输词确认）
  // 元素每次现查：hx-boost 换页会换掉 #confirm-modal 的 DOM，
  // 缓存旧节点会导致换页后弹窗"点了没反应"（0.8.31 侧栏 boost 前必须根治）。
  // ------------------------------------------------------------------
  var pendingForm = null;
  var confirmWord = "";

  function openConfirm(opts) {
    var modal = document.getElementById("confirm-modal");
    if (!modal) return;
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
    if (!modal.open) modal.showModal();   // 双击防抖：已打开时不再 showModal（否则抛错）
    if (confirmWord) input.focus();
  }

  document.addEventListener("click", function (e) {
    if (e.target.closest && e.target.closest("#confirm-accept")) {
      try {
        var typed = "";
        if (confirmWord) {
          typed = document.getElementById("confirm-input").value.trim();
          if (typed !== confirmWord) {
            showToast("确认词不匹配，未执行操作", "warning");
            return;
          }
        }
        var modal = document.getElementById("confirm-modal");
        if (modal) modal.close();
        if (!pendingForm) {
          showToast("未找到待提交的表单，请重试", "warning");
          return;
        }
        pendingForm(typed);
      } catch (err) {
        // 任何异常都必须可见：不再出现"点了没反应"
        showToast("提交失败：" + err, "error");
      }
      return;
    }
    // dialog backdrop 关闭（点在 <dialog> 本体 = 点到 backdrop）
    var dlg = e.target;
    if (dlg instanceof HTMLDialogElement && dlg.classList.contains("modal") && dlg.open) {
      dlg.close();
    }
    var closer = e.target.closest && e.target.closest("[data-modal-close]");
    if (closer) {
      var host = closer.closest("dialog");
      if (host) host.close();
    }
    var opener = e.target.closest && e.target.closest("[data-modal-open]");
    if (opener) {
      var target = document.querySelector(opener.dataset.modalOpen);
      if (target && target.showModal) target.showModal();
    }
  });

  // 拦截带 data-confirm 的表单与按钮
  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!(form instanceof HTMLFormElement) || form.dataset.confirm === undefined) return;
    e.preventDefault();
    openConfirm({
      message: form.dataset.confirm,
      word: form.dataset.confirmWord || "",
      submit: function (typed) {
        // 确认后直接原生提交（form.submit）：不再走 requestSubmit 的二次 submit
        // 事件往返——跨浏览器/时序下偶发的"确认后无反应"由此根除；
        // 确认词就地写入隐藏字段（data-confirm-field），服务端校验用。
        var field = form.querySelector("[data-confirm-field]");
        if (field) field.value = typed || "";
        if (window.htmx && form.hasAttribute("hx-post")) {
          // HTMX 表单：局部提交（响应是任务面板片段 + toast + 状态刷新），
          // 不再整页跳转；form.submit() 不会触发 htmx，必须显式走 htmx.ajax。
          htmx.ajax("POST", form.getAttribute("hx-post"), {
            source: form,
            target: form.getAttribute("hx-target") || "body",
            swap: form.getAttribute("hx-swap") || "innerHTML"
          });
        } else {
          form.submit();
        }
      }
    });
  }, true);

  // ------------------------------------------------------------------
  // 弹窗开关（data-modal-open / data-modal-close）+ dialog backdrop 关闭
  // （并入上方 #confirm-accept 委托处理器；直接绑定经不起 boost 换页）
  // ------------------------------------------------------------------

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
  // 侧边栏（窄屏抽屉）— 事件委托：boost 换页会换掉 #sidebar 的 DOM，
  // 直接绑定只在首屏有效（0.8.31 侧栏开启 hx-boost 的前置加固）。
  // ------------------------------------------------------------------
  document.addEventListener("click", function (e) {
    var toggleHit = e.target.closest && e.target.closest("[data-sidebar-toggle]");
    var sidebar = document.getElementById("sidebar");
    if (toggleHit && sidebar) {
      var open = sidebar.classList.toggle("open");
      document.body.classList.toggle("drawer-open", open);
      return;
    }
    if (!sidebar || !sidebar.classList.contains("open")) return;
    if (e.target.closest("[data-drawer-close]") ||
        (!sidebar.contains(e.target) && !toggleHit)) {
      sidebar.classList.remove("open");
      document.body.classList.remove("drawer-open");
    }
  });

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
    // 文件管理器换页后（boosted 导航 / 归档局部刷新）：清旧选中 + 重算状态栏
    // wbSelect 在文件工作台 IIFE 内，跨作用域经 window.__wbClearSel 调用。
    if (document.getElementById("osfm-items") && window.__wbClearSel) window.__wbClearSel();
    // 「加载更多」已改为窗口化滚动：换页/写操作后由它接管 #osfm-items 重渲染。
    if (window.__wbVirtInit) window.__wbVirtInit();
  });
})();

  // ------------------------------------------------------------------
  // 文件工作台（Windows 11 资源管理器风格）
  // 选择 / 详细信息面板 / 状态栏 / 双击 / 右键菜单 / 搜索 / 上传即传
  // 事件全部委托在 document 上，hx-boost 换 body 后依然有效。
  // ------------------------------------------------------------------
  (function () {
  "use strict";
  var sel = null, wbDefault = null, selMulti = [];

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
    var items = $id("osfm-items");
    var shown = document.querySelectorAll("#osfm-items .osfm-card-item").length;
    var total = items ? parseInt(items.dataset.total || "0", 10) : 0;
    if (c) {
      // 分页后页面上只有部分条目，状态栏说清楚“共多少 / 已显示多少”，
      // 不然用户以为工作区只剩这 120 个文件。
      c.textContent = !total || total === shown
        ? shown + " 个项目"
        : "共 " + total + " 个项目 · 已显示 " + shown;
    }
    if (s) {
      var n = selMulti.length || (sel ? 1 : 0);
      s.textContent = n ? ("选中 " + n + " 个项目" + (n === 1 && sel ? "  " + sel.dataset.size : "")) : "";
    }
  }
  function wbCmdbar(item) {
    var op = $id("osfm-act-open"), arc = $id("osfm-act-archive");
    var dl = $id("osfm-act-dl"), zip = $id("osfm-act-zip");
    var ren = $id("osfm-act-rename"), cp = $id("osfm-act-copy"), del = $id("osfm-act-delete");
    var trash = wbIsTrash(), multi = selMulti.length > 0;
    if (op) op.disabled = !item;
    if (arc) arc.disabled = !(item && item.dataset.bucket !== "1" && !trash);
    // 回收站里只提供「还原」：重命名/复制/再删除都会让回收站 manifest 指错原位置
    var has = item || multi;
    if (ren) ren.disabled = !(has && !multi && !trash);
    if (cp) cp.disabled = !(has && !trash);
    if (del) del.disabled = !(has && !trash);
    var isDir = item && item.dataset.dir === "1";
    if (dl) { dl.style.display = item && !isDir && !trash ? "" : "none";
      if (item) dl.href = "/files/raw?path=" + encodeURIComponent(item.dataset.rel) + "&dl=1"; }
    if (zip) { zip.style.display = item && isDir && !trash ? "" : "none";
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
    // 选中预览也用缩略图：详情栏只有 ~200px，拉 4000×3000 原图是纯浪费；
    // 非栅格图（SVG 等）缩略图端点不服务，回原图。
    else if (d.cat === "images") thumb = '<img src="' + (d.thumbable === "1"
        ? "/files/thumb?path=" + encodeURIComponent(d.rel)
        : "/files/raw?path=" + encodeURIComponent(d.rel)) + '" alt="">';
    else if (d.cat === "videos") thumb = '<video src="/files/raw?path=' + encodeURIComponent(d.rel) + '#t=0.5" preload="metadata" muted playsinline></video>';
    else thumb = wbSvg(d.cat || "file", "is-file");
    var acts = '<div class="e-dacts"><button type="button" class="e-dbtn" data-wb="open">打开</button>'
      + (d.dir === "0"
          ? '<a class="e-dbtn" href="/files/raw?path=' + encodeURIComponent(d.rel) + '&dl=1">下载</a>'
          : '<a class="e-dbtn" href="/files/zip?path=' + encodeURIComponent(d.rel) + '">打包</a>')
      + (root && root.dataset.admin === "1"
          ? (wbIsTrash()
              ? '<button type="button" class="e-dbtn" data-wb="restore">还原</button>'
              : (d.bucket !== "1"
                  ? '<button type="button" class="e-dbtn" data-wb="archive">归档</button>' : ""))
          : "")
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
  function wbNav(href) {
    // boosted 导航：经 #osfm-root（hx-boost 容器）内的 <a> 点击 → HTMX 换页 + 历史记录；
    // HTMX 未加载时退化为整页加载（原生锚点行为）。
    var a = document.createElement("a");
    a.href = href;
    var host = $id("osfm-root") || document.body;
    host.appendChild(a); a.click(); a.remove();
  }
  function wbOpen(item) {
    if (item.dataset.dir === "1") wbNav(item.dataset.href);
    else if (window.WM && WM.openFile) WM.openFile({ rel: item.dataset.rel });
    else wbPreviewContent(item.dataset.rel);   // WM 未加载时回退侧栏预览
  }
  function wbHere() {
    var root = $id("osfm-root");
    return root && root.dataset.here ? String(root.dataset.here).split("?")[1] || "" : "";
  }
  function wbIsTrash() { return /(^|&)cat=trash(&|$)/.test(wbHere()); }
  function wbCsrf() {
    var cf = document.querySelector(".osfm-upload input[name=_csrf]");
    var meta = document.querySelector('meta[name="csrf"]');
    return cf ? cf.value : (meta ? meta.content : "");
  }
  function wbCurrentDir() {
    // 当前目录 rel：here 查询串里的 path 参数（根视图 = ""）
    var m = wbHere().match(/(^|&)path=([^&]*)/);
    return m ? decodeURIComponent(m[2].replace(/\+/g, "%20")) : "";
  }
  function wbPost(url, fields, failMsg) {
    // 局部提交：服务端重渲染当前视图 + 侧栏 OOB 换入，不整页刷新。
    // htmx.ajax 要求 source 已挂载，故挂一个隐藏表单，请求结束后移除；
    // fields 的值是数组时展开为多值字段（批量操作的 paths）。
    var csrf = wbCsrf();
    var meta = document.querySelector('meta[name="csrf"]');
    if (window.htmx) {
      var f = document.createElement("form");
      f.style.display = "none";
      var html = "";
      for (var k in fields) {
        var v = fields[k];
        if (v && typeof v.join === "function") {
          for (var j = 0; j < v.length; j++)
            html += '<input name="' + esc(k) + '" value="' + esc(v[j]) + '">';
        } else {
          html += '<input name="' + esc(k) + '" value="' + esc(v) + '">';
        }
      }
      f.innerHTML = html + '<input name="_csrf" value="' + esc(csrf) + '">';
      document.body.appendChild(f);
      f.addEventListener("htmx:afterRequest", function () { f.remove(); }, { once: true });
      setTimeout(function () { if (f.parentNode) f.remove(); }, 30000);
      htmx.ajax("POST", url, { source: f, target: "#osfm-content", swap: "innerHTML" });
      return;
    }
    var fd = new FormData();
    for (var k2 in fields) {
      var v2 = fields[k2];
      if (v2 && typeof v2.join === "function") { for (var j2 = 0; j2 < v2.length; j2++) fd.append(k2, v2[j2]); }
      else fd.append(k2, v2);
    }
    fd.append("_csrf", csrf);
    fetch(url, { method: "POST",
      headers: { "X-CSRF-Token": meta ? meta.content : "" }, body: fd })
      .then(function (r) { if (!r.ok) throw new Error(r.status); window.location.reload(); })
      .catch(function () { window.alert(failMsg || "操作失败"); });
  }
  function wbArchive(rel) {
    if (!window.confirm("归档 " + rel + " → archive/？（移动，不留副本）")) return;
    wbPost("/files/move", { path: rel, here: wbHere() }, "归档失败");
  }
  function wbRename(item) {
    var name = window.prompt("重命名为：", item.dataset.name0);
    if (!name || name === item.dataset.name0) return;
    wbPost("/files/rename", { path: item.dataset.rel, name: name, here: wbHere() }, "重命名失败");
  }
  function wbSelRels() {
    if (selMulti.length) return selMulti.map(function (el) { return el.dataset.rel; });
    return sel ? [sel.dataset.rel] : [];
  }
  function wbCopy() {
    var rels = wbSelRels();
    if (!rels.length) return;
    wbPost("/files/batch", { op: "copy", paths: rels, here: wbHere() }, "复制失败");
  }
  function wbDelete() {
    var rels = wbSelRels();
    if (!rels.length) return;
    var msg = rels.length === 1
      ? "把 " + rels[0] + " 移入回收站？（可在回收站还原）"
      : "把选中的 " + rels.length + " 项移入回收站？（可在回收站还原）";
    if (!window.confirm(msg)) return;
    wbPost("/files/batch", { op: "delete", paths: rels, here: wbHere() }, "删除失败");
  }
  function wbRestore(item) {
    wbPost("/files/restore", { path: item.dataset.rel, here: wbHere() }, "还原失败");
  }
  function wbNew(kind) {
    var name = window.prompt(kind === "folder" ? "新文件夹名称：" : "新文件名称：", "");
    if (!name) return;
    wbPost("/files/new", { dir: wbCurrentDir(), kind: kind, name: name, here: wbHere() }, "创建失败");
  }
  function wbSelect(item) {
    wbClearSel(); selMulti = []; sel = item;
    if (item) { item.classList.add("selected"); item.focus({ preventScroll: true }); }
    wbCmdbar(item); wbDetails(item);
  }
  function wbToggleSel(item) {
    var idx = selMulti.indexOf(item);
    if (idx >= 0) {
      selMulti.splice(idx, 1);
      item.classList.remove("selected");
      if (sel === item) sel = selMulti[selMulti.length - 1] || null;
    } else {
      selMulti.push(item);
      item.classList.add("selected");
      sel = item;
    }
    if (!selMulti.length && !sel) wbClearSel();
    else if (sel) sel.classList.add("selected");
    wbCmdbar(sel); wbDetails(selMulti.length === 1 ? sel : null);
    if (!sel && !selMulti.length) wbDetails(null);
    wbStatus();
  }
  function wbRangeSel(item) {
    var items = Array.prototype.slice.call(
      document.querySelectorAll("#osfm-items .osfm-card-item"));
    var a = items.indexOf(sel), b = items.indexOf(item);
    if (a < 0 || b < 0) { wbSelect(item); return; }
    if (a > b) { var t = a; a = b; b = t; }
    wbClearSel(); selMulti = items.slice(a, b + 1); sel = item;
    for (var i = 0; i < selMulti.length; i++) selMulti[i].classList.add("selected");
    wbCmdbar(sel); wbDetails(null); wbStatus();
  }
  // 供顶部通用 afterSwap 处理器调用（换页/局部刷新后清掉指向旧 DOM 的选中）
  window.__wbClearSel = function () { selMulti = []; wbSelect(null); };
  // 「加载更多」改成窗口化滚动后，换页/写操作重渲染由它接管 #osfm-items。
  window.__wbVirtInit = wbVirtInit;

  // ------------------------------------------------------------------
  // 窗口化滚动（虚拟化渲染，0.8.x）
  // 「加载更多」无限制追加 DOM：翻几百页就是几千卡片，选中/状态栏每次扫全量，
  // 越翻越卡。改为资源管理器式的窗口化：只渲染可视区附近的一小批页（+ 缓冲），
  // 滚出视口的块丢弃、滚回再取（已加载即免重取）。行高统一（见 .e-fname 单行省略）
  // → 单页高度固定 = 每页条数 × 行高，凭 scrollTop ÷ 页高度就能算出可视区落在哪几页，
  // 无需逐元素测量。块用绝对定位，故翻页/丢块不跳滚动条。网格与列表都启用：
  // 列表的 <table> 不能承载绝对定位块，故换成带粘滞表头的外壳，块内放 e-table。
  var wbVirt = null;

  function wbVirtDestroy() {
    if (!wbVirt) return;
    var c = wbVirt.container;
    if (c) {
      if (wbVirt.scroller) wbVirt.scroller.removeEventListener("scroll", wbVirtOnScroll);
      c.classList.remove("wb-virt");
      c.style.height = "";
    }
    if (wbVirtResizeT) { clearTimeout(wbVirtResizeT); wbVirtResizeT = null; }
    window.removeEventListener("resize", wbVirtOnResize);
    wbVirt = null;
  }

  function wbVirtBlock(page, nodes) {
    var b = document.createElement("div");
    b.className = "wb-pblock";
    b.dataset.page = String(page);
    // 列表块从表头下方开始（offsetTop），网格从容器顶部开始（offsetTop=0）。
    b.style.top = ((wbVirt ? wbVirt.offsetTop + page * wbVirt.blockH : 0)) + "px";
    if (wbVirt && wbVirt.view === "list") {
      // <tr> 不能直接放 div 里：块内放一个 e-table>tbody，卡片 tr 在 tbody 中。
      b.className = "wb-pblock wb-pblock-list";
      var tbl = document.createElement("table");
      tbl.className = "e-table";
      var tb = document.createElement("tbody");
      for (var i = 0; i < nodes.length; i++) tb.appendChild(nodes[i]);
      tbl.appendChild(tb);
      b.appendChild(tbl);
    } else {
      for (var j = 0; j < nodes.length; j++) b.appendChild(nodes[j]);
    }
    return b;
  }

  function wbVirtBaseQs(container) {
    var here = wbHere();
    var p = new URLSearchParams(here || "");
    p.delete("offset");
    p.set("view", container.dataset.view || "grid");
    return p.toString();
  }

  function wbVirtFetchPage(page, qs, done) {
    fetch("/files/more?" + qs + "&offset=" + (page * wbVirt.pageSize))
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.text(); })
      .then(function (html) { done(html.trim() ? html : "") })
      .catch(function () { done(""); });
  }

  function wbVirtPaint() {
    if (!wbVirt) return;
    var W = wbVirt, container = W.container;
    var blockH = W.blockH;
    var lastPage = Math.ceil(W.total / W.pageSize) - 1;
    if (lastPage < 0) lastPage = 0;
    var st = W.scroller.scrollTop, ch = W.scroller.clientHeight;
    var fp = Math.min(lastPage, Math.floor(st / blockH));
    var lp = Math.min(lastPage, Math.floor((st + ch) / blockH));
    var lo = Math.max(0, fp - W.behind), hi = Math.min(lastPage, lp + W.ahead);

    // 把窗口内已加载的块挂回容器（顺序 = 页号序），窗口外（含越界）的块丢弃。
    var keep = new Set();
    for (var p = lo; p <= hi; p++) {
      var n = W.blocks[p];
      if (n && W.loaded.has(p) && !n.parentNode) container.appendChild(n);
      if (n && n.parentNode) keep.add(String(p));   // W.blocks 的 key 是字符串，这里也必须转字符串，否则删除判断（下面 for..in 用字符串 key）永远匹配不上，把窗口内块误删
    }
    for (var k in W.blocks) {
      if (!keep.has(k) && W.blocks[k].parentNode) {
        W.blocks[k].parentNode.removeChild(W.blocks[k]);
        delete W.blocks[k];          // 连内存一起回收，滚回再取 —— 总内存随窗口固定
        W.loaded.delete(k);
      }
    }
    // 每次稳定布局后都激活新挂出卡片的缩略图 + 刷新状态栏（首屏页 0 也立即激活）。
    wbVirtFinished();

    // 缺的块异步去取（窗口很小，拉完再重铺一次）。
    var miss = [];
    for (var m = lo; m <= hi; m++) if (!W.loaded.has(m) && !W.fetching.has(m)) miss.push(m);
    if (!miss.length) { return; }

    var done = 0;
    function all() { if (++done === miss.length) { wbVirtPaint(); } }
    miss.forEach(function (pg) {
      if (pg > lastPage) { W.fetching.add(pg); return all(); }   // 已到末尾页，无更多
      W.fetching.add(pg);
      wbVirtFetchPage(pg, W.qs, function (html) {
        W.fetching.delete(pg);
        if (!html) { W.loaded.add(pg); return all(); }  // 服务器无更多内容：标记为已兑换，不再重复请求（否则 miss 永远命中）
        var frag = document.createRange().createContextualFragment(html);
        var nodes = [];
        var ch2 = frag.childNodes;
        for (var i = 0; i < ch2.length; i++) if (ch2[i].nodeType === 1) nodes.push(ch2[i]);
        if (!nodes.length) { W.loaded.add(pg); return all(); }  // 解析不出卡片：同样记作已兑换
        var b = wbVirtBlock(pg, nodes);
        W.blocks[pg] = b;
        W.loaded.add(pg);
        all();
      });
    });
  }

  function wbVirtFinished() {
    if (!wbVirt) return;
    mediaScan(wbVirt.container);   // 新挂出的卡片才激活 data-src 缩略图
    wbStatus();
  }

  function wbVirtInit() {
    var container = $id("osfm-items");
    if (!container) { wbVirtDestroy(); return; }
    var view = container.dataset.view || "grid";
    if (view !== "grid" && view !== "list") { wbVirtDestroy(); return; }  // 仅网格/列表虚拟化
    if (wbVirt && wbVirt.container === container && wbVirtBaseQs(container) === wbVirt._qs)
      return;                          // 视图未变，不重开
    wbVirtDestroy();

    var total = parseInt(container.dataset.total || "0", 10) || 0;
    if (!total) { wbVirtDestroy(); return; }   // 空视图：交给既有占位文案

    // 清掉「加载更多」按钮与占位行（网格是 div.e-more-slot，列表是 tr.e-more-slot）。
    var more = $id("osfm-more");
    if (more && more.parentNode) more.parentNode.removeChild(more);
    container.querySelectorAll(".e-more-slot").forEach(function (el) { el.remove(); });

    // 抽首屏一页卡片 + 确定承载外壳。
    var shell, page0;
    if (view === "list") {
      var res = wbVirtListShell(container);
      if (!res) { wbVirtDestroy(); return; }
      shell = res.shell; page0 = res.rows;
    } else {
      page0 = [];
      var s = container.querySelectorAll(":scope > .osfm-card-item");
      for (var i = 0; i < s.length; i++) page0.push(s[i]);
      if (!page0.length) { wbVirtDestroy(); return; }
      shell = container;   // 网格容器本身就是 div，直接复用
    }

    var W = {
      container: shell, total: total, pageSize: page0.length, view: view,
      qs: wbVirtBaseQs(shell), _qs: wbVirtBaseQs(shell),
      behind: 1, ahead: 1, blockH: 0, offsetTop: 0,
      blocks: {}, loaded: new Set(), fetching: new Set()
    };

    // 块 0 先挂进外壳以便测量高度（列表外壳已带表头，用 appendChild 而非 replaceChildren）。
    W.blocks[0] = wbVirtBlock(0, page0);
    shell.appendChild(W.blocks[0]);
    W.blockH = W.blocks[0].offsetHeight || (page0.length * 40);
    if (view === "list") W.offsetTop = res.head.offsetHeight;
    shell.classList.add("wb-virt");
    shell.style.height = (W.offsetTop + Math.ceil(total / W.pageSize) * W.blockH + 24) + "px";

    // 滚动容器是 #osfm-content（.e-content{overflow:auto} 在 grid 单元格内），
    // 不是外壳自己：读错元素会算出「全高窗口」把全部页面都拉下来。
    W.scroller = wbVirtScroller(shell);
    wbVirt = W;
    W.scroller.addEventListener("scroll", wbVirtOnScroll, { passive: true });
    window.addEventListener("resize", wbVirtOnResize);
    wbVirtPaint();
  }

  // 列表视图：把服务端 <table id=osfm-items> 换成可承载绝对定位块的外壳。
  // 表格不能可靠地作为绝对定位块 containing block，故抽走表头（粘滞）与卡片 <tr>，
  // 每个块是一个内部带 <tbody> 的 <table> —— 复用全部表格样式/选中，无需改单元格。
  function wbVirtListShell(container) {
    var table = container;
    var thead = table.querySelector("thead");
    var cardRows = [];
    var tbody = table.querySelector("tbody");
    if (tbody) {
      var cs = tbody.querySelectorAll(":scope > .osfm-card-item");
      for (var i = 0; i < cs.length; i++) cardRows.push(cs[i]);
    }
    if (!cardRows.length) return null;

    var shell = document.createElement("div");
    shell.className = "osfm-list-shell";
    shell.setAttribute("id", "osfm-items");
    shell.dataset.total = table.dataset.total || String(table.dataset.total || 0);
    shell.dataset.view = "list";
    var head = thead ? document.createElement("div") : null;
    if (head) head.className = "osfm-list-head";
    if (head && thead) head.appendChild(thead.cloneNode(true));
    if (head) shell.appendChild(head);

    table.parentNode.replaceChild(shell, table);   // 外壳取代表格，保持 id=osfm-items
    return { shell: shell, head: head, rows: cardRows };
  }

  // 向上找最近的滚动容器（overflow 含 scroll/auto）：块在其内部绝对定位，
  // 由此容器的 scrollTop/clientHeight 算可视区落在哪几页。
  function wbVirtScroller(el) {
    var p = el.parentElement;
    while (p) {
      var s = getComputedStyle(p);
      if (/(auto|scroll)/.test(s.overflowY) || /(auto|scroll)/.test(s.overflowX)) return p;
      p = p.parentElement;
    }
    return el;
  }

  function wbVirtOnScroll() {
    if (wbVirtRAF) return;
    wbVirtRAF = window.requestAnimationFrame(function () {
      wbVirtRAF = null;
      if (wbVirt) wbVirtPaint();
    });
  }
  function wbVirtOnResize() {
    if (wbVirtResizeT) return;
    wbVirtResizeT = setTimeout(function () {
      wbVirtResizeT = null;
      if (!wbVirt) return;
      // 列表外壳的第一个子是粘滞表头，不是块 0：统一用 .wb-pblock 找块（网格也一样）
      var b0 = wbVirt.container.querySelector(":scope > .wb-pblock");
      var nh = b0 ? b0.offsetHeight : wbVirt.pageSize * 40;
      var hd = wbVirt.view === "list" ? wbVirt.container.querySelector(":scope > .osfm-list-head") : null;
      var nt = hd ? hd.offsetHeight : 0;
      if (nh && nh !== wbVirt.blockH) {   // 列数随宽度变 → 块高变，全量重定位
        wbVirt.blockH = nh;
        if (hd) wbVirt.offsetTop = nt;
        wbVirt.container.style.height = (wbVirt.offsetTop + Math.ceil(wbVirt.total / wbVirt.pageSize) * nh + 24) + "px";
        for (var k in wbVirt.blocks) if (wbVirt.blocks[k].parentNode)
          wbVirt.blocks[k].style.top = (wbVirt.offsetTop + k * nh) + "px";
        wbVirtPaint();
      } else if (hd && nt !== wbVirt.offsetTop) {  // 块高没变但表头高变了：块一起平移
        wbVirt.offsetTop = nt;
        for (var k in wbVirt.blocks) if (wbVirt.blocks[k].parentNode)
          wbVirt.blocks[k].style.top = (nt + k * wbVirt.blockH) + "px";
        wbVirtPaint();
      }
    }, 120);
  }
  var wbVirtRAF = null, wbVirtResizeT = null;


  document.addEventListener("click", function (ev) {
    if (wbMenu && !ev.target.closest(".osfm-menu")) wbHideMenu();
    var act = ev.target.closest && ev.target.closest("[id^='osfm-act-'],[data-wb]");
    if (act) {
      if ((act.id === "osfm-act-open" || act.dataset.wb === "open") && sel) { wbOpen(sel); return; }
      if ((act.id === "osfm-act-archive" || act.dataset.wb === "archive") && sel) { wbArchive(sel.dataset.rel); return; }
      if (act.id === "osfm-act-rename" && sel) { wbRename(sel); return; }
      if (act.id === "osfm-act-copy") { wbCopy(); return; }
      if (act.id === "osfm-act-delete") { wbDelete(); return; }
      if (act.id === "osfm-act-new-folder") { wbNew("folder"); return; }
      if (act.id === "osfm-act-new-file") { wbNew("file"); return; }
      if (act.id === "osfm-act-restore" && sel) { wbRestore(sel); return; }
      if (act.dataset.wb === "restore" && sel) { wbRestore(sel); return; }
    }
    if (ev.target.closest && ev.target.closest("#e-back")) { history.back(); return; }
    if (ev.target.closest && ev.target.closest("#e-fwd")) { history.forward(); return; }
    if (ev.target.closest && ev.target.closest("#e-details-toggle")) {
      var root = $id("osfm-root"); if (root) root.classList.toggle("e-nodetails"); return;
    }
    var item = wbItem(ev);
    if (!item) { if (ev.target.closest && !ev.target.closest(".e-side,.e-menu,.osfm-upload,input")) { selMulti = []; wbSelect(null); } return; }
    if (ev.ctrlKey || ev.metaKey) { wbToggleSel(item); return; }
    if (ev.shiftKey && sel) { wbRangeSel(item); return; }
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
  function wbEnsureMenu() {
    if (!wbMenu) { wbMenu = document.createElement("div"); wbMenu.className = "osfm-menu"; document.body.appendChild(wbMenu); }
  }
  function wbShowMenu(ev) {
    wbMenu.style.display = "block";
    wbMenu.style.left = Math.min(ev.clientX, window.innerWidth - wbMenu.offsetWidth - 8) + "px";
    wbMenu.style.top = Math.min(ev.clientY, window.innerHeight - wbMenu.offsetHeight - 8) + "px";
  }
  function wbMenuAdd(label, fn) {
    var b = document.createElement("button");
    b.type = "button"; b.textContent = label;
    b.addEventListener("click", function () { wbHideMenu(); fn(); });
    wbMenu.appendChild(b);
  }
  document.addEventListener("contextmenu", function (ev) {
    var root = $id("osfm-root");
    if (!root) return;
    var item = wbItem(ev);
    if (!item) {
      // 空白处右键：新建（仅目录视图 + 管理员；集合/搜索/回收站视图不适用）
      if (root.dataset.admin !== "1" || wbIsTrash() ||
          /[?&](cat|q)=/.test(wbHere())) return;
      if (!ev.target.closest || !ev.target.closest("#osfm-content")) return;
      ev.preventDefault();
      wbEnsureMenu(); wbMenu.innerHTML = "";
      wbMenuAdd("新建文件夹", function () { wbNew("folder"); });
      wbMenuAdd("新建文件", function () { wbNew("file"); });
      wbShowMenu(ev);
      return;
    }
    ev.preventDefault();
    wbSelect(item);
    wbEnsureMenu(); wbMenu.innerHTML = "";
    var trash = wbIsTrash();
    if (trash) {
      wbMenuAdd("还原到原位置", function () { wbRestore(item); });
      wbShowMenu(ev);
      return;
    }
    wbMenuAdd("打开", function () { wbOpen(item); });
    if (item.dataset.dir === "0")
      wbMenuAdd("下载", function () { location.href = "/files/raw?path=" + encodeURIComponent(item.dataset.rel) + "&dl=1"; });
    if (item.dataset.dir === "1")
      wbMenuAdd("打包下载（zip）", function () { location.href = "/files/zip?path=" + encodeURIComponent(item.dataset.rel); });
    if (root.dataset.admin === "1") {
      wbMenuAdd("重命名", function () { wbRename(item); });
      wbMenuAdd("复制副本", function () { wbCopy(); });
      if (item.dataset.bucket !== "1")
        wbMenuAdd("删除（移入回收站）", function () { wbDelete(); });
      if (item.dataset.bucket !== "1")
        wbMenuAdd("归档到 archive/", function () { wbArchive(item.dataset.rel); });
    }
    wbShowMenu(ev);
  });

  // 键盘：Esc 取消 / Enter 打开 / Backspace 上一级 / Delete 删除 / F2 重命名
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Enter" && ev.target && ev.target.id === "osfm-search") {
      // 全局搜索：回车按名字搜整个工作区；输入过程仍是当前视图即时过滤
      var q = ev.target.value.trim();
      if (q) wbNav("/files?q=" + encodeURIComponent(q) + "&sort=name&view=grid");
      return;
    }
    if (ev.key === "Escape") { wbHideMenu(); selMulti = []; wbSelect(null); return; }
    var tag = (document.activeElement || {}).tagName || "";
    if (/INPUT|TEXTAREA|SELECT/i.test(tag)) return;
    if (!$id("osfm-root")) return;
    if (ev.key === "Enter" && sel) { wbOpen(sel); return; }
    if (ev.key === "Delete" && (sel || selMulti.length) && !wbIsTrash()) { ev.preventDefault(); wbDelete(); return; }
    if (ev.key === "F2" && sel && !selMulti.length) { ev.preventDefault(); wbRename(sel); return; }
    if (ev.key === "Backspace") {
      var up = document.querySelector(".e-nav a.e-navbtn");
      // up 是 boost 容器内的 <a>：有 HTMX 走局部换页，无则原生整页
      if (up) { ev.preventDefault(); up.click(); }
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

  // 拖拽移动：拖到目录卡片 / 面包屑 / 侧栏位置上即移入该目录
  var dragRel = null;
  function wbDropTarget(ev) {
    var item = ev.target.closest && ev.target.closest(".osfm-card-item");
    if (item && item.dataset.dir === "1" && item.dataset.rel !== dragRel)
      return { dirRel: item.dataset.rel };
    var crumb = ev.target.closest && ev.target.closest(".e-address a");
    if (crumb) {
      var m = (crumb.getAttribute("href") || "").match(/[?&]path=([^&]*)/);
      return { dirRel: m ? decodeURIComponent(m[1].replace(/\+/g, "%20")) : "" };
    }
    var loc = ev.target.closest && ev.target.closest(".e-side a.e-loc");
    if (loc) {
      var m2 = (loc.getAttribute("href") || "").match(/[?&]path=([^&]*)/);
      if (m2) return { dirRel: decodeURIComponent(m2[1].replace(/\+/g, "%20")) };
    }
    return null;
  }
  document.addEventListener("dragstart", function (ev) {
    var item = wbItem(ev);
    if (!item || wbIsTrash()) return;
    dragRel = item.dataset.rel;
    if (ev.dataTransfer) {
      ev.dataTransfer.setData("text/plain", dragRel);
      ev.dataTransfer.effectAllowed = "move";
    }
  });
  document.addEventListener("dragend", function () { dragRel = null; });
  document.addEventListener("dragover", function (ev) {
    if (!dragRel) return;
    if (wbDropTarget(ev)) {
      ev.preventDefault();
      if (ev.dataTransfer) ev.dataTransfer.dropEffect = "move";
    }
  });
  document.addEventListener("drop", function (ev) {
    if (!dragRel) return;
    var dest = wbDropTarget(ev);
    if (!dest) return;
    ev.preventDefault();
    var rel = dragRel; dragRel = null;
    if (dest.dirRel === rel || dest.dirRel.indexOf(rel + "/") === 0) {
      showToast("不能把目录移进它自己", "warning");
      return;
    }
    wbPost("/files/move", { path: rel, dest: dest.dirRel, here: wbHere() }, "移动失败");
  });

  // ---- 缩略图 / 视频预览的受控加载（0.8.34 性能修复）-------------------
  // 网格里所有媒体一律 data-src（见 files/_item.html）：进入视口才挂 src，并且
  // 全页最多 MEDIA_MAX 个请求在飞。以前首屏 120〜1000 个 <img src> 同时发出，
  // 每个 /files/thumb 首次命中都要在服务端解码原图（CPU），浏览器和 worker 线程
  // 池一起被堵，表现就是“文件一多整页打不开”。
  var MEDIA_MAX = 4;
  var mediaIO = null, mediaActive = 0, mediaQueue = [];

  function mediaPump() {
    while (mediaActive < MEDIA_MAX && mediaQueue.length) {
      var el = mediaQueue.shift();
      var url = el.getAttribute("data-src");
      if (!url) continue;
      el.removeAttribute("data-src");
      mediaActive++;
      (function (node) {
        var settled = false;
        function done() {
          if (settled) return;
          settled = true;
          mediaActive--;
          mediaPump();
        }
        node.addEventListener("load", done, { once: true });
        node.addEventListener("error", done, { once: true });
        setTimeout(done, 20000);          // 元数据事件丢了也不能永久占位
        if (node.tagName === "VIDEO") node.preload = "metadata";
        node.src = url;
      })(el);
    }
  }

  function mediaScan(scope) {
    var host = scope || document;
    var list = host.querySelectorAll("[data-src]");
    if (!list.length) return;
    if (!window.IntersectionObserver) {   // 不支持的浏览器：排队加载，仍限并发
      for (var i = 0; i < list.length; i++) mediaQueue.push(list[i]);
      mediaPump();
      return;
    }
    if (!mediaIO) {
      mediaIO = new IntersectionObserver(function (entries) {
        var added = false;
        entries.forEach(function (en) {
          if (!en.isIntersecting) return;
          mediaIO.unobserve(en.target);
          mediaQueue.push(en.target);
          added = true;
        });
        if (added) mediaPump();
      }, { rootMargin: "400px 0px" });
    }
    for (var j = 0; j < list.length; j++) mediaIO.observe(list[j]);
  }

  function mediaOnSwap() {
    mediaQueue.length = 0;               // 换页/导航后丢弃未发出的旧卡片
    var items = document.getElementById("osfm-items");
    if (items) mediaScan(items);
  }
  mediaOnSwap();

  // ---- 「加载更多」：每次只取一页卡片，追加到按钮之前 ------------------
  // 端点回 X-Osfm-More / X-Osfm-Offset 告知还有没有下一页，前端不自己猜。
  document.addEventListener("click", function (ev) {
    var btn = ev.target && ev.target.closest ? ev.target.closest("#osfm-more") : null;
    if (!btn || btn.disabled) return;
    var url = btn.getAttribute("data-more-url");
    if (!url) return;
    btn.disabled = true;
    var label = btn.textContent;
    btn.textContent = "加载中…";
    fetch(url, { credentials: "same-origin" })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return Promise.all([
          r.text(),
          r.headers.get("X-Osfm-More") || "0",
          r.headers.get("X-Osfm-Offset") || "0",
        ]);
      })
      .then(function (res) {
        var html = res[0], more = res[1] === "1", off = res[2];
        // 列表视图的按钮包在 <tr> 里：插到整行之前才不会插坏表格
        var slot = btn.closest("tr") || btn;
        if (html) slot.insertAdjacentHTML("beforebegin", html);
        var items = $id("osfm-items");
        if (items) mediaScan(items);
        wbStatus();
        if (more) {
          btn.setAttribute("data-more-url", url.replace(/offset=\d+/, "offset=" + off));
          btn.disabled = false;
          btn.textContent = label;
        } else {
          if (slot.parentNode) slot.parentNode.removeChild(slot);
        }
      })
      .catch(function () {
        btn.disabled = false;
        btn.textContent = label || "加载更多";
        document.body.dispatchEvent(new CustomEvent("console:toast",
          { bubbles: true, detail: { message: "加载失败，请重试", level: "error" } }));
      });
  });

  // 目录变更监听（W16）：Agent 在另一头写文件时，打开中的目录自动刷新。
  // 弹窗打开 / 有选中 / 正在输入时不刷，避免打断操作。
  var watchEs = null, watchUrl = "";  function attachWatch() {
    if (!window.EventSource) return;
    var root = $id("osfm-root");
    if (!root) {
      if (watchEs) { watchEs.close(); watchEs = null; watchUrl = ""; }
      return;
    }
    var here = wbHere();
    if (/(^|&)q=/.test(here)) return;   // 搜索结果视图不监听
    var url = "/files/watch" + (here ? "?" + here : "");
    if (watchEs && watchUrl === url) return;
    if (watchEs) watchEs.close();
    watchUrl = url;
    var es = new EventSource(url);
    watchEs = es;
    es.addEventListener("changed", function () {
      if (document.querySelector("dialog[open]")) return;
      if (sel || selMulti.length) return;
      if (document.activeElement &&
          /INPUT|TEXTAREA/.test(document.activeElement.tagName || "")) return;
      wbNav("/files" + (here ? "?" + here : ""));
    });
    es.addEventListener("busy", function () {
      // 服务端监听名额满了：自己退避，别让 EventSource 每 3 秒重连一次
      es.close();
      if (watchEs === es) { watchEs = null; watchUrl = ""; }
      setTimeout(attachWatch, 60000);
    });
    es.onerror = function () {
      es.close();
      if (watchEs === es) { watchEs = null; watchUrl = ""; }
    };
  }
  attachWatch();
  document.body.addEventListener("htmx:afterSwap", attachWatch);
  // boosted 换页/写操作重渲染后，新卡片里的媒体同样要受控加载
  document.body.addEventListener("htmx:afterSwap", mediaOnSwap);
  // 选完文件即上传：htmx 提交（hx-swap=none，不 swap），服务端回 HX-Trigger
  document.addEventListener("change", function (ev) {
    if (!ev.target || !ev.target.matches || !ev.target.matches(".osfm-upload input[type=file]")) return;
    if (!ev.target.files || !ev.target.files.length) return;
    var form = ev.target.closest("form");
    if (window.htmx) htmx.trigger(form, "submit");
    else form.submit();
  });
  // 上传完成：服务端 osfm:nav 事件 → boosted 导航到目标目录（无白屏 + 历史记录）
  document.body.addEventListener("osfm:nav", function (ev) {
    var to = ev.detail && ev.detail.to;
    if (to) wbNav(to);
  });
  // 初次渲染：状态栏 + 窗口化接管
  document.addEventListener("DOMContentLoaded", function () { wbStatus(); if (window.__wbVirtInit) window.__wbVirtInit(); });
  if (document.readyState !== "loading") { wbStatus(); if (window.__wbVirtInit) window.__wbVirtInit(); }
  })();

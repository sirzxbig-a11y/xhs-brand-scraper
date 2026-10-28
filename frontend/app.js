/* 小红书视频品牌刮削 - 前端逻辑 */
const $ = (sel) => document.querySelector(sel);

async function api(path, opts = {}) {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${resp.status}`);
  }
  return resp.json();
}

function fmtTime(s) {
  if (s === undefined || s === null || s === "") return "—";
  if (typeof s === "number") {
    const m = Math.floor(s / 60), sec = Math.round(s % 60);
    return `${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
  }
  return s;
}

function segText(pair) {
  return `${fmtTime(pair[0])}-${fmtTime(pair[1])}`;
}

/* ---------------- Tab 切换 ---------------- */
document.querySelectorAll(".tab").forEach((t) => {
  t.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((x) => x.classList.remove("active"));
    t.classList.add("active");
    $(`#tab-${t.dataset.tab}`).classList.add("active");
    if (t.dataset.tab === "data") { loadData(); startDataPolling(); }
    if (t.dataset.tab === "bloggers") loadBloggers();
    if (t.dataset.tab === "config") loadConfig();
    if (t.dataset.tab === "logs") startLogPolling();
  });
});

/* ---------------- 抓取进度 ---------------- */
const PHASE_LABEL = {
  collecting: "收集视频", capturing: "截屏录音", recognizing: "识别中",
  done: "完成", risk: "遇到风控", error: "失败",
};
const PHASE_BADGE = {
  collecting: "running", capturing: "running", recognizing: "running",
  done: "normal", risk: "risk", error: "fail",
};

let dataTimer = null;
let lastPhases = {};

function fmtElapsed(startedAt) {
  if (!startedAt) return "—";
  const ms = Date.now() - new Date(startedAt).getTime();
  if (isNaN(ms) || ms < 0) return "—";
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  return m > 0 ? `${m}分${s % 60}秒` : `${s}秒`;
}

async function loadProgress() {
  try {
    const prog = await api("/api/progress");
    renderProgress(prog);
    if (detectCompletion(prog)) loadData();
  } catch (e) {
    /* 静默失败 */
  }
}

function renderProgress(prog) {
  const el = $("#progress-list");
  if (!el) return;
  const entries = Object.entries(prog);
  if (!entries.length) {
    el.innerHTML = '<p class="hint">暂无抓取记录。点博主列表里的「重新抓取」开始。</p>';
    return;
  }
  el.innerHTML = entries.map(([id, p]) => {
    const pct = p.total ? Math.min(100, Math.round((p.current / p.total) * 100)) : 0;
    const terminal = ["done", "risk", "error"].includes(p.phase);
    const count = p.total ? ` ${p.current}/${p.total}` : "";
    const elapsed = terminal ? "" : ` · 已用 ${fmtElapsed(p.started_at)}`;
    return `
      <div class="progress-item">
        <div class="progress-head">
          <b>${escapeHtml(id)}</b>
          <span class="badge ${PHASE_BADGE[p.phase] || "pending"}">${PHASE_LABEL[p.phase] || p.phase}</span>
          <span class="hint">${escapeHtml(p.stage || "")}${count}${elapsed}</span>
        </div>
        <div class="progress-bar"><div class="progress-fill" style="width:${pct}%"></div></div>
        <div class="hint">${escapeHtml(p.message || "")}</div>
        ${p.video_title ? `<div class="hint">当前视频：${escapeHtml(p.video_title)}</div>` : ""}
      </div>`;
  }).join("");
}

function detectCompletion(prog) {
  let refresh = false;
  for (const [id, p] of Object.entries(prog)) {
    const prev = lastPhases[id];
    const terminal = ["done", "risk", "error"].includes(p.phase);
    if (terminal && prev && !["done", "risk", "error"].includes(prev)) refresh = true;
    lastPhases[id] = p.phase;
  }
  return refresh;
}

function startDataPolling() {
  loadProgress();
  if (!dataTimer) dataTimer = setInterval(loadProgress, 2000);
}

$("#data-refresh").addEventListener("click", () => {
  loadProgress();
  loadData();
});

/* ---------------- 运行日志 ---------------- */
let logLastId = 0;
let logTimer = null;

async function loadLogs() {
  try {
    const r = await api(`/api/logs?since=${logLastId}`);
    const view = $("#log-view");
    if (!view || !r.logs) return;
    for (const e of r.logs) {
      const line = document.createElement("div");
      line.className = "log-line log-" + (e.level || "info").toLowerCase();
      line.textContent = `[${e.time}] ${e.level} ${e.name}: ${e.msg}`;
      view.appendChild(line);
    }
    logLastId = r.last_id;
    const auto = $("#log-autoscroll");
    if (auto && auto.checked) view.scrollTop = view.scrollHeight;
  } catch (e) {
    /* 静默失败，避免打断页面 */
  }
}

function startLogPolling() {
  loadLogs();
  if (!logTimer) logTimer = setInterval(loadLogs, 2000);
}

$("#log-clear").addEventListener("click", () => {
  $("#log-view").innerHTML = "";
});

/* ---------------- 状态 ---------------- */
async function loadStatus() {
  try {
    const s = await api("/api/status");
    const running = s.running?.length ? `抓取中：${s.running.join("、")}` : "";
    const next = s.next_daily_run ? `下次定时：${s.next_daily_run.replace("T", " ").slice(0, 16)}` : "定时任务未启动";
    $("#status-pill").textContent = [running, next].filter(Boolean).join(" · ") || "空闲";
  } catch (e) {
    $("#status-pill").textContent = "后端未连接";
  }
}

/* ---------------- 博主管理 ---------------- */
const STATUS_BADGE = {
  "正常": "normal", "遇到风控": "risk", "抓取中": "running",
  "失败": "fail", "待抓取": "pending",
};

async function loadBloggers() {
  const bloggers = await api("/api/bloggers");
  const rows = $("#blogger-rows");
  rows.innerHTML = "";
  if (!bloggers.length) {
    rows.innerHTML = '<tr><td colspan="5" class="hint">暂无博主，请先在上方新增。</td></tr>';
    return;
  }
  for (const b of bloggers) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${escapeHtml(b.nickname)}</td>
      <td>${escapeHtml(b.id)}</td>
      <td><span class="badge ${STATUS_BADGE[b.status] || "pending"}">${escapeHtml(b.status)}</span>
          ${b.error ? `<div class="hint">${escapeHtml(b.error)}</div>` : ""}</td>
      <td>${escapeHtml(b.last_scraped_at || "—")}</td>
      <td>
        <button class="btn small" data-act="edit">编辑</button>
        <button class="btn small" data-act="rescrape">重新抓取</button>
        <button class="btn small" data-act="delete">删除</button>
      </td>`;
    tr.querySelector('[data-act="edit"]').onclick = () => fillForm(b);
    tr.querySelector('[data-act="rescrape"]').onclick = async () => {
      await api(`/api/bloggers/${encodeURIComponent(b.id)}/rescrape`, { method: "POST" });
      loadBloggers(); loadStatus();
    };
    tr.querySelector('[data-act="delete"]').onclick = async () => {
      if (!confirm(`确认删除博主「${b.nickname}」？其抓取结果也会被删除。`)) return;
      await api(`/api/bloggers/${encodeURIComponent(b.id)}`, { method: "DELETE" });
      loadBloggers();
    };
    rows.appendChild(tr);
  }
}

function fillForm(b) {
  $("#b-nickname").value = b.nickname;
  $("#b-id").value = b.id;
  $("#b-home").value = b.home_url || "";
}

$("#blogger-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const nickname = $("#b-nickname").value.trim();
  const id = $("#b-id").value.trim();
  const home_url = $("#b-home").value.trim();
  if (!nickname || !id) return alert("昵称和小红书号为必填项");
  const body = { nickname, id, home_url: home_url || null };
  try {
    // 若已存在则更新，否则新增
    const bloggers = await api("/api/bloggers");
    if (bloggers.some((b) => b.id === id)) {
      await api(`/api/bloggers/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify(body) });
    } else {
      await api("/api/bloggers", { method: "POST", body: JSON.stringify(body) });
    }
    e.target.reset();
    loadBloggers();
  } catch (err) {
    alert(err.message);
  }
});

/* ---------------- API 配置 ---------------- */
async function loadConfig() {
  const c = await api("/api/config");
  $("#c-deepseek-key").value = c.deepseek_api_key || "";
  $("#c-deepseek-base").value = c.deepseek_base_url || "";
  $("#c-vision-model").value = c.vision_model || "";
  $("#c-llm-model").value = c.llm_model || "";
  $("#c-asr-provider").value = c.asr_provider || "aliyun";
  $("#c-aliyun-key").value = c.asr?.aliyun_app_key || "";
  $("#c-tencent-id").value = c.asr?.tencent_secret_id || "";
  $("#c-tencent-key").value = c.asr?.tencent_secret_key || "";
  $("#c-tencent-appid").value = c.asr?.tencent_app_id || "";
}

$("#config-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = {
    deepseek_api_key: $("#c-deepseek-key").value.trim(),
    deepseek_base_url: $("#c-deepseek-base").value.trim() || "https://api.deepseek.com",
    vision_model: $("#c-vision-model").value.trim() || "deepseek-vl2",
    llm_model: $("#c-llm-model").value.trim() || "deepseek-chat",
    asr_provider: $("#c-asr-provider").value,
    asr: {
      aliyun_app_key: $("#c-aliyun-key").value.trim(),
      aliyun_access_key_id: "",
      aliyun_access_key_secret: "",
      tencent_secret_id: $("#c-tencent-id").value.trim(),
      tencent_secret_key: $("#c-tencent-key").value.trim(),
      tencent_app_id: $("#c-tencent-appid").value.trim(),
    },
  };
  await api("/api/config", { method: "POST", body: JSON.stringify(body) });
  const msg = $("#config-msg");
  msg.textContent = "已保存";
  setTimeout(() => (msg.textContent = ""), 2000);
});

/* ---------------- 数据展示 ---------------- */
async function loadData() {
  const tree = $("#data-tree");
  tree.innerHTML = '<p class="hint">加载中…</p>';
  try {
    const list = await api("/api/results");
    if (!list.length) {
      tree.innerHTML = '<p class="hint">暂无博主数据。</p>';
      return;
    }
    tree.innerHTML = "";
    for (const summary of list) {
      const group = document.createElement("div");
      group.className = "blogger-group";
      const head = document.createElement("div");
      head.className = "blogger-head";
      head.innerHTML = `<span>${escapeHtml(summary.nickname)} <span class="badge ${STATUS_BADGE[summary.status] || "pending"}">${escapeHtml(summary.status)}</span></span>
                        <span class="hint">${summary.video_count} 条视频 · ${escapeHtml(summary.last_scraped_at || "未抓取")}</span>`;
      group.appendChild(head);
      if (summary.has_result) {
        const body = document.createElement("div");
        body.className = "brand-body";
        body.style.display = "none";
        group.appendChild(body);
        head.onclick = () => (body.style.display = body.style.display === "none" ? "block" : "none");
        const result = await api(`/api/results/${encodeURIComponent(summary.blogger_id)}`);
        renderResult(body, result);
      }
      tree.appendChild(group);
    }
  } catch (e) {
    tree.innerHTML = `<p class="hint">加载失败：${escapeHtml(e.message)}</p>`;
  }
}

function renderResult(container, result) {
  container.innerHTML = "";
  const brandMap = {};
  for (const v of result.videos || []) {
    const entries = Object.entries(v.brands || {});
    if (!entries.length) {
      (brandMap["（未识别到品牌）"] ||= []).push({ video: v, segs: { visual: [], asr: [] } });
    } else {
      for (const [brand, segs] of entries) {
        (brandMap[brand] ||= []).push({ video: v, segs });
      }
    }
  }
  if (!Object.keys(brandMap).length) {
    container.innerHTML = '<p class="hint">暂无数据。</p>';
    return;
  }
  for (const [brand, items] of Object.entries(brandMap)) {
    const bg = document.createElement("div");
    bg.className = "brand-group";
    const name = document.createElement("div");
    name.className = "brand-name";
    name.textContent = brand;
    bg.appendChild(name);
    for (const { video, segs } of items) {
      const item = document.createElement("div");
      item.className = "video-item";
      const visual = (segs.visual || []).map((p) => `<span class="seg visual">画面 ${segText(p)}</span>`).join("");
      const asr = (segs.asr || []).map((p) => `<span class="seg asr">口播 ${segText(p)}</span>`).join("");
      item.innerHTML = `
        <div class="video-head">
          <a href="${escapeHtml(video.url)}" target="_blank">${escapeHtml(video.title || video.note_id)}</a>
          <button class="btn small danger del-video">删除</button>
        </div>
        <div class="meta">发布：${escapeHtml(video.published_at || "—")}</div>
        <div>${visual || '<span class="hint">无画面时间段</span>'} ${asr || '<span class="hint">无口播时间段</span>'}</div>`;
      item.querySelector(".del-video").onclick = async () => {
        if (!confirm("确认删除这条视频的识别结果？（仅从此结果中移除）")) return;
        await api(`/api/results/${encodeURIComponent(result.blogger_id)}/videos/${encodeURIComponent(video.note_id)}`, { method: "DELETE" });
        loadData();
      };
      bg.appendChild(item);
    }
    container.appendChild(bg);
  }
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

/* ---------------- 小红书登录 ---------------- */
let loginTimer = null;

$("#login-btn").addEventListener("click", async () => {
  const el = $("#login-status");
  el.textContent = "正在打开浏览器…";
  try {
    const r = await api("/api/login", { method: "POST" });
    if (!r.started) {
      el.textContent = (r.login && r.login.message) || "无法开始登录";
      return;
    }
    startLoginPolling();
  } catch (e) {
    el.textContent = "请求失败：" + e.message;
  }
});

function startLoginPolling() {
  if (loginTimer) return;
  const tick = async () => {
    try {
      const s = await api("/api/login");
      $("#login-status").textContent = s.message || s.state;
      if (["blocked", "error", "done", "busy"].includes(s.state)) {
        clearInterval(loginTimer);
        loginTimer = null;
        if (s.state === "done") { loadBloggers(); loadData(); }
      }
    } catch (e) {
      /* ignore */
    }
  };
  tick();
  loginTimer = setInterval(tick, 2000);
}

/* ---------------- 初始化 ---------------- */
loadBloggers();
loadStatus();
setInterval(loadStatus, 5000);

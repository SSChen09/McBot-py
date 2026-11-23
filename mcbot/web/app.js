"use strict";

/* ------------------------------------------------------------------ 工具 */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

async function api(path, options = {}) {
  const opts = Object.assign({ headers: { "Content-Type": "application/json" } }, options);
  const res = await fetch(path, opts);
  let data = {};
  try {
    data = await res.json();
  } catch (_) {
    data = {};
  }
  if (res.status === 401) {
    location.replace("/login");
    throw new Error("登录已过期");
  }
  if (!res.ok || data.error) {
    throw new Error(data.error || `请求失败（HTTP ${res.status}）`);
  }
  return data;
}

const post = (path, body) =>
  api(path, { method: "POST", body: JSON.stringify(body || {}) });

function toast(message, kind = "") {
  const box = $("#toasts");
  const el = document.createElement("div");
  el.className = "toast" + (kind ? " " + kind : "");
  el.textContent = message;
  box.appendChild(el);
  while (box.children.length > 4) box.removeChild(box.firstChild);
  setTimeout(() => {
    el.classList.add("out");
    setTimeout(() => el.remove(), 260);
  }, 2800);
}

function debounce(fn, delay) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delay);
  };
}

function hueOf(name) {
  let sum = 0;
  for (const ch of name) sum = (sum + ch.codePointAt(0)) % 360;
  return sum;
}

/* ------------------------------------------------------------------ 状态 */
const STATUS = {
  "在线": { cls: "on", text: "在线" },
  "连接中": { cls: "warn", text: "正在连接…" },
  "断开": { cls: "err", text: "连接已断开" },
  "离线": { cls: "off", text: "离线" },
};

const state = {
  booted: false,
  accounts: [],
  config: null,
  reply: null,
  logs: [],
  lastSeq: 0,
  selected: new Set(),
  mode: "console",
  lines: ["", "", "", ""],
  currentLine: 1,
  pollBusy: false,
  replyDirty: false,
  lastTargetKey: "",
  keyDeadline: 0,
  keyTimer: null,
};

/* ------------------------------------------------------------------ 登录密钥 */
function renderKeyTime() {
  const left = Math.max(0, Math.round((state.keyDeadline - Date.now()) / 1000));
  const minutes = String(Math.floor(left / 60)).padStart(2, "0");
  const seconds = String(left % 60).padStart(2, "0");
  $("#keyText").textContent = `${minutes}:${seconds}`;
  $("#keyPill").classList.toggle("warn", left <= 120);
  if (left <= 0) {
    location.replace("/login");
  }
}

function setKeyExpiry(seconds) {
  if (!seconds || seconds <= 0) {
    location.replace("/login");
    return;
  }
  state.keyDeadline = Date.now() + seconds * 1000;
  renderKeyTime();
  if (!state.keyTimer) state.keyTimer = setInterval(renderKeyTime, 1000);
}

async function renewKey() {
  try {
    const data = await post("/api/renew");
    setKeyExpiry(data.expires_in);
    toast(`已续约，密钥有效期还剩 ${Math.floor(data.expires_in / 60)} 分钟`, "ok");
  } catch (err) {
    toast(err.message, "err");
  }
}

async function logout() {
  try {
    await post("/api/logout");
  } catch (_) {
    /* 会话可能已失效，直接回登录页 */
  }
  location.replace("/login");
}

/* ------------------------------------------------------------------ 渲染：账号 */
function renderAccounts() {
  const list = $("#accList");
  const empty = $("#accEmpty");
  const accounts = state.accounts;

  empty.style.display = accounts.length ? "none" : "";

  // 用名称顺序校正已选集合
  const names = new Set(accounts.map((a) => a.name));
  for (const name of Array.from(state.selected)) {
    if (!names.has(name)) state.selected.delete(name);
  }

  const existing = new Map();
  for (const node of Array.from(list.querySelectorAll(".acc"))) {
    existing.set(node.dataset.name, node);
  }

  const frag = document.createDocumentFragment();
  for (const acc of accounts) {
    let row = existing.get(acc.name);
    if (!row) {
      row = document.createElement("div");
      row.className = "acc";
      row.dataset.name = acc.name;
      row.innerHTML =
        '<label class="check"><input type="checkbox"></label>' +
        '<div class="avatar"></div>' +
        '<div class="acc__main"><div class="acc__name"></div>' +
        '<div class="acc__sub"><span class="dot"></span><span class="acc__status"></span></div></div>' +
        '<span class="tag"></span>';
      row.addEventListener("click", (event) => {
        if (event.target.closest(".check")) return;
        toggleSelect(acc.name);
      });
      row.querySelector(".check input").addEventListener("change", (event) => {
        if (event.target.checked) state.selected.add(acc.name);
        else state.selected.delete(acc.name);
        syncSelection();
      });
    }
    existing.delete(acc.name);

    const checked = state.selected.has(acc.name);
    row.classList.toggle("checked", checked);
    row.querySelector(".check input").checked = checked;

    const avatar = row.querySelector(".avatar");
    avatar.textContent = acc.name.slice(0, 1);
    avatar.style.setProperty("--hue", hueOf(acc.name));

    row.querySelector(".acc__name").textContent = acc.name;

    const info = STATUS[acc.status] || { cls: "off", text: acc.status };
    const dot = row.querySelector(".acc__sub .dot");
    dot.className = "dot dot--" + info.cls;
    const statusText = row.querySelector(".acc__status");
    statusText.textContent = info.text;
    if (acc.error) {
      statusText.textContent = info.text + " · " + acc.error;
      row.title = acc.error;
    } else {
      row.title = "";
    }

    const tag = row.querySelector(".tag");
    if (acc.has_password) {
      tag.textContent = "已设密码";
      tag.className = "tag";
    } else {
      tag.textContent = "无密码";
      tag.className = "tag tag--muted";
    }
    if (acc.online && acc.hp != null) {
      row.title = `❤ ${acc.hp} · 🍗 ${acc.food} · XYZ ${acc.x} / ${acc.y} / ${acc.z}`;
    }
    frag.appendChild(row);
  }

  // 移除已不存在的行
  for (const node of existing.values()) node.remove();
  list.appendChild(frag);

  updateCounts();
}

function toggleSelect(name) {
  if (state.selected.has(name)) state.selected.delete(name);
  else state.selected.add(name);
  syncSelection();
}

function syncSelection() {
  for (const row of $$(".acc")) {
    const checked = state.selected.has(row.dataset.name);
    row.classList.toggle("checked", checked);
    const box = row.querySelector(".check input");
    if (box.checked !== checked) box.checked = checked;
  }
  updateCounts();
}

function updateCounts() {
  const total = state.accounts.length;
  const selected = state.selected.size;
  $("#selectedChip").textContent = `已选 ${selected} / ${total}`;
}

function updateStats(stats) {
  $("#onlineText").textContent = `在线 ${stats.online} / ${stats.total}`;
  const pill = $("#onlinePill");
  pill.classList.toggle("on", stats.online > 0);
  const pillDot = pill.querySelector(".dot");
  pillDot.className = "dot " + (stats.online > 0 ? "dot--on" : "dot--off");

  const addr = stats.addr || (state.config && state.config.addr) || "";
  const dot = $("#serverDot");
  dot.className = "dot " + (stats.online > 0 ? "dot--on" : "dot--off");
  $("#serverText").textContent = addr
    ? `${addr} · ${stats.online} 个账号在线`
    : "未连接服务器 · 离线模式";
  $("#footerAddr").textContent = addr || "—";
}

/* ------------------------------------------------------------------ 渲染：服务器 */
function renderLines() {
  const box = $("#lines");
  box.innerHTML = "";
  for (let i = 1; i <= 4; i++) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "line-btn" + (i === state.currentLine ? " active" : "");
    btn.textContent = String(i);
    btn.title = state.lines[i - 1]
      ? `线路 ${i}：${state.lines[i - 1]}`
      : `线路 ${i}（空）`;
    btn.addEventListener("click", () => switchLine(i));
    box.appendChild(btn);
  }
}

function syncConfigForm(config) {
  state.config = config;
  state.lines = [1, 2, 3, 4].map((i) => (config.lines && config.lines[String(i)]) || "");
  state.currentLine = config.current_line || 1;

  $("#addr").value = config.addr || state.lines[state.currentLine - 1] || "";
  $("#gap").value = config.gap || 1;
  $("#optReconnect").checked = !!config.auto_reconnect;
  $("#optLogin").checked = !!config.auto_login;
  $("#optRespawn").checked = !!config.auto_respawn;
  renderLines();
}

function switchLine(line) {
  state.lines[state.currentLine - 1] = $("#addr").value.trim();
  state.currentLine = line;
  $("#addr").value = state.lines[line - 1] || "";
  renderLines();
  saveSettings();
}

const saveSettingsNow = async () => {
  const addr = $("#addr").value.trim();
  state.lines[state.currentLine - 1] = addr;
  try {
    await post("/api/config", {
      addr,
      gap: Number($("#gap").value) || 1,
      auto_reconnect: $("#optReconnect").checked,
      auto_login: $("#optLogin").checked,
      auto_respawn: $("#optRespawn").checked,
      current_line: state.currentLine,
      lines: {
        "1": state.lines[0] || "",
        "2": state.lines[1] || "",
        "3": state.lines[2] || "",
        "4": state.lines[3] || "",
      },
    });
    if (state.config) state.config.addr = addr;
  } catch (err) {
    toast(err.message, "err");
  }
};

const saveSettings = debounce(saveSettingsNow, 450);

/* ------------------------------------------------------------------ 渲染：日志 */
function classify(log) {
  if (log.kind === "chat") return "chat";
  const text = log.text || "";
  if (/📩|💬|⚙️|🔏/.test(text)) return "priv";
  if (/❌|⛔|⚠|失败|错误|超时|断开/.test(text)) return "err";
  if (/✅|✔|成功|已进入服务器/.test(text)) return "ok";
  return "sys";
}

function logVisible(log) {
  if (state.mode === "console") return true;
  if (log.kind === "chat") return true;
  return /📩|💬|⚙️|🔏|对你说|悄悄/.test(log.text || "");
}

function makeLogLine(log) {
  const row = document.createElement("div");
  row.className = "l " + classify(log);
  const time = document.createElement("span");
  time.className = "t";
  time.textContent = log.ts || "";
  const msg = document.createElement("span");
  msg.className = "m";
  msg.textContent = log.text || "";
  row.append(time, msg);
  return row;
}

function appendLogs(logs) {
  if (!logs || !logs.length) return;
  const box = $("#log");
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 90;

  for (const log of logs) {
    state.logs.push(log);
    state.lastSeq = Math.max(state.lastSeq, log.seq);
    if (logVisible(log)) box.appendChild(makeLogLine(log));
  }
  if (state.logs.length > 1200) state.logs.splice(0, state.logs.length - 1200);

  while (box.childElementCount > 800) box.removeChild(box.firstChild);
  $("#logCount").textContent = `${state.logs.length} 条`;
  if (atBottom) box.scrollTop = box.scrollHeight;
}

function renderLog() {
  const box = $("#log");
  box.innerHTML = "";
  const frag = document.createDocumentFragment();
  for (const log of state.logs) {
    if (logVisible(log)) frag.appendChild(makeLogLine(log));
  }
  box.appendChild(frag);
  box.scrollTop = box.scrollHeight;
  $("#logCount").textContent = `${state.logs.length} 条`;
}

/* ------------------------------------------------------------------ 渲染：自动回复 */
function ruleRow(rule = {}) {
  const row = document.createElement("div");
  row.className = "rule";

  const trigger = document.createElement("input");
  trigger.className = "input";
  trigger.dataset.k = "trigger";
  trigger.placeholder = "关键词（空=任意）";
  trigger.value = rule.trigger || "";

  const reply = document.createElement("input");
  reply.className = "input";
  reply.dataset.k = "reply";
  reply.placeholder = "回复内容，可用 {name} {msg}";
  reply.value = rule.reply || "";

  const command = document.createElement("input");
  command.className = "input";
  command.dataset.k = "command";
  command.placeholder = "/命令，可用 {name} {msg}";
  command.value = rule.command || "";

  const owner = document.createElement("label");
  owner.className = "switch";
  owner.title = "仅主人名单内的玩家可触发";
  const ownerBox = document.createElement("input");
  ownerBox.type = "checkbox";
  ownerBox.dataset.k = "owner_only";
  ownerBox.checked = !!rule.owner_only;
  const track = document.createElement("span");
  track.className = "track";
  owner.append(ownerBox, track);

  const del = document.createElement("button");
  del.type = "button";
  del.className = "rule__del";
  del.title = "删除这条规则";
  del.textContent = "×";
  del.addEventListener("click", () => {
    row.remove();
    markReplyDirty();
  });

  for (const input of [trigger, reply, command, ownerBox]) {
    input.addEventListener("input", markReplyDirty);
    input.addEventListener("change", markReplyDirty);
  }

  row.append(trigger, reply, command, owner, del);
  return row;
}

function markReplyDirty() {
  state.replyDirty = true;
  $("#replyHint").textContent = "有未保存的修改";
  $("#replyHint").style.color = "var(--yellow)";
}

function resetReplyHint() {
  state.replyDirty = false;
  $("#replyHint").textContent = "触发词留空 = 任意私聊；{name} / {msg} 会被替换";
  $("#replyHint").style.color = "";
}

function renderReply(config) {
  state.reply = config;
  $("#replyEnabled").checked = !!config.enabled;
  $("#masters").value = (config.masters || []).join(", ");
  const box = $("#rules");
  box.innerHTML = "";
  for (const rule of config.rules || []) box.appendChild(ruleRow(rule));
  resetReplyHint();
}

function collectReply() {
  const rules = $$(".rule").map((row) => ({
    trigger: row.querySelector('[data-k="trigger"]').value.trim(),
    reply: row.querySelector('[data-k="reply"]').value,
    command: row.querySelector('[data-k="command"]').value.trim(),
    owner_only: row.querySelector('[data-k="owner_only"]').checked,
  }));
  const masters = $("#masters").value
    .split(/[,，]/)
    .map((s) => s.trim())
    .filter(Boolean);
  return { enabled: $("#replyEnabled").checked, masters, rules };
}

async function saveReply(silent = false) {
  try {
    const data = collectReply();
    await post("/api/auto-reply", data);
    state.reply = data;
    resetReplyHint();
    if (!silent) toast("自动回复规则已保存", "ok");
  } catch (err) {
    toast(err.message, "err");
  }
}

/* ------------------------------------------------------------------ 轮询 */
async function poll() {
  if (state.pollBusy) return;
  state.pollBusy = true;
  try {
    const data = await api(`/api/state?since=${state.lastSeq}`);
    if (!state.booted) {
      state.booted = true;
      syncConfigForm(data.config);
      renderReply(data.auto_reply);
    } else if ((data.stats || {}).seq < state.lastSeq) {
      // 服务重启后 seq 归零，重新同步一次完整日志
      state.lastSeq = 0;
      state.logs = [];
      $("#log").innerHTML = "";
    }
    state.accounts = data.accounts || [];
    renderAccounts();
    appendLogs(data.logs || []);
    updateStats(data.stats || { online: 0, total: 0, addr: "" });
    if (data.expires_in) setKeyExpiry(data.expires_in);
    updateTargets();
  } catch (err) {
    console.warn("状态同步失败", err);
  } finally {
    state.pollBusy = false;
  }
}

function updateTargets() {
  const select = $("#target");
  const online = state.accounts.filter((a) => a.online).map((a) => a.name);
  const key = online.join("|");
  if (key === state.lastTargetKey) return;
  state.lastTargetKey = key;

  const current = select.value;
  select.innerHTML = "";
  const all = document.createElement("option");
  all.value = "";
  all.textContent = `全部在线账号（${online.length}）`;
  select.appendChild(all);
  for (const name of online) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    select.appendChild(opt);
  }
  if (online.includes(current)) select.value = current;
}

/* ------------------------------------------------------------------ 操作 */
async function connectSelected() {
  const names = Array.from(state.selected);
  if (!names.length) return toast("请先勾选要上线的账号", "err");
  try {
    await post("/api/connect", { names });
    toast(`已加入上线队列：${names.length} 个账号`, "ok");
    setTimeout(poll, 300);
  } catch (err) {
    toast(err.message, "err");
  }
}

async function disconnectSelected() {
  const names = Array.from(state.selected);
  if (!names.length) return toast("请先勾选要下线的账号", "err");
  try {
    await post("/api/disconnect", { names });
    toast(`已下线 ${names.length} 个账号`, "ok");
    setTimeout(poll, 300);
  } catch (err) {
    toast(err.message, "err");
  }
}

async function deleteSelected() {
  const names = Array.from(state.selected);
  if (!names.length) return toast("请先勾选要删除的账号", "err");
  if (!confirm(`确定删除 ${names.length} 个账号？该操作不可撤销。`)) return;
  try {
    await api("/api/accounts", {
      method: "DELETE",
      body: JSON.stringify({ names }),
    });
    for (const name of names) state.selected.delete(name);
    toast(`已删除 ${names.length} 个账号`, "ok");
    setTimeout(poll, 300);
  } catch (err) {
    toast(err.message, "err");
  }
}

async function resetReconnect() {
  const names = Array.from(state.selected);
  if (!names.length) return toast("请先勾选账号", "err");
  try {
    await post("/api/disconnect", { names });
    setTimeout(async () => {
      try {
        await post("/api/connect", { names });
        toast("已重置并重新排队连接", "ok");
      } catch (err) {
        toast(err.message, "err");
      }
    }, 600);
  } catch (err) {
    toast(err.message, "err");
  }
}

async function ping() {
  const button = $("#btnPing");
  const addr = $("#addr").value.trim();
  if (!addr) return toast("请先填写服务器地址", "err");
  button.disabled = true;
  button.textContent = "测速中…";
  try {
    const data = await api(`/api/ping?addr=${encodeURIComponent(addr)}`);
    const chip = $("#pingChip");
    chip.hidden = false;
    chip.textContent = `${data.latency} ms · ${data.version}`;
    chip.className = "chip " + (data.latency < 120 ? "chip--ok" : "chip--warn");
    const players = data.online != null ? `在线 ${data.online}/${data.max}` : "";
    $("#pingText").textContent = [players, `协议 ${data.protocol}`]
      .filter(Boolean)
      .join(" · ");
  } catch (err) {
    $("#pingChip").hidden = true;
    toast(err.message, "err");
  } finally {
    button.disabled = false;
    button.textContent = "测速";
  }
}

/* ------------------------------------------------------------------ 事件 */
function bind() {
  $("#btnRefresh").addEventListener("click", async () => {
    await poll();
    toast("状态已刷新");
  });

  $("#btnRenew").addEventListener("click", renewKey);
  $("#btnLogout").addEventListener("click", logout);

  $("#btnSelectAll").addEventListener("click", () => {
    for (const acc of state.accounts) state.selected.add(acc.name);
    syncSelection();
  });
  $("#btnInvert").addEventListener("click", () => {
    for (const acc of state.accounts) {
      if (state.selected.has(acc.name)) state.selected.delete(acc.name);
      else state.selected.add(acc.name);
    }
    syncSelection();
  });
  $("#btnSelectNone").addEventListener("click", () => {
    state.selected.clear();
    syncSelection();
  });

  $("#btnConnect").addEventListener("click", connectSelected);
  $("#btnDisconnect").addEventListener("click", disconnectSelected);
  $("#btnReconnect").addEventListener("click", resetReconnect);
  $("#btnDelete").addEventListener("click", deleteSelected);

  $("#addForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const name = $("#newName").value.trim();
    const password = $("#newPwd").value;
    if (!name) return toast("请填写账号名", "err");
    try {
      await post("/api/accounts", { name, password });
      $("#newName").value = "";
      $("#newPwd").value = "";
      toast(`已添加账号 ${name}`, "ok");
      setTimeout(poll, 250);
    } catch (err) {
      toast(err.message, "err");
    }
  });

  $("#addr").addEventListener("input", saveSettings);
  $("#gap").addEventListener("change", saveSettings);
  for (const id of ["optReconnect", "optLogin", "optRespawn"]) {
    $("#" + id).addEventListener("change", saveSettings);
  }
  $("#btnSaveLine").addEventListener("click", async () => {
    await saveSettingsNow();
    renderLines();
    toast(`线路 ${state.currentLine} 已保存`, "ok");
  });
  $("#btnPing").addEventListener("click", ping);

  $("#modeSeg").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-mode]");
    if (!button) return;
    state.mode = button.dataset.mode;
    for (const item of $$("#modeSeg button")) {
      item.classList.toggle("active", item === button);
    }
    renderLog();
  });

  $("#btnClear").addEventListener("click", () => {
    state.logs = [];
    $("#log").innerHTML = "";
    $("#logCount").textContent = "0 条";
  });

  $("#cmdForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = $("#cmdInput").value.trim();
    if (!text) return;
    const target = $("#target").value;
    try {
      const data = await post("/api/command", {
        text,
        names: target ? [target] : [],
      });
      $("#cmdInput").value = "";
      toast(`已发送给 ${data.sent} 个账号`, "ok");
      setTimeout(poll, 200);
    } catch (err) {
      toast(err.message, "err");
    }
  });

  $("#replySwitch").addEventListener("click", (event) => event.stopPropagation());
  $("#replyEnabled").addEventListener("change", () => saveReply(true));

  $("#btnAddRule").addEventListener("click", () => {
    $("#rules").appendChild(ruleRow());
    markReplyDirty();
    $("#rules").scrollTop = $("#rules").scrollHeight;
  });
  $("#btnSaveReply").addEventListener("click", () => saveReply(false));
  $("#masters").addEventListener("input", markReplyDirty);

  window.addEventListener("beforeunload", (event) => {
    if (state.replyDirty) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
}

function startClock() {
  const tick = () => {
    const now = new Date();
    $("#footerClock").textContent = now.toLocaleTimeString("zh-CN", { hour12: false });
  };
  tick();
  setInterval(tick, 1000);
}

bind();
startClock();
poll();
setInterval(poll, 1200);

const api = (path, opts = {}) => {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  const token = localStorage.getItem("token");
  if (token) headers.Authorization = "Bearer " + token;
  return fetch(path, { ...opts, headers }).then(async (r) => {
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || r.statusText);
    return data;
  });
};

const $ = (id) => document.getElementById(id);
let current = null;
let poll = null;

function showApp() {
  $("auth").classList.add("hidden");
  $("app").classList.remove("hidden");
  $("who").textContent = localStorage.getItem("email") || "";
  refresh();
  showMcp();
}

function showAuth() {
  $("app").classList.add("hidden");
  $("auth").classList.remove("hidden");
}

$("btn-reg").onclick = async () => {
  $("auth-err").textContent = "";
  try {
    const d = await api("/api/register", {
      method: "POST",
      body: JSON.stringify({ email: $("email").value, password: $("password").value }),
    });
    localStorage.setItem("token", d.token);
    localStorage.setItem("email", d.email);
    showApp();
  } catch (e) {
    $("auth-err").textContent = e.message;
  }
};

$("btn-login").onclick = async () => {
  $("auth-err").textContent = "";
  try {
    const d = await api("/api/login", {
      method: "POST",
      body: JSON.stringify({ email: $("email").value, password: $("password").value }),
    });
    localStorage.setItem("token", d.token);
    localStorage.setItem("email", d.email);
    showApp();
  } catch (e) {
    $("auth-err").textContent = e.message;
  }
};

$("btn-out").onclick = () => {
  localStorage.clear();
  showAuth();
};

async function refresh() {
  const devices = await api("/api/devices");
  $("devices").innerHTML = devices
    .map(
      (d) =>
        `<div class="device ${current === d.device_id ? "active" : ""}" data-id="${d.device_id}">
          <span class="dot ${d.status === "online" ? "on" : "off"}"></span>
          <b>${d.hostname || d.device_id}</b><br/>
          <span class="muted">${d.os} · ${d.status}</span>
        </div>`
    )
    .join("") || "<p class='muted'>Пока пусто. Создай токен и запусти агент.</p>";
  $("devices").querySelectorAll(".device").forEach((el) => {
    el.onclick = () => openDevice(el.dataset.id);
  });
  const sticks = await api("/api/sticks");
  $("sticks").innerHTML = sticks
    .map(
      (s) =>
        `<div class="stick">${s.label}<br/><code>${s.token}</code><br/>
        <a href="/api/sticks/${s.token}/usb.zip">скачать USB zip</a></div>`
    )
    .join("");
}

async function openDevice(id) {
  current = id;
  $("empty").classList.add("hidden");
  $("panel").classList.remove("hidden");
  await loadDevice();
  if (poll) clearInterval(poll);
  poll = setInterval(loadDevice, 3000);
  refresh();
}

async function loadDevice() {
  if (!current) return;
  const d = await api("/api/devices/" + current);
  $("dev-title").textContent = `${d.status === "online" ? "🟢" : "🔴"} ${d.hostname || d.device_id}`;
  const hw = d.hardware || {};
  $("dev-meta").textContent = `${d.os} · CPU ${hw.cpu || "?"} · RAM ${hw.ram_gb || "?"} GB`;
  const msgs = await api("/api/devices/" + current + "/messages");
  $("chat").innerHTML = msgs
    .map((m) => `<div class="msg ${m.role}"><b>${m.role}:</b> ${escapeHtml(m.content)}</div>`)
    .join("");
  $("chat").scrollTop = $("chat").scrollHeight;
}

function escapeHtml(s) {
  return s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

$("btn-send").onclick = async () => {
  if (!current) return;
  const message = $("msg").value.trim();
  if (!message) return;
  $("msg").value = "";
  $("chat").innerHTML += `<div class="msg user"><b>user:</b> ${escapeHtml(message)}</div>`;
  try {
    await api("/api/devices/" + current + "/chat", { method: "POST", body: JSON.stringify({ message }) });
  } catch (e) {
    $("chat").innerHTML += `<div class="msg assistant">${escapeHtml(e.message)}</div>`;
  }
  await loadDevice();
};

$("btn-cmd").onclick = async () => {
  if (!current) return;
  let params = {};
  try {
    params = JSON.parse($("params").value || "{}");
  } catch {
    alert("params должен быть JSON");
    return;
  }
  const r = await api("/api/devices/" + current + "/command", {
    method: "POST",
    body: JSON.stringify({ action: $("action").value, params }),
  });
  $("chat").innerHTML += `<div class="msg assistant">${escapeHtml((r.stdout || r.stderr || JSON.stringify(r)).slice(0, 2000))}</div>`;
};

function renderMcp(url, key, extra) {
  $("mcp-box").innerHTML =
    "URL: <code>" +
    url +
    "</code><br/>Ключ: <code>" +
    key +
    "</code><br/><button type='button' id='copy-mcp'>Копировать URL</button> " +
    "<button type='button' id='copy-key'>Копировать ключ</button>" +
    (extra ? "<br/>" + extra : "");
  const copy = (t) => navigator.clipboard.writeText(t).catch(() => alert(t));
  $("copy-mcp").onclick = () => copy(url);
  $("copy-key").onclick = () => copy(key);
}

$("btn-mcp").onclick = async () => {
  const d = await api("/api/mcp-key", { method: "POST" });
  renderMcp(d.mcp_url, d.mcp_key, "В Grok: Custom MCP + Bearer этот ключ. URL должен быть публичным (не localhost).");
};

async function showMcp() {
  try {
    const me = await api("/api/me");
    if (me.mcp_key) {
      renderMcp(me.mcp_url, me.mcp_key, "");
    }
  } catch (e) {}
}

$("btn-stick").onclick = async () => {
  const s = await api("/api/sticks", {
    method: "POST",
    body: JSON.stringify({ label: $("stick-label").value || "USB Agent" }),
  });
  alert("Токен: " + s.token + "\n\nСейчас скачается usb-maker.bat — запусти его (подтверди UAC). ISO не нужен.");
  const a = document.createElement("a");
  a.href = "/usb-maker.bat";
  a.download = "usb-maker.bat";
  document.body.appendChild(a);
  a.click();
  a.remove();
  refresh();
};

if (localStorage.getItem("token")) showApp();
showMcp();

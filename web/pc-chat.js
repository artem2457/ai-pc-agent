const params = new URLSearchParams(location.search);
const enrollToken = params.get("token") || "";
const wantedDevice = params.get("device") || "";

const $ = (id) => document.getElementById(id);

let jwt = "";
let deviceId = wantedDevice;

const api = (path, opts = {}) => {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  if (jwt) headers.Authorization = "Bearer " + jwt;
  return fetch(path, { ...opts, headers }).then(async (r) => {
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || r.statusText);
    return data;
  });
};

function escapeHtml(s) {
  return String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

function renderMessages(messages) {
  const box = $("chat");
  if (!messages.length) {
    box.innerHTML = "<p class='muted'>Это чат на этом компьютере. Напиши, что сделать — бот выполнит команду здесь.</p>";
    return;
  }
  box.innerHTML = messages
    .map(
      (m) =>
        `<div class="msg ${m.role}"><b>${m.role === "user" ? "Ты" : "Бот"}</b><pre>${escapeHtml(m.content || "")}</pre></div>`
    )
    .join("");
  box.scrollTop = box.scrollHeight;
}

async function loadMessages() {
  if (!deviceId) return;
  const messages = await api("/api/devices/" + deviceId + "/messages");
  renderMessages(messages);
}

async function boot() {
  if (!enrollToken) {
    $("boot-err").textContent = "Нет токена. Запусти агент с сайта (install-agent.bat) — чат откроется сам.";
    $("btn-send").disabled = true;
    return;
  }
  try {
    const session = await api("/api/pc/session", {
      method: "POST",
      body: JSON.stringify({ token: enrollToken, device_id: wantedDevice }),
    });
    jwt = session.token;
    deviceId = session.device_id;
    $("who").textContent = `${session.status === "online" ? "онлайн" : "офлайн"} · ${session.hostname} · ${session.os || ""}`;
    await loadMessages();
    setInterval(() => {
      loadMessages().catch(() => {});
    }, 2500);
  } catch (e) {
    $("boot-err").textContent = e.message;
    $("btn-send").disabled = true;
  }
}

$("chat-form").onsubmit = async (ev) => {
  ev.preventDefault();
  if (!deviceId || !jwt) return;
  const text = $("chat-in").value.trim();
  if (!text) return;
  $("chat-err").textContent = "";
  $("chat-in").value = "";
  $("btn-send").disabled = true;
  const poller = setInterval(() => {
    loadMessages().catch(() => {});
  }, 1200);
  try {
    await api("/api/devices/" + deviceId + "/chat", {
      method: "POST",
      body: JSON.stringify({ message: text }),
    });
  } catch (e) {
    $("chat-err").textContent = e.message;
  } finally {
    clearInterval(poller);
    $("btn-send").disabled = false;
    await loadMessages();
  }
};

boot();

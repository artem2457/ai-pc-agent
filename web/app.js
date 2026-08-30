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
}

function showAuth() {
  $("app").classList.add("hidden");
  $("auth").classList.remove("hidden");
}

async function authSuccess(d) {
  localStorage.setItem("token", d.token);
  localStorage.setItem("email", d.email);
  showApp();
}

$("btn-reg").onclick = async () => {
  $("auth-err").textContent = "";
  try {
    await authSuccess(
      await api("/api/register", {
        method: "POST",
        body: JSON.stringify({ email: $("email").value, password: $("password").value }),
      })
    );
  } catch (e) {
    $("auth-err").textContent = e.message;
  }
};

$("btn-login").onclick = async () => {
  $("auth-err").textContent = "";
  try {
    await authSuccess(
      await api("/api/login", {
        method: "POST",
        body: JSON.stringify({ email: $("email").value, password: $("password").value }),
      })
    );
  } catch (e) {
    $("auth-err").textContent = e.message;
  }
};

$("btn-out").onclick = () => {
  localStorage.clear();
  showAuth();
};

function downloadFile(url, name) {
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

async function createEnrollment() {
  return api("/api/sticks", {
    method: "POST",
    body: JSON.stringify({ label: $("stick-label").value || "PC Agent" }),
  });
}

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
    .join("") || "<p class='muted'>Нет устройств. Скачай агент или флешку.</p>";
  $("devices").querySelectorAll(".device").forEach((el) => {
    el.onclick = () => openDevice(el.dataset.id);
  });
  const sticks = await api("/api/sticks");
  $("sticks").innerHTML = sticks
    .map(
      (s) =>
        `<div class="stick">${s.label}<br/>
        <a class="dl" href="/install-agent.bat?token=${encodeURIComponent(s.token)}" download="install-agent.bat">Windows</a>
        <a class="dl" href="/install-agent.sh?token=${encodeURIComponent(s.token)}" download="install-agent.sh">Linux</a>
        <a class="dl" href="/usb-maker.bat?token=${encodeURIComponent(s.token)}" download="usb-maker.bat">USB</a></div>`
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

function renderMessages(messages) {
  const box = $("chat");
  if (!messages.length) {
    box.innerHTML = "<p class='muted'>Напиши задачу боту — он выполнит её на этом ПК.</p>";
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
  if (!current) return;
  const messages = await api("/api/devices/" + current + "/messages");
  renderMessages(messages);
}

async function loadDevice() {
  if (!current) return;
  const d = await api("/api/devices/" + current);
  $("dev-title").textContent = `${d.status === "online" ? "онлайн" : "офлайн"} · ${d.hostname || d.device_id}`;
  const hw = d.hardware || {};
  $("dev-meta").textContent = `${d.os} · CPU ${hw.cpu || "?"} · RAM ${hw.ram_gb || "?"} GB`;
  await loadMessages();
  const logs = await api("/api/devices/" + current + "/logs");
  $("logs").innerHTML = logs.length
    ? logs
        .map(
          (l) =>
            `<div class="log ${l.status}">
              <b>${escapeHtml(l.action)}</b> · exit ${l.exit_code ?? "?"} · ${escapeHtml(l.status || "")}
              <pre>${escapeHtml((l.stdout || l.stderr || "").slice(0, 1500))}</pre>
            </div>`
        )
        .join("")
    : "<p class='muted'>Пока нет выполненных команд.</p>";
}

$("chat-form").onsubmit = async (ev) => {
  ev.preventDefault();
  if (!current) return;
  const text = $("chat-in").value.trim();
  if (!text) return;
  $("chat-err").textContent = "";
  $("chat-in").value = "";
  $("btn-send").disabled = true;
  const poller = setInterval(() => {
    loadMessages().catch(() => {});
  }, 1200);
  try {
    await api("/api/devices/" + current + "/chat", {
      method: "POST",
      body: JSON.stringify({ message: text }),
    });
  } catch (e) {
    $("chat-err").textContent = e.message;
  } finally {
    clearInterval(poller);
    $("btn-send").disabled = false;
    await loadDevice();
  }
};

function escapeHtml(s) {
  return String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

$("btn-agent-win").onclick = async () => {
  const s = await createEnrollment();
  downloadFile("/install-agent.bat?token=" + encodeURIComponent(s.token), "install-agent.bat");
  refresh();
};

$("btn-agent-linux").onclick = async () => {
  const s = await createEnrollment();
  downloadFile("/install-agent.sh?token=" + encodeURIComponent(s.token), "install-agent.sh");
  refresh();
};

$("btn-stick").onclick = async () => {
  const s = await createEnrollment();
  downloadFile("/usb-maker.bat?token=" + encodeURIComponent(s.token), "usb-maker.bat");
  refresh();
};

if (localStorage.getItem("token")) showApp();

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
  renderMcpFallback();
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
    .join("") || "<p class='muted'>Нет устройств. Создай токен агента и запусти agent.py или флешку.</p>";
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
  $("dev-meta").textContent = `${d.os} · CPU ${hw.cpu || "?"} · RAM ${hw.ram_gb || "?"} GB · Grok → MCP → агент`;
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
    : "<p class='muted'>Пока нет команд. В Grok: list_devices → execute_command.</p>";
  const msgs = await api("/api/devices/" + current + "/messages");
  $("chat").innerHTML = msgs
    .map((m) => `<div class="msg ${m.role}"><b>${m.role}:</b> ${escapeHtml(m.content)}</div>`)
    .join("");
}

function escapeHtml(s) {
  return String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

$("btn-send").onclick = async () => {
  if (!current) return;
  const message = $("msg").value.trim();
  if (!message) return;
  $("msg").value = "";
  try {
    await api("/api/devices/" + current + "/chat", { method: "POST", body: JSON.stringify({ message }) });
  } catch (e) {
    alert(e.message);
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
  await api("/api/devices/" + current + "/command", {
    method: "POST",
    body: JSON.stringify({ action: $("action").value, params }),
  });
  await loadDevice();
};

function mcpView(me) {
  return {
    mcp_url: me.mcp_url || "https://bot.holderchat.com/mcp",
    mcp_key: me.mcp_key || "",
    mcp_connect_url: me.mcp_connect_url || (me.mcp_key ? `${me.mcp_url || "https://bot.holderchat.com/mcp"}?key=${me.mcp_key}` : ""),
    grok_connectors: me.grok_connectors || "https://grok.com/connectors",
    tagline: me.tagline || "Мозг — Grok Bot. Руки — агент. JSON команд, не скриншот терминала.",
  };
}

function renderMcp(me) {
  const m = mcpView(me || {});
  if ($("tagline")) $("tagline").textContent = m.tagline;
  const box = $("mcp-box");
  if (!box) return;
  const keyBlock = m.mcp_key
    ? `<code class="block">Authorization: Bearer ${escapeHtml(m.mcp_key)}</code>`
    : `<p class="muted">Войди — покажем MCP-ключ. Или возьми его на сайте после входа.</p>`;
  const connectBlock = m.mcp_connect_url
    ? `<code class="block">${escapeHtml(m.mcp_connect_url)}</code>`
    : `<p class="muted">После входа появится полный URL для Grok.</p>`;
  box.innerHTML = `
    <p><strong>1.</strong> <a href="${m.grok_connectors}" target="_blank">grok.com/connectors</a> → Custom</p>
    <p><strong>2.</strong> Server-URL для Grok:</p>
    ${connectBlock}
    <p><strong>3.</strong> URL + Bearer (если Grok просит OAuth):</p>
    <code class="block">${escapeHtml(m.mcp_url)}</code>
    ${keyBlock}
    <div class="row mcp-actions">
      <button type="button" id="copy-connect">Копировать URL для Grok</button>
      <button type="button" id="copy-key" class="ghost">Копировать ключ</button>
      <button type="button" id="btn-mcp" class="ghost">Новый ключ</button>
      <a class="dl" href="https://grok.com" target="_blank">Open in Grok</a>
    </div>`;
  const copy = (t) => navigator.clipboard.writeText(t).catch(() => prompt("Скопируй:", t));
  if ($("copy-connect")) $("copy-connect").onclick = () => copy(m.mcp_connect_url || m.mcp_url);
  if ($("copy-key")) $("copy-key").onclick = () => copy(m.mcp_key);
  if ($("btn-mcp")) $("btn-mcp").onclick = async () => {
    if (!localStorage.getItem("token")) return;
    if (!confirm("Старый MCP-ключ перестанет работать. Обнови connector в Grok.")) return;
    await showMcp(true);
  };
}

function renderMcpFallback() {
  renderMcp({ mcp_url: "https://bot.holderchat.com/mcp" });
}

async function showMcp(forceNew) {
  if (!localStorage.getItem("token")) {
    renderMcpFallback();
    return;
  }
  try {
    const me = forceNew ? await api("/api/mcp-key", { method: "POST" }) : await api("/api/me");
    renderMcp(me);
  } catch (e) {
    renderMcpFallback();
  }
}

$("btn-stick").onclick = async () => {
  const s = await api("/api/sticks", {
    method: "POST",
    body: JSON.stringify({ label: $("stick-label").value || "USB Agent" }),
  });
  alert("Токен агента: " + s.token);
  const a = document.createElement("a");
  a.href = "/usb-maker.bat";
  a.download = "usb-maker.bat";
  document.body.appendChild(a);
  a.click();
  a.remove();
  refresh();
};

if (localStorage.getItem("token")) showApp();
else renderMcpFallback();

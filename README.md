# AI PC Agent

USB-агент на пустом ПК + облачный сервер + любой мозг по MCP (Grok Bot, OpenAI, …).

Grok не подключается к компьютеру напрямую. Он вызывает инструменты на **твоём** сервере, сервер шлёт команду агенту по WebSocket, агент выполняет и возвращает JSON: `exit_code`, `stdout_tail`, `stderr_tail`. Не скриншот терминала.

**Без Node.js.** Только Python + обычный HTML.

## Запуск сервера

```
cd ai-pc-agent
python -m pip install -r server/requirements.txt
copy .env.example .env
cd server
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Открой http://localhost:8000

1. Зарегистрируйся и подключи ПК (токен агента)
2. Управляй компьютером через **Grok Bot** — MCP настроен на сервере, пользователю ключи не нужны
3. На сайте — только устройства и лог команд

Чтобы Grok из интернета достучался до MCP, в `.env` поставь публичный адрес:

```
PUBLIC_URL=https://твой-домен-или-ngrok
```

И пробрось порт (cloudflared / ngrok / свой VPS). Localhost Grok не увидит.

## Grok Bot

Пользователь работает в **Grok Bot**. Сервер отдаёт MCP-инструменты (`/mcp`), Grok вызывает их, агент выполняет на ПК.

Connector настраивается **один раз** администратором (не в UI сайта). См. `.env-server` / deploy docs.

Инструменты: `list_devices`, `execute_command`, `get_hardware`, … — JSON ответ, не скриншот.

## Флешка для пустого ПК (Alpine Linux, open source)

WinRE / Windows **не нужны**. Программа записывает **Alpine Linux** (~400 MB) — хватит флешки **512 MB+**.

1. С сайта скачай `usb-maker.bat`, запусти **от администратора** (токен уже внутри)
2. Выбери USB — диск будет стёрт
3. В BIOS: Boot from USB, нужен интернет (Ethernet проще)
4. На сайте появляется устройство
5. Grok может ставить Linux, Windows или пакеты через агента

Лицензия: Alpine Linux — MIT/GPL, без проприетарного Windows.

## Агент на этой Windows-машине (уже есть ОС)

```
python -m pip install -r agent/requirements.txt
python agent/agent.py --url http://localhost:8000 --token ТВОЙ_ТОКЕН
```

Служба при старте:

```
powershell -ExecutionPolicy Bypass -File agent\windows\install.ps1 -Token ТВОЙ_ТОКЕН -Url http://localhost:8000
```

## Агент на Linux VPS

```
ssh root@vps
curl -fsSL http://ТВОЙ_СЕРВЕР:8000/install.sh | bash -s -- --token ТВОЙ_ТОКЕН --url http://ТВОЙ_СЕРВЕР:8000
```

## Запасной мозг (чат на сайте, не Grok)

Без ключа — встроенный планировщик.

```
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
```

Подойдёт любой совместимый API через `OPENAI_BASE_URL`.

## Docker (только Python)

```
docker compose up --build
```

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

1. Зарегистрируйся
2. Создай токен
3. Для Grok: создай MCP-ключ. Для чата на сайте мозг запасной (встроенный план / OpenAI).

Чтобы Grok из интернета достучался до MCP, в `.env` поставь публичный адрес:

```
PUBLIC_URL=https://твой-домен-или-ngrok
```

И пробрось порт (cloudflared / ngrok / свой VPS). Localhost Grok не увидит.

## Grok Bot (основной мозг)

1. На сайте: **Создать MCP-ключ**
2. [grok.com/connectors](https://grok.com/connectors) → New Connector → Custom
3. URL: `{PUBLIC_URL}/mcp`
4. Auth: `Authorization: Bearer <mcp_key>`
5. Open in Grok → «поставь Docker на мой ПК» / «установи Windows, ключ …»

Инструменты: `list_devices`, `get_hardware`, `execute_command`, `read_file`, `write_file`, `upload_file`, `download_file`, `install_package`, `install_os`, `get_processes`, `get_services`, `get_logs`, `reboot`, `shutdown`, `get_screen` (запасной).

Большой stdout обрезается. Полный лог — `get_logs`.

## Флешка для пустого ПК

Python и Windows ISO **не нужны**. Программа — обычный PowerShell.

1. С сайта скачай `usb-maker.zip`, запусти `start.bat` **от администратора**
2. Вставь токен, выбери USB — диск будет стёрт. Загрузка берётся из WinRE этого ПК
3. В BIOS: Boot from USB, нужен интернет
4. На сайте появляется устройство
5. Grok в своём браузере находит официальный Windows, вызывает `download_file`, потом `install_os`

Zip с токеном — не загрузочный. Запись флешки делает `start.bat`.

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

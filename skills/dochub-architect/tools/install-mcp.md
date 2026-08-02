# Подключение MCP-сервера dochub

Сервер даёт детерминированные операции: разбор схем, сборку манифеста
репозитория, поиск компонентов, проверку и рендер контекстов, превью и
публикацию.

## Автоматически

```bash
python skills/dochub-architect/tools/install_mcp.py --agent cursor
python skills/dochub-architect/tools/install_mcp.py --agent claude
python skills/dochub-architect/tools/install_mcp.py --agent codex
```

Ключ `--scope user` пропишет сервер глобально, а не только для проекта.
Существующие серверы в конфиге не затираются.

## Вручную

```json
{
  "mcpServers": {
    "dochub": {
      "command": "python",
      "args": ["-m", "app.mcp_server"],
      "cwd": "<путь к dochub-architect-tool>"
    }
  }
}
```

Куда класть: `.cursor/mcp.json`, `.mcp.json` (Claude Code), `.codex/mcp.json`.
После правки конфига агент нужно перезапустить.

## Проверка

```bash
python -m app.mcp_server
```

Сервер должен написать в stderr `dochub_mcp: запуск` и ждать ввода —
он общается по stdio. Прервать: Ctrl+C.

## Требования

| Что | Зачем | Если нет |
|-----|-------|----------|
| Python 3.10+ | сам сервер | обязателен |
| Java 11+ | рендер PlantUML | превью и рендер не работают |
| `lib/plantuml.jar` | рендер диаграмм | скачивается `python setup_assets.py` |
| `vendor/dochub/` | метамодель DocHub | идёт в комплекте |
| `vendor/viewer-static.min.js` | показ оригинала DrawIO | левая панель превью не отрисуется |
| `lib/elk/` | раскладка ELK | откат на smetana, схема шире |
| `GITLAB_TOKEN` | создание merge request | ветка уйдёт, MR создаётся вручную |

## Токен GitLab

Только через переменную окружения:

```bash
export GITLAB_TOKEN=...          # bash
$env:GITLAB_TOKEN = '...'        # PowerShell
```

Нужен scope `api`. Не передавай токен параметрами инструментов и не вставляй
в конфигурационные файлы проекта.

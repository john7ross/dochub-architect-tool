#!/usr/bin/env python3
"""
Прописать MCP-сервер dochub в конфигурацию агента.

Форматы конфигов у Cursor, Claude Code и Codex различаются мелочами, но
все они держат серверы в объекте mcpServers. Скрипт добавляет запись, не
затирая уже существующие серверы.

Запуск:
    python install_mcp.py --agent cursor
    python install_mcp.py --agent claude --scope project
    python install_mcp.py --print          # только показать фрагмент
"""

import argparse
import json
import sys
from pathlib import Path

SERVER_NAME = 'dochub'

# skills/dochub-architect/tools/install_mcp.py -> корень проекта
PROJECT_ROOT = Path(__file__).resolve().parents[3]

TARGETS = {
    'cursor': {
        'project': Path('.cursor/mcp.json'),
        'user': Path.home() / '.cursor' / 'mcp.json',
    },
    'claude': {
        'project': Path('.mcp.json'),
        'user': Path.home() / '.claude.json',
    },
    'codex': {
        'project': Path('.codex/mcp.json'),
        'user': Path.home() / '.codex' / 'mcp.json',
    },
}


def server_entry() -> dict:
    """Описание сервера для конфигурации."""
    return {
        'command': sys.executable,
        'args': ['-m', 'app.mcp_server'],
        'cwd': str(PROJECT_ROOT),
    }


def install(config_path: Path) -> str:
    """
    Добавить сервер в конфигурацию.

    Args:
        config_path: Путь к файлу конфигурации

    Returns:
        Сообщение о результате
    """
    config = {}

    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding='utf-8')) or {}
        except json.JSONDecodeError as e:
            return f"ОШИБКА: {config_path} повреждён ({e}). Файл не тронут."

    servers = config.setdefault('mcpServers', {})
    existed = SERVER_NAME in servers
    servers[SERVER_NAME] = server_entry()

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )

    action = 'обновлён' if existed else 'добавлен'
    return f"Сервер {SERVER_NAME} {action} в {config_path}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agent', choices=sorted(TARGETS), help='Какой агент настроить')
    parser.add_argument('--scope', choices=['project', 'user'], default='project',
                        help='Конфиг проекта или пользователя (по умолчанию project)')
    parser.add_argument('--print', dest='show', action='store_true',
                        help='Только показать фрагмент конфигурации')
    args = parser.parse_args()

    if args.show or not args.agent:
        print(json.dumps({'mcpServers': {SERVER_NAME: server_entry()}},
                         ensure_ascii=False, indent=2))
        if not args.agent:
            print('\nЧтобы прописать автоматически: --agent cursor|claude|codex',
                  file=sys.stderr)
        return 0

    print(install(TARGETS[args.agent][args.scope]))
    print('Перезапустите агент, чтобы он подхватил сервер.')
    return 0


if __name__ == '__main__':
    sys.exit(main())

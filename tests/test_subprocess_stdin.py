"""
Дочерние процессы не наследуют stdin сервера.

Сервер общается по stdio: его stdin — труба протокола MCP, которая не
закрывается, пока жив клиент. Дочерний процесс, унаследовавший такой stdin,
может не завершиться вовсе — git в этом случае висел до таймаута, и от этого
переставали работать и dochub_changed_entities, и вся публикация.

Проверка статическая: воспроизвести условие в тесте нельзя, оно возникает
только когда сервер запущен клиентом MCP.
"""

import ast
from pathlib import Path

APP = Path(__file__).parent.parent / 'app'

# Вызовы, которые запускают дочерний процесс
SPAWNING = {'run', 'Popen', 'call', 'check_call', 'check_output'}


def _spawn_calls():
    """Все вызовы subprocess.*, запускающие процесс."""
    for path in sorted(APP.rglob('*.py')):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if (isinstance(func, ast.Attribute) and func.attr in SPAWNING
                    and isinstance(func.value, ast.Name)
                    and func.value.id == 'subprocess'):
                yield path, node


class TestChildStdin:
    """Каждый запуск дочернего процесса закрывает ему stdin."""

    def test_calls_found(self):
        """Проверка имеет смысл, только если вызовы вообще находятся."""
        assert list(_spawn_calls())

    def test_every_call_sets_stdin(self):
        """У каждого вызова задан stdin или передан input."""
        bad = []

        for path, node in _spawn_calls():
            keywords = {kw.arg for kw in node.keywords}
            # input=... сам открывает и закрывает трубу stdin
            if not ({'stdin', 'input'} & keywords):
                bad.append(f'{path.name}:{node.lineno}')

        assert bad == [], (
            'дочерний процесс унаследует stdin сервера: ' + ', '.join(bad)
        )

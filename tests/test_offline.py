"""
Сервер работает офлайн.

Правило проекта: в работе ничего не тянется из сети. Всё тяжёлое —
PlantUML, просмотрщик drawio, jar-файлы раскладки — скачивается один раз
скриптом установки, а дальше инструмент должен работать в закрытом контуре.
Единственное исключение — API GitLab, ради которого проект и существует.

Проверка статическая: сетевой вызов, добавленный в новый модуль, обычно
замечают уже на машине без интернета.
"""

import ast
from pathlib import Path

APP = Path(__file__).parent.parent / 'app'

# Библиотеки, которые ходят наружу
NETWORK_MODULES = {'httpx', 'requests', 'urllib.request', 'urllib3', 'aiohttp',
                   'socket', 'ftplib', 'telnetlib'}

# Где сеть разрешена и почему
ALLOWED = {
    'publisher.py': 'API GitLab: создание merge request',
}


def _imports(path: Path):
    """Импортируемые модули верхнего уровня."""
    tree = ast.parse(path.read_text(encoding='utf-8'))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.module


class TestNoNetwork:
    """Ни один модуль, кроме публикации, не ходит в сеть."""

    def test_modules_found(self):
        assert list(APP.rglob('*.py'))

    def test_only_publisher_talks_to_network(self):
        offenders = []

        for path in sorted(APP.rglob('*.py')):
            if path.name in ALLOWED:
                continue

            for module in _imports(path):
                root = module.split('.')[0]
                if module in NETWORK_MODULES or root in NETWORK_MODULES:
                    offenders.append(f'{path.name}: {module}')

        assert offenders == [], (
            'сеть в работе разрешена только для GitLab: ' + ', '.join(offenders)
        )

    def test_preview_serves_localhost_only(self):
        """Превью отдаёт схему и YAML без авторизации — только своей машине."""
        source = (APP / 'preview' / 'server.py').read_text(encoding='utf-8')
        assert "host: str = '127.0.0.1'" in source

    def test_metamodel_is_local(self):
        """Метамодель DocHub исполняется из vendor, а не тянется с сайта."""
        source = (APP / 'core' / 'dochub' / 'native_renderer.py').read_text(
            encoding='utf-8')
        assert 'DOCHUB_METAMODEL_DIR' in source
        assert 'http' not in source.lower().replace('https://', '')

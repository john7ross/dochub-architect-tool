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
    # Доменная схема живёт у компании и меняется; держать её копию в
    # актуальном состоянии руками — не работа пользователя. Ссылка задаётся
    # им самим, а без сети берётся ранее скачанная копия, см. ddd_source
    'ddd_source.py': 'доменная схема по ссылке, если её задали ссылкой',
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


class TestDddByLinkStaysOptional:
    """Сеть — только на подготовке, и только если схему задали ссылкой."""

    def test_work_reads_the_downloaded_copy(self, tmp_path, monkeypatch):
        """
        После подготовки инструмент в сеть не ходит.

        В закрытом контуре её может не быть вовсе, а схему к этому моменту
        уже скачали в dochub_repo_sync.
        """
        import urllib.request

        from app.core import ddd_source

        def explode(*args, **kwargs):
            raise AssertionError('обращение в сеть после подготовки')

        monkeypatch.setattr(urllib.request, 'urlopen', explode)
        local = tmp_path / 'ddd.drawio'
        local.write_text('<mxfile/>', encoding='utf-8')

        assert ddd_source.local_path(str(local))[0] == local

    def test_only_preparation_downloads(self):
        """Скачивает только refresh — его зовёт шаг подготовки."""
        source = (APP / 'core' / 'ddd_source.py').read_text(encoding='utf-8')
        after_refresh = source[source.index('def refresh('):]

        assert 'urlopen' in after_refresh
        assert 'urlopen' not in source[:source.index('def refresh(')]

    def test_brief_does_not_download(self):
        """Бриф читает копию, а не тянет схему заново."""
        server = (APP / 'mcp_server.py').read_text(encoding='utf-8')
        brief = server[server.index('async def dochub_brief'):]

        assert 'ddd_source.local_path' in brief
        assert 'ddd_source.refresh' not in brief

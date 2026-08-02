"""
Тесты сценариев для агента.

Скрипт workflow исполняется не здесь, а в клиенте, поэтому проверяется то,
что ломается молча: синтаксис, обязательные поля meta и имена инструментов.
Сценарий, зовущий несуществующий инструмент, падает уже в середине прогона,
когда половина агентов отработала.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
WORKFLOWS = ROOT / 'workflows'
SERVER = ROOT / 'app' / 'mcp_server.py'


def scripts():
    """Все сценарии проекта."""
    return sorted(WORKFLOWS.glob('*.js'))


class TestScripts:
    """Каждый сценарий рабочий."""

    def test_workflows_exist(self):
        assert scripts(), 'сценарии не найдены'

    @pytest.mark.skipif(shutil.which('node') is None, reason='нужен node')
    @pytest.mark.parametrize('path', scripts(), ids=lambda p: p.name)
    def test_syntax(self, path):
        """Синтаксис проверяет node: свой разбор JS был бы враньём."""
        result = subprocess.run(
            ['node', '--check', str(path)], capture_output=True, text=True,
            stdin=subprocess.DEVNULL
        )
        assert result.returncode == 0, result.stderr

    @pytest.mark.parametrize('path', scripts(), ids=lambda p: p.name)
    def test_meta_fields(self, path):
        """meta читается клиентом до запуска и должна быть литералом."""
        source = path.read_text(encoding='utf-8')

        assert source.lstrip().startswith('export const meta'), \
            'meta должна быть первой в файле'
        for field in ('name:', 'description:', 'phases:'):
            assert field in source, f'в meta нет {field}'

        name = re.search(r"name:\s*'([^']+)'", source)
        assert name and name.group(1) == path.stem, \
            'имя в meta должно совпадать с именем файла'

    @pytest.mark.parametrize('path', scripts(), ids=lambda p: p.name)
    def test_only_existing_tools_named(self, path):
        """Сценарий не должен звать инструмент, которого нет на сервере."""
        implemented = set(re.findall(
            r'(?:async\s+)?def\s+(dochub_\w+)\s*\(',
            SERVER.read_text(encoding='utf-8')
        ))
        named = set(re.findall(r'\bdochub_\w+', path.read_text(encoding='utf-8')))

        # Имя самого сценария инструментом не является
        named -= {path.stem.replace('-', '_')}

        assert named - implemented == set(), \
            f'нет таких инструментов: {sorted(named - implemented)}'

    @pytest.mark.parametrize('path', scripts(), ids=lambda p: p.name)
    def test_phases_declared(self, path):
        """Каждая phase() из тела объявлена в meta, иначе прогресс разъезжается."""
        source = path.read_text(encoding='utf-8')

        declared = set(re.findall(r"\{\s*title:\s*'([^']+)'", source))
        used = set(re.findall(r"phase\('([^']+)'\)", source))

        assert used - declared == set(), \
            f'фазы нет в meta: {sorted(used - declared)}'


class TestVersion:
    """Версия объявлена один раз."""

    def test_matches_changelog(self):
        """Иначе в архив уедет одна версия, а в описании релиза будет другая."""
        import re

        import app

        changelog = (ROOT / 'CHANGELOG.md').read_text(encoding='utf-8')
        latest = re.search(r'^## \[([0-9]+\.[0-9]+\.[0-9]+)\]', changelog, re.M)

        assert latest, 'в CHANGELOG нет ни одной версии'
        assert app.__version__ == latest.group(1)

    def test_not_duplicated_in_code(self):
        """Вторая копия версии в коде рано или поздно разъедется с первой."""
        import re

        hits = []
        for path in (ROOT / 'app').rglob('*.py'):
            if path.name == '__init__.py' and path.parent.name == 'app':
                continue
            for number, line in enumerate(
                    path.read_text(encoding='utf-8').splitlines(), 1):
                if re.search(r"__version__\s*=\s*['\"]", line):
                    hits.append(f'{path.name}:{number}')

        assert hits == [], 'версия объявлена ещё раз: ' + ', '.join(hits)

"""
Тесты синхронизации с архитектурным репозиторием.

Схема, собранная на устаревшем состоянии, расходится с чужими правками, и
узнаётся это только при push. Поэтому проверяется на настоящем git: временный
репозиторий с настоящим origin, а не заглушки.
"""

import subprocess

import pytest

from app.core.publisher import Publisher, PublishError
from app.utils.paths import git_command


def git(*args, cwd):
    """Выполнить git, не наследуя stdin теста.

    check=True намеренно: молча провалившаяся подготовка репозитория даёт
    непонятное падение проверки вместо понятной ошибки git.
    """
    return subprocess.run(
        [git_command(), *args], cwd=cwd, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, check=True
    )


def clone_of(origin, into, cwd):
    """Второй рабочий каталог того же репозитория — «коллега»."""
    git('clone', '-q', str(origin), str(into), cwd=cwd)
    git('config', 'user.email', 'other@example.com', cwd=into)
    git('config', 'user.name', 'other', cwd=into)
    return into


def commit_in(work, name: str, message: str):
    """Коммит в рабочем каталоге."""
    (work / name).write_text('components: {}\n', encoding='utf-8')
    git('add', '-A', cwd=work)
    git('commit', '-q', '-m', message, cwd=work)


@pytest.fixture
def repo(tmp_path):
    """Репозиторий с origin и одним коммитом в main."""
    origin = tmp_path / 'origin.git'
    origin.mkdir()
    # -b main: иначе HEAD пустого origin указывает на master, клон остаётся
    # без рабочей копии, и подготовка теста разваливается не там, где смотрят
    git('init', '--bare', '-q', '-b', 'main', cwd=origin)

    work = tmp_path / 'work'
    work.mkdir()
    git('init', '-q', '-b', 'main', cwd=work)
    git('config', 'user.email', 'test@example.com', cwd=work)
    git('config', 'user.name', 'test', cwd=work)
    (work / 'arch.yaml').write_text('components: {}\n', encoding='utf-8')
    git('add', '-A', cwd=work)
    git('commit', '-q', '-m', 'first', cwd=work)
    git('remote', 'add', 'origin', str(origin), cwd=work)
    git('push', '-q', '-u', 'origin', 'main', cwd=work)

    return work


class TestSync:
    """Осмотр репозитория перед работой."""

    def test_reachable_origin(self, repo):
        report = Publisher(repo).sync()
        assert report.reachable is True
        assert report.fetched is True
        assert report.unreachable_reason is None

    def test_empty_origin_still_reachable(self, tmp_path):
        """В пустом origin нет HEAD, но сам он доступен."""
        origin = tmp_path / 'bare.git'
        origin.mkdir()
        git('init', '--bare', '-q', '-b', 'main', cwd=origin)

        work = tmp_path / 'fresh'
        work.mkdir()
        git('init', '-q', '-b', 'main', cwd=work)
        git('config', 'user.email', 'test@example.com', cwd=work)
        git('config', 'user.name', 'test', cwd=work)
        (work / 'a.yaml').write_text('x: 1\n', encoding='utf-8')
        git('add', '-A', cwd=work)
        git('commit', '-q', '-m', 'first', cwd=work)
        git('remote', 'add', 'origin', str(origin), cwd=work)

        assert Publisher(work).sync().reachable is True

    def test_missing_origin_explained(self, tmp_path):
        work = tmp_path / 'lonely'
        work.mkdir()
        git('init', '-q', cwd=work)

        with pytest.raises(PublishError, match='origin'):
            Publisher(work).sync()

    def test_unreachable_origin_reported_not_raised(self, repo):
        """Недоступный origin — это состояние, а не крах: схему собрать можно."""
        git('remote', 'set-url', 'origin', str(repo / 'nowhere.git'), cwd=repo)

        report = Publisher(repo).sync()
        assert report.reachable is False
        assert report.unreachable_reason
        assert report.fetched is False

    def test_behind_counted(self, repo, tmp_path):
        """Чужие изменения в origin видны до начала работы."""
        other = clone_of(tmp_path / 'origin.git', tmp_path / 'other', tmp_path)
        commit_in(other, 'more.yaml', 'second')
        git('push', '-q', 'origin', 'HEAD:main', cwd=other)

        report = Publisher(repo).sync()
        assert report.behind == 1
        assert report.ahead == 0

    def test_update_fast_forwards(self, repo, tmp_path):
        other = clone_of(tmp_path / 'origin.git', tmp_path / 'other2', tmp_path)
        commit_in(other, 'more.yaml', 'second')
        git('push', '-q', 'origin', 'HEAD:main', cwd=other)

        report = Publisher(repo).sync(update=True)
        assert report.updated is True
        assert (repo / 'more.yaml').exists()

    def test_dirty_tree_blocks_update(self, repo):
        """Несохранённую работу пользователя обновление не трогает."""
        (repo / 'arch.yaml').write_text('components: {a: {}}\n', encoding='utf-8')

        report = Publisher(repo).sync(update=True)
        assert report.updated is False
        assert report.dirty_files == ['arch.yaml']
        assert any('несохранённые' in w for w in report.warnings)


class TestWorkBranch:
    """Рабочая ветка заводится до работы, а не в момент публикации."""

    def test_branch_created(self, repo):
        report = Publisher(repo).sync(branch='feature/orders')
        assert report.branch_created == 'feature/orders'
        assert Publisher(repo).current_branch() == 'feature/orders'

    def test_existing_branch_switched(self, repo):
        Publisher(repo).sync(branch='feature/orders')
        git('checkout', '-q', 'main', cwd=repo)

        report = Publisher(repo).sync(branch='feature/orders')
        assert report.branch_created is None
        assert report.branch_switched == 'feature/orders'

    def test_bad_branch_name_refused(self, repo):
        with pytest.raises(PublishError, match='Недопустимое имя ветки'):
            Publisher(repo).sync(branch='ветка с пробелом')


class TestProtocol:
    """Протокол origin виден до попытки push."""

    def test_https(self, repo):
        git('remote', 'set-url', 'origin', 'https://gitlab.example.com/g/p.git',
            cwd=repo)
        assert Publisher(repo).protocol() == 'https'

    def test_ssh(self, repo):
        git('remote', 'set-url', 'origin', 'git@gitlab.example.com:g/p.git', cwd=repo)
        assert Publisher(repo).protocol() == 'ssh'

"""
Тесты публикации: подтверждение, шаблон коммита и параметры merge request.

Публикация — единственная операция, которая выходит наружу и которую нельзя
откатить одним движением, поэтому проверяется и то, что она делает, и то,
чего она не делает без спроса.
"""

import asyncio
import json
import subprocess

import pytest

import app.mcp_server as server
from app.core import settings as settings_module
from app.core.publisher import Publisher
from app.utils.paths import git_command


def git(*args, cwd):
    return subprocess.run(
        [git_command(), *args], cwd=cwd, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, check=True
    )


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Настройки пользователя в тесты не попадают."""
    monkeypatch.setenv('DOCHUB_WORKSPACE', str(tmp_path / 'workspace.yaml'))
    monkeypatch.setenv('DOCHUB_ENV', str(tmp_path / '.env'))
    for key in settings_module.KEYS:
        monkeypatch.delenv(key, raising=False)
    return tmp_path


@pytest.fixture
def repo(tmp_path):
    """Репозиторий с одним коммитом и незакоммиченной правкой."""
    work = tmp_path / 'repo'
    work.mkdir()
    git('init', '-q', '-b', 'main', cwd=work)
    git('config', 'user.email', 'test@example.com', cwd=work)
    git('config', 'user.name', 'test', cwd=work)
    (work / 'arch.yaml').write_text('components: {}\n', encoding='utf-8')
    git('add', '-A', cwd=work)
    git('commit', '-q', '-m', 'first', cwd=work)

    (work / 'orders.yaml').write_text('components: {demo: {}}\n', encoding='utf-8')
    return work


def publish(**kwargs) -> dict:
    """Вызвать инструмент публикации и разобрать ответ."""
    params = server.PublishInput(**kwargs)
    return json.loads(asyncio.run(server.dochub_publish(params)))


class TestConfirmation:
    """Без согласия пользователя ничего не публикуется."""

    def test_refuses_without_confirmation(self, repo):
        answer = publish(repo_path=str(repo), branch='feature/orders',
                         message='проба', push=False)

        assert 'error' in answer
        assert 'подтверждения' in answer['error']
        assert Publisher(repo).current_branch() == 'main'

    def test_publishes_when_confirmed(self, repo):
        answer = publish(repo_path=str(repo), branch='feature/orders',
                         message='проба', confirmed=True, push=False)

        assert answer['committed'] is True
        assert answer['automode'] is False

    def test_automode_publishes_without_confirmation(self, repo, isolated):
        """Режим для тех, кто не разбирается: агент доводит работу сам."""
        (isolated / '.env').write_text('DOCHUB_AUTOMODE=true\n', encoding='utf-8')

        answer = publish(repo_path=str(repo), branch='feature/orders',
                         message='проба', push=False)

        assert answer['committed'] is True
        assert answer['automode'] is True
        assert any('automode' in w for w in answer['warnings'])

    def test_automode_does_not_merge(self, repo, isolated):
        """Мерж остаётся человеку в любом режиме."""
        (isolated / '.env').write_text('DOCHUB_AUTOMODE=true\n', encoding='utf-8')

        answer = publish(repo_path=str(repo), branch='feature/orders',
                         message='проба', push=False)

        assert answer['branch'] == 'feature/orders'
        assert Publisher(repo).current_branch() == 'feature/orders'


class TestCommitMessage:
    """Сообщение коммита единообразно во всём репозитории."""

    def test_built_from_template(self, repo, isolated):
        (isolated / '.env').write_text(
            'DOCHUB_COMMIT_TEMPLATE=Добавление схемы {schema}\n', encoding='utf-8')

        answer = publish(repo_path=str(repo), branch='feature/orders',
                         schema_name='OrderService', confirmed=True, push=False)

        assert answer['commit_message'] == 'Добавление схемы OrderService'
        last = subprocess.run(
            [git_command(), 'log', '-1', '--pretty=%s'], cwd=repo, capture_output=True,
            text=True, stdin=subprocess.DEVNULL, encoding='utf-8'
        ).stdout.strip()
        assert last == 'Добавление схемы OrderService'

    def test_explicit_message_wins(self, repo):
        answer = publish(repo_path=str(repo), branch='feature/orders',
                         message='ручное сообщение', schema_name='OrderService',
                         confirmed=True, push=False)

        assert answer['commit_message'] == 'ручное сообщение'

    def test_nothing_to_write_is_explained(self, repo):
        answer = publish(repo_path=str(repo), branch='feature/orders',
                         confirmed=True, push=False)

        assert 'error' in answer
        assert 'schema_name' in answer['error']


class TestMergeRequestOptions:
    """Параметры MR берутся из настроек и уходят в GitLab."""

    @pytest.fixture
    def sent(self, monkeypatch):
        """Перехваченное тело запроса к API."""
        captured = {}

        class FakeResponse:
            status_code = 201

            @staticmethod
            def json():
                return {'web_url': 'https://gitlab.example.com/mr/1'}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured['url'] = url
            captured['payload'] = json
            return FakeResponse()

        monkeypatch.setattr('app.core.publisher.httpx.post', fake_post)
        monkeypatch.setattr(
            Publisher, '_user_id',
            lambda self, token, username: {'ivanov': 7, 'petrov': 9}.get(username)
        )
        monkeypatch.setattr(Publisher, 'remote_url',
                            lambda self: 'https://gitlab.example.com/group/repo.git')
        monkeypatch.setattr(Publisher, '_git',
                            lambda self, *a, **kw: '' if a[0] == 'push' else 'ok')
        return captured

    def test_settings_reach_the_payload(self, repo, isolated, sent, monkeypatch):
        (isolated / '.env').write_text(
            'GITLAB_TOKEN=secret\n'
            'DOCHUB_TARGET_BRANCH=develop\n'
            'DOCHUB_MR_ASSIGNEE=ivanov\n'
            'DOCHUB_MR_REVIEWERS=petrov\n'
            'DOCHUB_MR_SQUASH=true\n'
            'DOCHUB_MR_REMOVE_SOURCE_BRANCH=false\n',
            encoding='utf-8'
        )
        monkeypatch.setenv('GITLAB_TOKEN', 'secret')

        publisher = Publisher(repo)
        current = settings_module.load()
        publisher.publish(
            branch='feature/orders', message='проба', files=['orders.yaml'],
            push=True, merge_request_title='Схема OrderService',
            target_branch=current.target_branch,
            assignee=current.mr_assignee, reviewers=current.reviewers,
            squash=current.mr_squash,
            remove_source_branch=current.mr_remove_source_branch
        )

        payload = sent['payload']
        assert payload['target_branch'] == 'develop'
        assert payload['assignee_id'] == 7
        assert payload['reviewer_ids'] == [9]
        assert payload['squash'] is True
        assert payload['remove_source_branch'] is False

    def test_unknown_reviewer_does_not_block_mr(self, repo, isolated, sent,
                                                monkeypatch):
        """Опечатка в имени не должна стоить merge request."""
        monkeypatch.setenv('GITLAB_TOKEN', 'secret')

        result = Publisher(repo).publish(
            branch='feature/orders', message='проба', files=['orders.yaml'],
            push=True, merge_request_title='Схема',
            reviewers=['несуществующий']
        )

        assert result.merge_request_url
        assert any('несуществующий' in w for w in result.warnings)
        assert 'reviewer_ids' not in sent['payload']


class TestGitLabAddress:
    """Адрес API выводится из origin, и не из чего попало."""

    @staticmethod
    def _with_remote(repo, url, monkeypatch):
        from app.core.publisher import Publisher

        monkeypatch.setattr(Publisher, 'remote_url', lambda self: url)
        return Publisher(repo)

    def test_https_remote(self, repo, monkeypatch):
        publisher = self._with_remote(
            repo, 'https://gitlab.example.com/group/sub/repo.git', monkeypatch)

        assert publisher._api_base() == 'https://gitlab.example.com/api/v4'
        assert publisher._project_path() == 'group/sub/repo'

    def test_ssh_remote(self, repo, monkeypatch):
        publisher = self._with_remote(
            repo, 'git@gitlab.example.com:group/sub/repo.git', monkeypatch)

        assert publisher._api_base() == 'https://gitlab.example.com/api/v4'
        assert publisher._project_path() == 'group/sub/repo'

    def test_local_origin_explained(self, repo, monkeypatch):
        """Локальная папка как origin — рабочий случай для проверок, но не GitLab."""
        from app.core.publisher import PublishError

        publisher = self._with_remote(repo, 'C:/Temp/origin.git', monkeypatch)

        with pytest.raises(PublishError, match='не в GitLab'):
            publisher._api_base()

    def test_local_origin_does_not_break_push(self, repo, monkeypatch):
        """Ветка и коммит при локальном origin должны проходить как обычно."""
        answer = publish(repo_path=str(repo), branch='feature/orders',
                         message='проба', confirmed=True, push=False)

        assert answer['committed'] is True

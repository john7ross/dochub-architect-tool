"""
Тесты настроек сервера.

Настройки приходят из трёх мест сразу, и важен не столько разбор файла,
сколько порядок: переменная окружения должна перебивать .env, а .env —
сохранённые пути. Ошибка здесь приводит к работе не с тем репозиторием.
"""

import pytest

from app.core import settings as settings_module


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Никаких настроек пользователя в тестах."""
    monkeypatch.setenv('DOCHUB_WORKSPACE', str(tmp_path / 'workspace.yaml'))
    monkeypatch.setenv('DOCHUB_ENV', str(tmp_path / '.env'))
    for key in settings_module.KEYS:
        monkeypatch.delenv(key, raising=False)
    return tmp_path


def write_env(path, text: str):
    (path / '.env').write_text(text, encoding='utf-8')


class TestParseEnv:
    """Разбор .env."""

    def test_basic_pairs(self):
        values = settings_module.parse_env('A=1\nB=два\n')
        assert values == {'A': '1', 'B': 'два'}

    def test_comments_and_blanks(self):
        values = settings_module.parse_env('# коммент\n\nA=1\n   \n#B=2\n')
        assert values == {'A': '1'}

    def test_export_prefix(self):
        assert settings_module.parse_env('export A=1\n') == {'A': '1'}

    def test_quotes_kept_inside(self):
        values = settings_module.parse_env('A="Добавление схемы {schema}"\n')
        assert values['A'] == 'Добавление схемы {schema}'

    def test_equals_inside_value(self):
        values = settings_module.parse_env('URL=https://host/x?a=b\n')
        assert values['URL'] == 'https://host/x?a=b'

    def test_trailing_comment_only_without_quotes(self):
        values = settings_module.parse_env('A=1 # хвост\nB="2 # не хвост"\n')
        assert values['A'] == '1'
        assert values['B'] == '2 # не хвост'

    def test_bom_ignored(self):
        assert settings_module.parse_env('﻿A=1\n') == {'A': '1'}


class TestPriority:
    """Порядок источников."""

    def test_env_file_read(self, isolated):
        write_env(isolated, 'DOCHUB_TARGET_BRANCH=develop\n')
        assert settings_module.load().target_branch == 'develop'

    def test_environment_wins_over_file(self, isolated, monkeypatch):
        write_env(isolated, 'DOCHUB_TARGET_BRANCH=develop\n')
        monkeypatch.setenv('DOCHUB_TARGET_BRANCH', 'release')

        current = settings_module.load()
        assert current.target_branch == 'release'
        assert current.sources['target_branch'] == 'переменная окружения'

    def test_env_file_wins_over_saved_paths(self, isolated):
        from app.core import workspace

        saved, from_env = isolated / 'saved', isolated / 'from_env'
        saved.mkdir()
        from_env.mkdir()

        workspace.save({'repo_root': str(saved)})
        write_env(isolated, f'DOCHUB_REPO_ROOT={from_env.as_posix()}\n')

        current = settings_module.load()
        assert current.repo_root == str(from_env)
        assert current.sources['repo_root'] == '.env'

    def test_saved_paths_used_when_env_silent(self, isolated):
        from app.core import workspace

        saved = isolated / 'saved'
        saved.mkdir()
        workspace.save({'repo_root': str(saved)})

        current = settings_module.load()
        assert current.repo_root == str(saved)
        assert current.sources['repo_root'] == 'dochub-workspace.yaml'

    def test_defaults_without_any_source(self, isolated):
        current = settings_module.load()
        assert current.target_branch == 'main'
        assert current.automode is False
        assert current.commit_template == 'Добавление схемы {schema}'


class TestValues:
    """Значения, в которых легко ошибиться."""

    @pytest.mark.parametrize('word', ['1', 'true', 'yes', 'on', 'да', 'ВКЛ'])
    def test_true_words(self, isolated, word):
        write_env(isolated, f'DOCHUB_AUTOMODE={word}\n')
        assert settings_module.load().automode is True

    @pytest.mark.parametrize('word', ['0', 'false', 'no', 'off', 'нет'])
    def test_false_words(self, isolated, word):
        write_env(isolated, f'DOCHUB_AUTOMODE={word}\n')
        assert settings_module.load().automode is False

    def test_unclear_flag_is_reported_not_guessed(self, isolated):
        """Молча включённый automode опубликовал бы схему без спроса."""
        write_env(isolated, 'DOCHUB_AUTOMODE=иногда\n')

        current = settings_module.load()
        assert current.automode is False
        assert any('DOCHUB_AUTOMODE' in w for w in current.warnings)

    def test_unknown_protocol_falls_back(self, isolated):
        write_env(isolated, 'DOCHUB_REMOTE_PROTOCOL=ftp\n')

        current = settings_module.load()
        assert current.remote_protocol == 'https'
        assert any('DOCHUB_REMOTE_PROTOCOL' in w for w in current.warnings)

    def test_reviewers_split(self, isolated):
        write_env(isolated, 'DOCHUB_MR_REVIEWERS=ivanov, petrov ,\n')
        assert settings_module.load().reviewers == ['ivanov', 'petrov']

    def test_commit_message_from_template(self, isolated):
        write_env(isolated, 'DOCHUB_COMMIT_TEMPLATE=feat: схема {schema}\n')
        assert settings_module.load().commit_message('OrderService') == \
            'feat: схема OrderService'

    def test_broken_template_does_not_block_publication(self, isolated):
        """Шаблон правит человек, а публикация не должна из-за этого встать."""
        write_env(isolated, 'DOCHUB_COMMIT_TEMPLATE=схема {название}\n')

        current = settings_module.load()
        message = current.commit_message('OrderService')

        assert message == 'Добавление схемы OrderService'
        assert any('шаблон коммита' in w for w in current.warnings)

    def test_missing_paths_named(self, isolated):
        assert set(settings_module.load().missing_paths) == {
            'repo_root', 'manifest_path', 'ddd_path'
        }

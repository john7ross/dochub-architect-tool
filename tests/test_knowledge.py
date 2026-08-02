"""
Тесты накопления знаний.

Смысл механизма — чтобы пользователь не объяснял одно и то же на каждой
схеме. Значит, важны две вещи: повторы не должны плодить дубли, а мусор
не должен попадать в хранилище.
"""

import pytest

from app.core.knowledge import CATEGORIES, KnowledgeStore


@pytest.fixture
def store(tmp_path):
    """Пустое хранилище во временном каталоге."""
    return KnowledgeStore(tmp_path / 'lessons.yaml')


class TestRecording:
    """Запись уроков."""

    def test_empty_store_reads_as_empty(self, store):
        assert store.load() == []

    def test_records_lesson(self, store):
        result = store.record(
            [{'rule': 'Очереди заводим у владельца шины', 'category': 'ownership'}],
            source='serviceA'
        )
        assert result.added
        assert len(store.load()) == 1

    def test_rejects_empty_rule(self, store):
        result = store.record([{'rule': '   ', 'category': 'naming'}])
        assert result.rejected
        assert store.load() == []

    def test_rejects_unknown_category(self, store):
        """Свободные категории превращают файл в свалку."""
        result = store.record([{'rule': 'Правило', 'category': 'придумал'}])
        assert result.rejected
        assert store.load() == []

    def test_all_categories_accepted(self, store):
        result = store.record([
            {'rule': f'Правило про {name}', 'category': name}
            for name in CATEGORIES
        ])
        assert len(result.added) == len(CATEGORIES)


class TestDeduplication:
    """Повтор не должен плодить дубли."""

    def test_same_rule_merged(self, store):
        store.record([{'rule': 'Аббревиатуры пишем как Api', 'category': 'naming'}],
                     source='serviceA')
        result = store.record(
            [{'rule': 'Аббревиатуры пишем как Api', 'category': 'naming'}],
            source='serviceB'
        )
        assert result.merged
        assert len(store.load()) == 1

    def test_reworded_rule_merged(self, store):
        """Формулировки отличаются, смысл тот же."""
        store.record([{'rule': 'Очереди шины заводить в eventBus.yaml',
                       'category': 'ownership'}], source='serviceA')
        result = store.record([{'rule': 'Очереди шины заводим в eventBus.yaml',
                                'category': 'ownership'}], source='serviceB')
        assert result.merged
        assert len(store.load()) == 1

    def test_confirmation_counter_grows(self, store):
        store.record([{'rule': 'Правило про очереди', 'category': 'ownership'}],
                     source='serviceA')
        store.record([{'rule': 'Правило про очереди', 'category': 'ownership'}],
                     source='serviceB')

        lesson = store.load()[0]
        assert lesson.seen == 2
        # По источникам видно, на скольких схемах правило подтвердилось
        assert 'serviceA' in lesson.source
        assert 'serviceB' in lesson.source

    def test_different_rules_kept_apart(self, store):
        store.record([
            {'rule': 'Очереди заводим у владельца шины', 'category': 'ownership'},
            {'rule': 'DTO на схему не выносим', 'category': 'inclusion'},
        ])
        assert len(store.load()) == 2


class TestReading:
    """Чтение и поиск."""

    @pytest.fixture
    def filled(self, store):
        store.record([
            {'rule': 'Аббревиатуры пишем как Api', 'category': 'naming'},
            {'rule': 'Очереди заводим у владельца шины', 'category': 'ownership',
             'rationale': 'владелец — поддомен domainConnect'},
            {'rule': 'DTO и Entities на схему не выносим', 'category': 'inclusion'},
        ], source='serviceA')
        return store

    def test_filter_by_category(self, filled):
        found = filled.search(category='ownership')
        assert len(found) == 1
        assert 'Очереди' in found[0].rule

    def test_search_by_substring(self, filled):
        assert filled.search('DTO')
        assert not filled.search('такого правила нет')

    def test_search_covers_rationale(self, filled):
        assert filled.search('domainConnect')

    def test_markdown_groups_by_category(self, filled):
        text = filled.as_markdown()
        assert CATEGORIES['naming'] in text
        assert CATEGORIES['ownership'] in text
        assert 'Аббревиатуры' in text

    def test_markdown_marks_confirmations(self, filled):
        filled.record([{'rule': 'Аббревиатуры пишем как Api', 'category': 'naming'}],
                      source='serviceB')
        assert '2×' in filled.as_markdown()

    def test_empty_store_explains_itself(self, store):
        """Пустое хранилище не должно выглядеть как поломка."""
        text = store.as_markdown()
        assert 'пока нет' in text


class TestPersistence:
    """Хранилище переживает перезапуск."""

    def test_survives_reload(self, tmp_path):
        path = tmp_path / 'lessons.yaml'
        KnowledgeStore(path).record(
            [{'rule': 'Правило про именование', 'category': 'naming'}],
            source='serviceA'
        )
        assert len(KnowledgeStore(path).load()) == 1

    def test_broken_file_does_not_crash(self, tmp_path):
        """Битый файл не должен ронять работу над схемой."""
        path = tmp_path / 'lessons.yaml'
        path.write_text('lessons: [{unclosed', encoding='utf-8')
        assert KnowledgeStore(path).load() == []


class TestWorkspace:
    """Постоянные пути задаются один раз."""

    @pytest.fixture
    def config(self, tmp_path):
        return tmp_path / 'workspace.yaml'

    def test_empty_reports_what_is_missing(self, config):
        from app.core.workspace import load

        workspace = load(str(config))
        assert set(workspace.missing) == {'repo_root', 'manifest_path', 'ddd_path'}

    def test_saves_and_reads_back(self, config, tmp_path):
        from app.core.workspace import load, save

        repo = tmp_path / 'repo'
        repo.mkdir()
        save({'repo_root': str(repo)}, str(config))

        assert load(str(config)).repo_root == str(repo)

    def test_manifest_derived_from_repo(self, config, tmp_path):
        """Манифест лежит по соглашению, спрашивать его отдельно незачем."""
        from app.core.workspace import load, save

        manifest = tmp_path / 'repo' / 'architecture' / 'dochub.yaml'
        manifest.parent.mkdir(parents=True)
        manifest.write_text('imports: []\n', encoding='utf-8')

        save({'repo_root': str(tmp_path / 'repo')}, str(config))
        assert load(str(config)).manifest_path == str(manifest)

    def test_partial_update_keeps_the_rest(self, config, tmp_path):
        from app.core.workspace import load, save

        repo, ddd = tmp_path / 'repo', tmp_path / 'ddd.drawio'
        repo.mkdir()
        ddd.write_text('<mxfile/>', encoding='utf-8')

        save({'repo_root': str(repo)}, str(config))
        save({'ddd_path': str(ddd)}, str(config))

        workspace = load(str(config))
        assert workspace.repo_root == str(repo)
        assert workspace.ddd_path == str(ddd)

    def test_warns_about_missing_path(self, config, tmp_path):
        """Путь задан, но каталога нет — молчать об этом нельзя."""
        from app.core.workspace import load, save

        save({'repo_root': str(tmp_path / 'нет-такого')}, str(config))
        assert load(str(config)).warnings

    def test_given_value_wins(self, config, tmp_path):
        """Явно переданный путь важнее настроек."""
        from app.core.workspace import resolve, save

        repo = tmp_path / 'repo'
        repo.mkdir()
        save({'repo_root': str(repo)}, str(config))

        assert resolve('repo_root', '/явно/указанный') == '/явно/указанный'

    def test_broken_file_does_not_crash(self, config):
        from app.core.workspace import load

        config.write_text('repo_root: [незакрытая', encoding='utf-8')
        workspace = load(str(config))
        assert workspace.warnings
        assert workspace.repo_root is None

"""
Тесты конвейера DocHub: манифест, рендеринг, регистрация, сверка с кодом.

Часть проверок требует настоящего архитектурного репозитория. Если его нет,
такие тесты пропускаются: на чужой машине репозиторий лежит в другом месте.
"""

import os
from pathlib import Path

import pytest
import yaml

from app.core.dochub.manifest import ManifestLoader, merge_deep
from app.core.dochub.native_renderer import DocHubNativeRenderer
from app.core.dochub.registration import RegistrationChecker
from app.core.dochub.registrar import OwnerIndex, Registrar
from app.core.parsers.base_parser import Component
from app.core.publisher import Publisher, PublishError
from app.core.reconcile import Reconciler
from app.utils.paths import git_command

PROJECT_ROOT = Path(__file__).parent.parent

# Репозиторий можно указать переменной окружения
def _find_repo() -> Path:
    """
    Найти архитектурный репозиторий для тестов.

    Путь можно задать переменной DOCHUB_TEST_REPO. Иначе проверяются
    привычные места: рядом с проектом и внутри него.
    """
    override = os.environ.get('DOCHUB_TEST_REPO')
    if override:
        return Path(override)

    for candidate in (
        PROJECT_ROOT.parent / 'architectural-repository',
        PROJECT_ROOT / 'architectural-repository',
    ):
        if (candidate / 'architecture' / 'dochub.yaml').exists():
            return candidate

    return PROJECT_ROOT / 'architectural-repository'


REPO = _find_repo()
MANIFEST = REPO / 'architecture' / 'dochub.yaml'
# Конкретный поддомен и контекст зависят от репозитория — задаются окружением
SUBDOMAIN = REPO / os.environ.get(
    'DOCHUB_TEST_SUBDOMAIN', 'architecture/domain/example/context/subdomain.yaml'
)
CONTEXT_ID = os.environ.get('DOCHUB_TEST_CONTEXT', 'example.context.subdomain.process')
OWNER_FILE = os.environ.get(
    'DOCHUB_TEST_OWNER_FILE', 'architecture/domain/example/context/api.yaml'
)
OWNER_COMPONENT = os.environ.get('DOCHUB_TEST_OWNER_COMPONENT', 'dotnet.exampleApi')

# Часть проверок опирается на конкретный поддомен и контекст, а они у
# каждого репозитория свои. Без настройки такие тесты пропускаются, иначе
# они падали бы на чужом репозитории по несуществующим путям.
needs_repo = pytest.mark.skipif(
    not MANIFEST.exists(),
    reason='нужен архитектурный репозиторий (DOCHUB_TEST_REPO)'
)

needs_fixtures = pytest.mark.skipif(
    # Проверяются все пути, а не два из восьми: при частичной настройке тест
    # падал по несуществующему пути-заглушке вместо честного пропуска
    not (MANIFEST.exists() and SUBDOMAIN.exists()
         and (REPO / OWNER_FILE).exists()),
    reason='нужны DOCHUB_TEST_SUBDOMAIN, DOCHUB_TEST_CONTEXT, '
           'DOCHUB_TEST_OWNER_FILE и остальные пути'
)


class TestMergeDeep:
    """Слияние секций между файлами репозитория."""

    def test_merges_nested(self):
        target = {'components': {'a': {'title': 'A'}}}
        merge_deep(target, {'components': {'b': {'title': 'B'}}})
        assert set(target['components']) == {'a', 'b'}

    def test_later_wins(self):
        target = {'components': {'a': {'title': 'старое'}}}
        merge_deep(target, {'components': {'a': {'title': 'новое'}}})
        assert target['components']['a']['title'] == 'новое'


@needs_repo
class TestManifest:
    """Сборка манифеста разворачивает imports рекурсивно."""

    def test_loads_whole_repository(self):
        manifest = ManifestLoader().load(MANIFEST)
        # Один файл поддомена дал бы десятки, а не сотни
        assert len(manifest['components']) > 500
        assert len(manifest['contexts']) > 100
        assert len(manifest['aspects']) > 500

    def test_resolves_cross_file_references(self):
        """Компоненты соседних файлов видны — иначе связи не разрешаются."""
        manifest = ManifestLoader().load(MANIFEST)
        assert manifest['components']


@needs_fixtures
class TestNativeRenderer:
    """Рендеринг идёт метамоделью самого DocHub."""

    def test_renders_context_with_regions(self):
        manifest = ManifestLoader().load(MANIFEST)
        renderer = DocHubNativeRenderer()
        puml = renderer.render_context(
            manifest,
            CONTEXT_ID,
            render_core='smetana'
        )
        # Вложенные области и блоки с аспектами — то, чего не давал
        # самописный рендерер
        assert '$Region(' in puml
        assert '$Entity(' in puml
        assert '$EntityAspect(' in puml

    def test_unknown_context_reported(self):
        from app.core.dochub.native_renderer import DocHubRenderError

        manifest = ManifestLoader().load(MANIFEST)
        with pytest.raises(DocHubRenderError):
            DocHubNativeRenderer().render_context(manifest, 'нет.такого.контекста')


@needs_fixtures
class TestRegistrationChecker:
    """Проверка подключения схемы к дереву."""

    def test_no_false_positives_on_real_file(self):
        """Рукописный файл уже корректен — проверка не должна ругаться."""
        report = RegistrationChecker(REPO).check(SUBDOMAIN)
        assert report.imported is True
        assert report.ok is True

    def test_detects_missing_registration(self, tmp_path):
        """Неподключённый файл с чужими корнями ловится целиком."""
        probe = SUBDOMAIN.parent / '_pytest_probe.yaml'
        probe.write_text(yaml.safe_dump({
            'aspects': {
                'example.context.probe.feature': {
                    'title': 'Проба', 'location': 'x'
                }
            },
            'components': {
                'rustlang.probeService': {'title': 'P', 'entity': 'component'},
                'rustlang.probeService.feature': {
                    'title': 'F', 'entity': 'component',
                    'aspects': ['example.context.probe.feature']
                },
            },
            'contexts': {
                'example.context.probe.process': {
                    'title': 'П',
                    'components': [
                        'rustlang.probeService.feature',
                        'externalServices.unknownPartner',
                    ],
                }
            },
        }, allow_unicode=True), encoding='utf-8')

        try:
            report = RegistrationChecker(REPO).check(probe)

            assert report.imported is False
            assert [m.id for m in report.missing_roots] == ['rustlang']
            assert any(
                m.id == 'externalServices.unknownPartner'
                for m in report.missing_externals
            )
            assert 'rustlang.probeService' in report.orphan_components
            assert report.ok is False
        finally:
            probe.unlink(missing_ok=True)


@pytest.fixture(scope='module')
def index():
    """Указатель владельцев, строится один раз."""
    return OwnerIndex(REPO)


@needs_fixtures
class TestOwnerIndex:
    """Определение владельца компонента."""

    def test_finds_owner_by_prefix(self, index):
        """Новая очередь пишется в файл владельца шины событий."""
        found = index.locate(os.environ.get('DOCHUB_TEST_QUEUE', 'dotnet.exampleBus.someNewQueue'))
        assert found['file']
        assert found['exists'] is False
        assert found['l2_component']

    def test_existing_component_marked(self, index):
        found = index.locate(OWNER_COMPONENT)
        assert found['exists'] in (True, False)

    def test_platform_root_is_not_an_owner(self, index):
        """Совпадение по одному сегменту — платформа, она ничем не владеет.

        Иначе всё уезжает в common/general/root.yaml.
        """
        found = index.locate('dotnet.brandNewServiceNobodyKnows.feature')
        assert found['file'] is None


@needs_fixtures
class TestRegistrar:
    """Планирование правок в общих и чужих файлах."""

    def test_plan_skips_existing(self):
        registrar = Registrar(REPO)
        plan = registrar.plan_roots({
            'dotnet': {'title': 'уже есть', 'entity': 'component'},
            'rustlang': {'title': 'Rust', 'entity': 'component'},
        })
        assert [c.target for c in plan.changes] == ['rustlang']

    def test_foreign_plan_marks_foreign(self):
        """Правки в чужом файле помечаются, чтобы агент предупредил."""
        registrar = Registrar(REPO)
        plan = registrar.plan_foreign_component(
            owner_file=OWNER_FILE,
            component_id=f'{OWNER_COMPONENT}.pytestProbeGet',
            title='/api/probe',
            entity='component',
            aspect_id=os.environ.get('DOCHUB_TEST_OWNER_ASPECT', 'example.context.api.pytestProbeGet'),
            aspect_title='Проба',
            aspect_location='x/y',
            owner_component=OWNER_COMPONENT,
            owner_context=os.environ.get('DOCHUB_TEST_OWNER_CONTEXT', 'example.context.api'),
        )
        assert plan.touches_foreign is True
        actions = {c.action for c in plan.changes}
        # Именно этот набор правок делается руками при добавлении метода
        assert actions == {
            'add_aspect', 'add_component',
            'link_aspect_to_component', 'add_to_context',
        }

    def test_plan_does_not_write(self):
        """Планирование ничего не меняет на диске."""
        before = (REPO / 'architecture/common/general/root.yaml').read_bytes()
        Registrar(REPO).plan_roots({'rustlang': {'title': 'R'}})
        after = (REPO / 'architecture/common/general/root.yaml').read_bytes()
        assert before == after


class TestPublisher:
    """Ветка, коммит и разбор состояния репозитория."""

    @pytest.fixture
    def repo(self, tmp_path):
        import subprocess

        def git(*args):
            subprocess.run([git_command(), *args], cwd=tmp_path, capture_output=True,
                           check=True)

        git('init', '-q')
        git('config', 'user.email', 'test@example.com')
        git('config', 'user.name', 'Test')
        (tmp_path / 'a.yaml').write_text('a: 1\n', encoding='utf-8')
        git('add', '.')
        git('commit', '-qm', 'init')
        return tmp_path

    def test_pending_files_keeps_full_name(self, repo):
        """Разбор git status не должен срезать первый символ имени."""
        (repo / 'a.yaml').write_text('a: 2\n', encoding='utf-8')
        (repo / 'new.yaml').write_text('b: 3\n', encoding='utf-8')

        assert set(Publisher(repo).pending_files()) == {'a.yaml', 'new.yaml'}

    def test_commit_creates_branch(self, repo):
        (repo / 'a.yaml').write_text('a: 2\n', encoding='utf-8')
        result = Publisher(repo).publish(
            branch='feature/probe', message='test', push=False
        )
        assert result.committed is True
        assert result.pushed is False
        assert Publisher(repo).current_branch() == 'feature/probe'

    def test_rejects_bad_branch_name(self, repo):
        (repo / 'a.yaml').write_text('a: 2\n', encoding='utf-8')
        with pytest.raises(PublishError):
            Publisher(repo).publish(branch='плохая ветка!', message='x', push=False)

    def test_rejects_empty_commit(self, repo):
        with pytest.raises(PublishError):
            Publisher(repo).publish(branch='feature/empty', message='x', push=False)


@pytest.fixture(scope='module')
def reconciler():
    """Сверщик по коду самого приложения."""
    return Reconciler(PROJECT_ROOT / 'app')


class TestReconciler:
    """Сверка схемы с кодом. Ничего не решает — только показывает."""

    def test_exact_match(self, reconciler):
        report = reconciler.reconcile([
            Component(id='1', title='PreviewRenderer', type='Component')
        ])
        assert 'PreviewRenderer' in report.matched

    def test_case_mismatch_cites_code(self, reconciler):
        report = reconciler.reconcile([
            Component(id='1', title='previewrenderer', type='Component')
        ])
        found = [d for d in report.discrepancies if d.kind == 'case_mismatch']
        assert found
        assert found[0].code_locations

    def test_typo_detected(self, reconciler):
        report = reconciler.reconcile([
            Component(id='1', title='ManifestLoadr', type='Component')
        ])
        found = [d for d in report.discrepancies if d.kind == 'name_mismatch']
        assert found
        assert 'ManifestLoader' in found[0].detail

    def test_absent_in_code_reported(self, reconciler):
        report = reconciler.reconcile([
            Component(id='1', title='NoSuchServiceAnywhere', type='Component')
        ])
        kinds = {d.kind for d in report.discrepancies}
        assert 'on_schema_not_in_code' in kinds

    def test_boundaries_ignored(self, reconciler):
        """Рамки — не классы, сверять их с кодом бессмысленно."""
        report = reconciler.reconcile([
            Component(id='1', title='Infrastructure', type='Container_Boundary',
                      is_boundary=True)
        ])
        assert not [
            d for d in report.discrepancies
            if d.kind == 'on_schema_not_in_code' and d.name == 'Infrastructure'
        ]


class TestCheckRegistrationTool:
    """Ответ инструмента, когда корневой манифест не найден."""

    def test_missing_manifest_is_named(self, tmp_path):
        """Пустой отчёт не отличить от «файл просто не подключён»."""
        import asyncio
        import json

        import app.mcp_server as server_module

        subdomain = tmp_path / 'sub.yaml'
        subdomain.write_text('components: {}\n', encoding='utf-8')

        params = server_module.RegistrationInput(
            repo_root=str(tmp_path), yaml_path=str(subdomain)
        )
        answer = json.loads(
            asyncio.run(server_module.dochub_check_registration(params))
        )

        assert 'error' in answer
        assert 'dochub.yaml' in answer['error']


class TestSearchComponents:
    """Поиск по нескольким названиям сразу."""

    @staticmethod
    def _manifest(tmp_path):
        """Маленький репозиторий из одного файла."""
        path = tmp_path / 'dochub.yaml'
        path.write_text(yaml.safe_dump({
            'components': {
                'dotnet.orderService': {'title': 'OrderService', 'entity': 'component'},
                'dotnet.catalogApi': {'title': 'Catalog API', 'entity': 'component'},
                'mssql.orders': {'title': 'Orders DB', 'entity': 'database'},
            }
        }, allow_unicode=True), encoding='utf-8')
        return path

    def _search(self, tmp_path, **kwargs):
        import asyncio
        import json

        import app.mcp_server as server_module

        params = server_module.SearchComponentsInput(
            manifest_path=str(self._manifest(tmp_path)), **kwargs
        )
        return json.loads(
            asyncio.run(server_module.dochub_search_components(params))
        )

    def test_single_query_shape_kept(self, tmp_path):
        """Старый вызов отвечает как раньше: его ждут скилл и документация."""
        answer = self._search(tmp_path, query='catalog')

        assert answer['total'] == 1
        assert answer['components'][0]['id'] == 'dotnet.catalogApi'
        assert 'results' not in answer

    def test_many_queries_one_manifest(self, tmp_path):
        """Список названий со схемы проверяется одним вызовом."""
        answer = self._search(
            tmp_path, queries=['catalog', 'orderService', 'нет такого']
        )

        assert answer['queries'] == 3
        assert answer['with_matches'] == 2
        assert [r['query'] for r in answer['results']] == \
            ['catalog', 'orderService', 'нет такого']
        assert answer['results'][2]['components'] == []

    def test_order_preserved(self, tmp_path):
        """Агент сопоставляет ответ со своим списком по порядку."""
        queries = ['mssql', 'catalog', 'orderService']
        answer = self._search(tmp_path, queries=queries)

        assert [r['query'] for r in answer['results']] == queries

    def test_short_and_empty_dropped(self, tmp_path):
        """Односимвольные обрывки названий совпадут со всем подряд."""
        answer = self._search(tmp_path, queries=['catalog', '', 'a', '  '])

        assert answer['queries'] == 1

    def test_nothing_to_search_explained(self, tmp_path):
        answer = self._search(tmp_path)

        assert 'error' in answer
        assert 'queries' in answer['error']


class TestRegistrationPlanNamesSubdomains:
    """План называет поддомены, а не только количество правок."""

    def test_own_and_foreign_split(self, tmp_path):
        import asyncio
        import json

        import app.mcp_server as server_module
        from app.core.dochub.registrar import Change, Plan

        plan = Plan()
        plan.changes = [
            Change(file='architecture/domain/sales/orders/orderFlow.yaml',
                   action='add', target='dotnet.orderService'),
            Change(file='architecture/common/general/root.yaml',
                   action='add', target='dotnet', foreign=True),
            Change(file='architecture/domain/bus/eventGateway.yaml',
                   action='add', target='dotnet.eventBus.queue', foreign=True),
        ]

        own = sorted({c.file for c in plan.changes if not c.foreign})
        foreign = sorted({c.file for c in plan.changes if c.foreign})

        assert own == ['architecture/domain/sales/orders/orderFlow.yaml']
        assert len(foreign) == 2
        assert plan.touches_foreign is True

    def test_warning_names_foreign_files(self, tmp_path, monkeypatch):
        """Пользователю нужен список файлов, а не «есть правки в чужом»."""
        import asyncio
        import json

        import app.mcp_server as server_module
        from app.core.dochub.registrar import Change, Plan, Registrar

        repo = tmp_path / 'repo'
        (repo / 'architecture').mkdir(parents=True)
        (repo / 'architecture' / 'dochub.yaml').write_text(
            'imports: []\n', encoding='utf-8')
        subdomain = repo / 'architecture' / 'sub.yaml'
        subdomain.write_text('components: {}\n', encoding='utf-8')

        def fake_import(self, yaml_path):
            plan = Plan()
            plan.changes = [
                Change(file='architecture/sub.yaml', action='add', target='свой'),
                Change(file='architecture/common/external/root.yaml',
                       action='add', target='externalServices.чужой', foreign=True),
            ]
            return plan

        monkeypatch.setattr(Registrar, 'plan_import', fake_import)

        params = server_module.RegisterSchemaInput(
            repo_root=str(repo), yaml_path=str(subdomain), apply=False
        )
        answer = json.loads(
            asyncio.run(server_module.dochub_register_schema(params))
        )

        assert answer['own_subdomains'] == ['architecture/sub.yaml']
        assert answer['foreign_subdomains'] == \
            ['architecture/common/external/root.yaml']
        assert 'architecture/common/external/root.yaml' in answer['warning']

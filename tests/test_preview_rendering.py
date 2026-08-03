"""
Тесты отрисовки схем и офлайн-отката.

Схемы компаний почти всегда подключают настройки и иконки с внутренних
серверов. Недоступность такого сервера не должна ронять рендер: оформление
теряется, структура остаётся. Здесь закреплено поведение отката — в нём
уже была регрессия, из-за которой перестали рисоваться 12 схем.
"""

import shutil
from pathlib import Path

import pytest

from app.core.parsers.geometry import contains, find_innermost, get_geometry
from app.preview.renderer import (
    PreviewError,
    PreviewRenderer,
    _stub_external_includes,
    _undefined_vars,
)
from app.utils.paths import PLANTUML_JAR

needs_plantuml = pytest.mark.skipif(
    not PLANTUML_JAR.exists() or shutil.which('java') is None,
    reason='нужны Java и lib/plantuml.jar'
)

SELF_CONTAINED = """@startuml
!include <C4/C4_Container>
Container(svc, "Сервис", "C#", "Отправка заказов")
ContainerDb(db, "База", "PostgreSQL", "Хранение")
Rel(svc, db, "EF Core")
@enduml
"""

WITH_EXTERNAL = """@startuml probe
!global $SETTINGS_PATH = "https://unreachable.invalid/settings.iuml"
!include $SETTINGS_PATH
!define DEVICONS https://unreachable.invalid/icons
!include DEVICONS/dotnetcore.puml
title [[$LINK_PATH/x.puml Заголовок]]
Component(a, "A", "C#", "Описание")
Component(b, "B", "C#", "Описание")
a --> b
@enduml
"""


class TestStubExternalIncludes:
    """Отключение недоступных подключений."""

    def test_external_directives_commented(self):
        body, stubbed = _stub_external_includes(WITH_EXTERNAL)
        assert stubbed >= 3
        assert 'unreachable.invalid' not in body.replace(
            "' [отключено при офлайн-рендере]", 'X'
        ).split('X')[0]

    def test_c4_library_injected(self):
        """Макросы C4 приходили из отключённого файла — берём из stdlib.

        Нужен именно C4_Component: Component() в C4_Container не определён.
        """
        body, _ = _stub_external_includes(WITH_EXTERNAL)
        assert '!include <C4/C4_Component>' in body

    def test_undefined_variables_declared(self):
        """Без объявления $LINK_PATH PlantUML падает с Syntax Error."""
        body, _ = _stub_external_includes(WITH_EXTERNAL)
        assert '!$LINK_PATH = ""' in body

    def test_untouched_when_nothing_external(self):
        body, stubbed = _stub_external_includes(SELF_CONTAINED)
        assert stubbed == 0
        assert body == SELF_CONTAINED

    def test_local_file_used_instead_of_stub(self, tmp_path):
        """Если подключаемый файл лежит рядом, берём его, а не глушим.

        Иначе теряются определения элементов, на которые ссылаются
        note и связи.
        """
        (tmp_path / 'settings.iuml').write_text('/' + "' пусто\n", encoding='utf-8')
        source = (
            '@startuml\n'
            '!include https://unreachable.invalid/data/settings.iuml\n'
            'component a\n'
            '@enduml\n'
        )

        body, stubbed = _stub_external_includes(source, tmp_path)

        assert '!include settings.iuml' in body
        assert stubbed == 0
        # Регрессия: подмена без заглушек считалась «ничего не изменилось»,
        # и результат отбрасывался
        assert body != source

    def test_undefined_vars_skips_defined(self):
        body = '@startuml\n!$KNOWN = "x"\ntitle $KNOWN $UNKNOWN_PATH\n@enduml\n'
        assert 'KNOWN' not in _undefined_vars(body)
        assert 'UNKNOWN_PATH' in _undefined_vars(body)


class TestUnregisteredSchemaInPreview:
    """
    Схему показывают пользователю до того, как она подключена к репозиторию.

    Превью собирает манифест репозитория, где нового файла ещё нет: без
    подмешивания редактируемого файла правая панель отвечала «контекст не
    найден» весь этап показа и правок по замечаниям — то есть всегда.
    """

    @pytest.fixture
    def repo(self, tmp_path):
        """Репозиторий с одним чужим компонентом и не подключённым файлом."""
        architecture = tmp_path / 'architecture'
        (architecture / 'domain' / 'sales').mkdir(parents=True)

        (architecture / 'dochub.yaml').write_text("""imports: []
components:
  dotnet.foreignApi:
    title: Foreign API
    entity: component
""", encoding='utf-8')

        own = architecture / 'domain' / 'sales' / 'OrderService.yaml'
        own.write_text("""components:
  dotnet.orderServiceApi:
    title: OrderService
    entity: component
contexts:
  sales.orders.orderFlow:
    title: orderFlow
    components:
    - dotnet.orderServiceApi
    - dotnet.foreignApi
""", encoding='utf-8')
        return own

    def test_own_context_visible_before_registration(self, repo):
        manifest = PreviewRenderer()._load_manifest(repo)

        assert 'sales.orders.orderFlow' in manifest['contexts']

    def test_foreign_components_still_resolve(self, repo):
        """Чужой компонент из репозитория обязан остаться разрешимым."""
        manifest = PreviewRenderer()._load_manifest(repo)

        assert 'dotnet.foreignApi' in manifest['components']
        assert 'dotnet.orderServiceApi' in manifest['components']

    def test_edit_reaches_the_manifest(self, repo):
        """Правка по замечанию пользователя видна без перезапуска превью."""
        renderer = PreviewRenderer()
        renderer._load_manifest(repo)

        repo.write_text(
            repo.read_text(encoding='utf-8').replace(
                'title: OrderService', 'title: Сервис заказов'),
            encoding='utf-8')

        manifest = renderer._load_manifest(repo)

        assert manifest['components']['dotnet.orderServiceApi']['title'] ==             'Сервис заказов'


class TestGeometry:
    """Восстановление вложенности по координатам."""

    def test_contains(self):
        assert contains((0, 0, 100, 100), (10, 10, 20, 20))
        assert not contains((0, 0, 100, 100), (90, 90, 50, 50))

    def test_tolerance(self):
        """Блоки часто выступают за рамку на несколько пикселей."""
        assert not contains((0, 0, 100, 100), (-5, 0, 50, 50))
        assert contains((0, 0, 100, 100), (-5, 0, 50, 50), tolerance=10)

    def test_innermost_wins(self):
        """Элемент относится к самой внутренней из объемлющих рамок."""
        geometries = {
            'outer': (0, 0, 100, 100),
            'inner': (10, 10, 50, 50),
            'item': (20, 20, 10, 10),
        }
        found = find_innermost('item', geometries, ['outer', 'inner'])
        assert found == 'inner'

    def test_no_geometry(self):
        assert find_innermost('missing', {}, ['outer']) is None

    def test_get_geometry_reads_child_node(self):
        import xml.etree.ElementTree as ET

        cell = ET.fromstring(
            '<mxCell><mxGeometry x="5" y="6" width="7" height="8"/></mxCell>'
        )
        assert get_geometry(cell) == (5.0, 6.0, 7.0, 8.0)


@pytest.fixture(scope='module')
def renderer():
    """Отрисовщик превью."""
    return PreviewRenderer()


@needs_plantuml
class TestPlantUMLRendering:
    """Реальный вызов PlantUML."""

    def test_renders_self_contained(self, renderer):
        svg = renderer.render_plantuml(SELF_CONTAINED)
        assert svg.lstrip().startswith('<?xml')
        assert '<text' in svg

    def test_cyrillic_preserved(self, renderer):
        """PlantUML кодирует кириллицу XML-сущностями — это нормально."""
        svg = renderer.render_plantuml(SELF_CONTAINED)
        import html
        import re

        texts = [html.unescape(t) for t in re.findall(r'<text[^>]*>(.*?)</text>', svg)]
        assert any('Сервис' in t for t in texts)

    def test_falls_back_when_server_unreachable(self, renderer, tmp_path):
        """Недоступный сервер не должен ронять отрисовку."""
        path = tmp_path / 'external.puml'
        path.write_text(WITH_EXTERNAL, encoding='utf-8')

        payload = renderer.describe_source(path)

        assert payload['kind'] == 'svg'
        assert '<text' in payload['data']
        assert payload['warnings'], 'откат должен быть отмечен предупреждением'

    def test_drawio_returned_as_xml(self, renderer):
        """DrawIO рисует встроенный движок, серверный рендер не нужен."""
        schema = Path(__file__).parent / 'fixtures' / 'OrderService.drawio'
        payload = renderer.describe_source(schema)
        assert payload['kind'] == 'drawio'
        assert payload['data'].lstrip().startswith('<mxfile')

    def test_unsupported_format(self, renderer, tmp_path):
        path = tmp_path / 'schema.txt'
        path.write_text('нет', encoding='utf-8')
        with pytest.raises(PreviewError):
            renderer.describe_source(path)

    def test_old_java_explained(self):
        """setup_assets.py качает последний PlantUML, а он требует Java 11+.

        Сообщение JVM про версию class-файла ничего не говорит ни о PlantUML,
        ни о том, что делать.
        """
        error = (
            'Exception in thread "main" java.lang.UnsupportedClassVersionError: '
            'net/sourceforge/plantuml/Run has been compiled by a more recent '
            'version of the Java Runtime (class file version 55.0)'
        )
        explained = PreviewRenderer._explain_plantuml_error(error, '@startuml\n@enduml')

        assert 'Java 11' in explained
        assert 'class file version' not in explained


class TestDrawIOExport:
    """Выгрузка DrawIO в картинку через Draw.io Desktop."""

    def test_reports_absence_clearly(self, tmp_path, monkeypatch):
        """Без установленного Draw.io нужно объяснить, а не падать молча."""
        import app.core.drawio_export as module

        monkeypatch.setattr(module, 'find_drawio', lambda: None)
        monkeypatch.delenv('DRAWIO_PATH', raising=False)

        with pytest.raises(module.DrawIOExportError) as excinfo:
            module.export(Path('any.drawio'), tmp_path / 'out.svg')

        message = str(excinfo.value)
        # Пользователю нужно знать, что превью всё равно работает
        assert 'dochub_preview' in message
        assert 'DRAWIO_PATH' in message

    def test_rejects_unknown_format(self, tmp_path):
        from app.core.drawio_export import DrawIOExportError, export

        with pytest.raises(DrawIOExportError):
            export(Path('any.drawio'), tmp_path / 'out.bmp', image_format='bmp')


class TestRenderedSourceHandover:
    """Картинку исходной схемы даёт встроенный движок, а не внешняя программа."""

    def test_server_stores_svg_from_page(self, tmp_path):
        """Страница присылает отрисованное, сервер это запоминает."""
        from app.preview.server import PreviewServer

        schema = Path(__file__).parent / 'fixtures' / 'OrderService.drawio'
        yaml_file = tmp_path / 'ctx.yaml'
        yaml_file.write_text('contexts: {}\n', encoding='utf-8')

        server = PreviewServer(schema, yaml_file, port=0)
        assert server.source_svg is None

        server.source_svg = '<svg xmlns="http://www.w3.org/2000/svg"></svg>'
        assert server.source_svg.startswith('<svg')

    def test_mcp_prefers_engine_output(self, tmp_path, monkeypatch):
        """Если превью уже отрисовало схему, внешняя программа не нужна."""
        import app.mcp_server as server_module

        schema = Path(__file__).parent / 'fixtures' / 'OrderService.drawio'

        class FakePreview:
            def __init__(self):
                self.source_path = schema
                self.source_svg = '<svg xmlns="http://www.w3.org/2000/svg"/>'

        monkeypatch.setitem(server_module._previews, 'fake', FakePreview())

        found = server_module._rendered_by_preview(schema)
        assert found and found.startswith('<svg')

    def test_no_preview_means_no_handover(self, monkeypatch):
        import app.mcp_server as server_module

        monkeypatch.setattr(server_module, '_previews', {})
        assert server_module._rendered_by_preview(Path('any.drawio')) is None


class TestGeneratedExports:
    """Выгрузка результата: YAML, PlantUML и картинка."""

    @needs_plantuml
    def test_context_plantuml_produced(self, renderer, tmp_path):
        """PlantUML выгружается текстом — его можно положить рядом со схемой."""
        yaml_file = tmp_path / 'sub.yaml'
        yaml_file.write_text(
            'components:\n'
            '  dotnet.svc:\n'
            '    title: Сервис\n'
            '    entity: component\n'
            'contexts:\n'
            '  demo.context:\n'
            '    title: Демонстрация\n'
            '    components:\n'
            '      - dotnet.svc\n',
            encoding='utf-8'
        )

        source = renderer.context_plantuml(yaml_file, 'demo.context')

        assert source.lstrip().startswith('@startuml')
        assert '$Entity(' in source

    def test_missing_context_reported(self, renderer, tmp_path):
        yaml_file = tmp_path / 'empty.yaml'
        yaml_file.write_text('contexts: {}\n', encoding='utf-8')

        with pytest.raises(PreviewError):
            renderer.context_plantuml(yaml_file)


class TestBundledJava:
    """Java из комплекта: получателю не нужно её ставить."""

    def test_falls_back_to_path(self, tmp_path, monkeypatch):
        """При установке из исходников берётся системная java."""
        from app.utils import paths

        monkeypatch.setattr(paths, 'BUNDLED_JRE', tmp_path / 'нет-такой')
        assert paths.java_command() == 'java'

    def test_bundled_wins(self, tmp_path, monkeypatch):
        """Если рядом лежит рантайм, спрашивать PATH незачем."""
        from app.utils import paths

        jre = tmp_path / 'runtime' / 'jre' / 'bin'
        jre.mkdir(parents=True)
        (jre / 'java.exe').write_bytes(b'')

        monkeypatch.setattr(paths, 'BUNDLED_JRE', tmp_path / 'runtime' / 'jre')
        assert paths.java_command() == str(jre / 'java.exe')

    def test_renderer_uses_it(self, tmp_path, monkeypatch):
        """Команда запуска PlantUML собирается с этой самой java."""
        from app.preview import renderer as renderer_module

        monkeypatch.setattr(renderer_module, 'java_command', lambda: r'X:\jre\java.exe')

        command = PreviewRenderer()._java_command()
        assert command[0] == r'X:\jre\java.exe'


BROKEN_YAML = 'contexts:' + chr(10) + '  broken: [' + chr(10)


class TestPreviewSurvivesBrokenYaml:
    """
    Опечатка в YAML не должна гасить оригинал схемы.

    Пользователь сверяется как раз с ним: когда правка оказалась неудачной,
    картинка нужна больше обычного. Сообщение об ошибке при этом должно
    называть файл и говорить, что проверить, а не быть дампом разборщика.
    """

    @staticmethod
    def _server(tmp_path, body: str):
        from app.preview.server import PreviewServer

        schema = Path(__file__).parent / 'fixtures' / 'TwoServices.drawio'
        broken = tmp_path / 'bad.yaml'
        broken.write_text(body, encoding='utf-8')
        return PreviewServer(schema, broken)

    def test_error_names_the_file_and_what_to_check(self, tmp_path):
        state = self._server(tmp_path, BROKEN_YAML).state()

        assert 'bad.yaml' in state['error']
        assert 'отступы' in state['error']

    def test_source_is_served_despite_the_error(self, tmp_path):
        """Левая панель берёт схему отдельным запросом и от YAML не зависит."""
        server = self._server(tmp_path, BROKEN_YAML)

        described = server.renderer.describe_source(server.source_path)

        assert described['pages'] == ['Заказы', 'Оплаты']
        assert described['data']

    def test_page_loads_source_before_reporting_the_error(self):
        """
        Страница выходила из опроса на ошибке раньше загрузки оригинала.

        Проверяется порядок в самом скрипте: живой прогон подтверждает
        результат, а этот тест ловит возврат порядка обратно.
        """
        page = (Path(__file__).parent.parent / 'app' / 'preview' / 'static'
                / 'index.html').read_text(encoding='utf-8')
        poll = page[page.index('async function poll()'):]

        assert poll.index('loadSource()') < poll.index('if (state.error)')

    def test_toolbar_wraps_instead_of_overflowing(self):
        """В узком окне кнопки выгрузки уезжали за край экрана."""
        page = (Path(__file__).parent.parent / 'app' / 'preview' / 'static'
                / 'index.html').read_text(encoding='utf-8')
        head = page[page.index('.pane-head {'):page.index('.pane-body {')]

        assert 'flex-wrap: wrap' in head

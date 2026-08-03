"""
Тесты брифа.

Смысл инструмента в том, чтобы спросить у пользователя только то, чего нет
ни в схеме, ни в доменной модели, ни в репозитории. Поэтому проверяется не
формат ответа, а какие вопросы остаются и откуда взялись факты.
"""

import asyncio
import json
from pathlib import Path

import pytest

import app.mcp_server as server
from app.core import settings as settings_module

SCHEMA = Path(__file__).parent / 'fixtures' / 'OrderService.drawio'

# Оформление ровно такое, какое ждёт парсер: домен — толстая рамка,
# ограниченный контекст — серая заливка с подписью снизу, поддомен —
# скруглённый блок с подписью сверху. Вложенность геометрическая
DDD_SCHEMA = '''<?xml version="1.0" encoding="UTF-8"?>
<mxfile>
  <diagram name="DDD">
    <mxGraphModel>
      <root>
        <mxCell id="0"/>
        <mxCell id="1" parent="0"/>
        <mxCell id="d1" value="Продажи&lt;div&gt;sales&lt;/div&gt;"
                style="strokeWidth=10" vertex="1" parent="1">
          <mxGeometry x="0" y="0" width="800" height="600" as="geometry"/>
        </mxCell>
        <mxCell id="c1" value="Заказы&lt;div&gt;orders&lt;/div&gt;"
                style="fillColor=#f5f5f5;verticalAlign=bottom" vertex="1" parent="1">
          <mxGeometry x="40" y="40" width="700" height="500" as="geometry"/>
        </mxCell>
        <mxCell id="s1" value="Поток заказов&lt;div&gt;orderFlow&lt;/div&gt;"
                style="rounded=1;verticalAlign=top" vertex="1" parent="1">
          <mxGeometry x="80" y="80" width="600" height="400" as="geometry"/>
        </mxCell>
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>'''

# Схема с русскими названиями и переносом строки в подписи — так рисуют на
# настоящих схемах компании
RU_SCHEMA = """<?xml version="1.0" encoding="UTF-8"?>
<mxfile><diagram><mxGraphModel><root>
  <mxCell id="0"/><mxCell id="1" parent="0"/>
  <mxCell id="f" c4Type="System_Boundary" value="Сервис заявок" parent="1">
    <mxGeometry x="0" y="0" width="600" height="400" as="geometry"/>
  </mxCell>
  <mxCell id="a" c4Type="container" c4Technology="MS SQL SP"
          value="ХП отправки события" parent="1">
    <mxGeometry x="40" y="40" width="200" height="80" as="geometry"/>
  </mxCell>
  <mxCell id="b" c4Type="container" c4Technology="MS SQL SP"
          value="Метод возврата&lt;br&gt;статуса" parent="1">
    <mxGeometry x="300" y="40" width="200" height="80" as="geometry"/>
  </mxCell>
  <mxCell id="r" edge="1" source="a" target="b" value="вызов" parent="1"/>
</root></mxGraphModel></diagram></mxfile>"""

# Двухстраничный файл: в DrawIO так лежат разные сервисы, и сливать их
# в один контекст нельзя
PAGES_SCHEMA = """<?xml version="1.0" encoding="UTF-8"?>
<mxfile>
  <diagram name="Сервис заказов">
    <mxGraphModel><root>
      <mxCell id="0"/><mxCell id="1" parent="0"/>
      <mxCell id="f1" c4Type="System_Boundary" value="OrderService" parent="1">
        <mxGeometry x="0" y="0" width="400" height="300" as="geometry"/>
      </mxCell>
      <mxCell id="a1" c4Type="container" c4Technology="dotnet"
              value="OrderController" parent="1">
        <mxGeometry x="20" y="20" width="150" height="60" as="geometry"/>
      </mxCell>
    </root></mxGraphModel>
  </diagram>
  <diagram name="Сервис оплат">
    <mxGraphModel><root>
      <mxCell id="0"/><mxCell id="1" parent="0"/>
      <mxCell id="f2" c4Type="System_Boundary" value="PaymentService" parent="1">
        <mxGeometry x="0" y="0" width="400" height="300" as="geometry"/>
      </mxCell>
      <mxCell id="b1" c4Type="container" c4Technology="dotnet"
              value="PaymentController" parent="1">
        <mxGeometry x="20" y="20" width="150" height="60" as="geometry"/>
      </mxCell>
      <mxCell id="b2" c4Type="container" c4Technology="dotnet"
              value="PaymentWorker" parent="1">
        <mxGeometry x="200" y="20" width="150" height="60" as="geometry"/>
      </mxCell>
    </root></mxGraphModel>
  </diagram>
</mxfile>"""


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Свои настройки и своя доменная схема."""
    monkeypatch.setenv('DOCHUB_WORKSPACE', str(tmp_path / 'workspace.yaml'))
    monkeypatch.setenv('DOCHUB_ENV', str(tmp_path / '.env'))
    for key in settings_module.KEYS:
        monkeypatch.delenv(key, raising=False)

    ddd = tmp_path / 'ddd.drawio'
    ddd.write_text(DDD_SCHEMA, encoding='utf-8')
    (tmp_path / '.env').write_text(
        f'DOCHUB_DDD_PATH={ddd.as_posix()}\n', encoding='utf-8')
    return tmp_path


def brief(**kwargs) -> dict:
    """Вызвать инструмент и разобрать ответ."""
    return json.loads(asyncio.run(server.dochub_brief(server.BriefInput(**kwargs))))


class TestFacts:
    """Что инструмент берёт из первоисточников."""

    def test_schema_summarised(self):
        answer = brief(schema_path=str(SCHEMA), query='orders')

        assert answer['schema']['physical'] is True
        assert answer['schema']['elements'] == 86
        assert answer['schema']['relations'] == 67

    def test_own_root_candidates_are_what_the_draft_expects(self):
        """
        Ответ на вопрос брифа уходит в own_root как есть.

        Кандидаты — корни идентификаторов DocHub, а не подписи рамок: подпись
        черновик не примет, и все компоненты молча уедут в чужие.
        """
        answer = brief(schema_path=str(SCHEMA), query='orders')
        candidates = answer['own_root_candidates']

        assert 'dotnet.orderServiceApi' in candidates
        assert 'OrderService.Api' not in candidates
        assert all(candidate.count('.') == 1 for candidate in candidates)

    def test_service_of_the_schema_offered_first(self):
        """
        В automode ответа ждать не от кого, и берётся первый кандидат.

        Схему называют по её сервису: OrderService.drawio — про
        dotnet.orderServiceApi. По весу первой была бы база: таблиц больше,
        по алфавиту — случайная чужая рамка.
        """
        answer = brief(schema_path=str(SCHEMA), query='orders')
        question = next(q for q in answer['questions'] if q['id'] == 'own_root')

        assert answer['own_root_candidates'][0] == 'dotnet.orderServiceApi'
        assert question['default'] == 'dotnet.orderServiceApi'

    def test_foreign_candidates_stay_frame_titles(self):
        """Чужие рамки показываются человеку так, как подписаны на схеме."""
        answer = brief(schema_path=str(SCHEMA), query='orders')

        # Controllers и Domain лежат внутри рамки сервиса — это его части
        assert 'Controllers' not in answer['foreign_candidates']

    def test_draft_accepts_every_candidate(self):
        """Каждый предложенный корень черновик принимает и строит по нему."""
        answer = brief(schema_path=str(SCHEMA), query='orders')

        for candidate in answer['own_root_candidates']:
            draft = asyncio.run(server.dochub_draft_from_schema(
                schema_path=str(SCHEMA), domain='sales', context='orders',
                subdomain='orderFlow', own_root=candidate))

            assert 'не совпал' not in draft, candidate
            assert 'components: {}' not in draft, candidate

    def test_draft_rejects_frame_title(self):
        """Подпись рамки вместо корня — отказ со списком, а не пустой файл."""
        draft = asyncio.run(server.dochub_draft_from_schema(
            schema_path=str(SCHEMA), domain='sales', context='orders',
            subdomain='orderFlow', own_root='OrderService.Api'))

        assert 'не совпал' in draft
        assert 'dotnet.orderServiceApi' in draft

    def test_draft_says_it_did_not_write_the_file(self, tmp_path):
        """
        Файл черновик не пишет, а следующие шаги работают с файлом.

        Пока это не сказано вслух, шаг «сохрани» остаётся подразумеваемым, и
        цепочка обрывается на dochub_validate_context.
        """
        target = tmp_path / 'OrderService.yaml'
        draft = asyncio.run(server.dochub_draft_from_schema(
            schema_path=str(SCHEMA), domain='sales', context='orders',
            subdomain='orderFlow', own_root='dotnet.orderServiceApi',
            yaml_path=str(target)))

        assert not target.exists()
        assert 'НЕ записан' in draft
        assert str(target) in draft

    def test_unknown_owner_is_not_printed_as_none(self):
        """`-> None` читается как «владелец известен», а он не найден."""
        draft = asyncio.run(server.dochub_draft_from_schema(
            schema_path=str(SCHEMA), domain='sales', context='orders',
            subdomain='orderFlow', own_root='dotnet.orderServiceApi'))

        assert '->  None' not in draft
        assert 'владелец не найден' in draft

    def test_russian_names_are_not_lost(self, tmp_path):
        """
        Компонент с русским названием обязан попасть в черновик.

        Идентификаторы DocHub латиницей, и раньше русское название давало
        пустой слаг: компонент молча исчезал. На схемах компании по-русски
        названы люди, методы и хранимые процедуры — терялось 17% элементов.
        """
        schema = tmp_path / 'ru.drawio'
        schema.write_text(RU_SCHEMA, encoding='utf-8')

        draft = asyncio.run(server.dochub_draft_from_schema(
            schema_path=str(schema), domain='sales', context='orders',
            subdomain='orderFlow', own_root='component.servisZayavok'))

        assert 'hpOtpravkiSobytiya' in draft
        assert 'ХП отправки события' in draft

    def test_transliterated_ids_are_shown(self, tmp_path):
        """Имена в репозитории выбирает человек, а не таблица транслитерации."""
        schema = tmp_path / 'ru.drawio'
        schema.write_text(RU_SCHEMA, encoding='utf-8')

        draft = asyncio.run(server.dochub_draft_from_schema(
            schema_path=str(schema), domain='sales', context='orders',
            subdomain='orderFlow', own_root='component.servisZayavok'))

        assert 'ТРАНСЛИТЕРАЦИЕЙ' in draft

    def test_saved_draft_is_valid_yaml(self, tmp_path):
        """
        Перенос строки в названии рвал сохранённый файл.

        Вторая половина названия уходила на строку без `#`, и следующий шаг
        падал разбором YAML вместо того, чтобы проверить схему.
        """
        import yaml

        schema = tmp_path / 'ru.drawio'
        schema.write_text(RU_SCHEMA, encoding='utf-8')

        draft = asyncio.run(server.dochub_draft_from_schema(
            schema_path=str(schema), domain='sales', context='orders',
            subdomain='orderFlow', own_root='component.servisZayavok'))
        body = "\n".join(
            line for line in draft.splitlines() if not line.startswith('#'))

        assert yaml.safe_load(body)

    def test_multipage_draft_refuses_until_page_chosen(self, tmp_path):
        """
        Страницы файла DrawIO — разные диаграммы.

        Слитые в один контекст, они дают схему, которой нет ни на одной
        странице. Инструмент называет страницы и ждёт выбора.
        """
        schema = tmp_path / 'pages.drawio'
        schema.write_text(PAGES_SCHEMA, encoding='utf-8')

        draft = asyncio.run(server.dochub_draft_from_schema(
            schema_path=str(schema), domain='sales', context='orders',
            subdomain='orderFlow'))

        assert '2 страниц' in draft
        assert 'Сервис заказов' in draft
        assert 'Сервис оплат' in draft

    def test_draft_by_page_takes_only_that_page(self, tmp_path):
        schema = tmp_path / 'pages.drawio'
        schema.write_text(PAGES_SCHEMA, encoding='utf-8')

        draft = asyncio.run(server.dochub_draft_from_schema(
            schema_path=str(schema), domain='sales', context='orders',
            subdomain='orderFlow', page=1, own_root='component.paymentService'))

        assert 'paymentController' in draft
        assert 'orderController' not in draft

    def test_brief_asks_which_page(self, tmp_path):
        schema = tmp_path / 'pages.drawio'
        schema.write_text(PAGES_SCHEMA, encoding='utf-8')

        answer = brief(schema_path=str(schema), query='orders')
        question = next(q for q in answer['questions'] if q['id'] == 'page')

        assert [p['name'] for p in answer['pages']] ==             ['Сервис заказов', 'Сервис оплат']
        assert 'Сервис оплат' in question['why']
        # умолчания нет: страницу называет пользователь
        assert question['default'] == ''

    def test_code_is_read_when_its_path_is_given(self, tmp_path):
        """
        Путь к коду в брифе — это чтение кода, а не снятие вопроса.

        Схему рисовали раньше, код менялся: точные имена классов, эндпоинты и
        процедуры берутся из репозитория сервиса, иначе схема уедет в DocHub
        такой, какой сервис давно не является.
        """
        code = tmp_path / 'src'
        code.mkdir()
        (code / 'OrderController.cs').write_text(
            'public class OrderController { }', encoding='utf-8')
        (code / 'NewInCodeService.cs').write_text(
            'public class NewInCodeService { }', encoding='utf-8')

        answer = brief(schema_path=str(SCHEMA), query='orders',
                       project_root=str(code))

        code_facts = answer['code']

        assert code_facts['scanned_files'] == 2
        assert code_facts['symbols_found'] == 2
        # Класс, которого нет на схеме, инструмент обязан заметить
        assert code_facts['summary'].get('in_code_not_on_schema')
        # А совпавшее по имени — засчитать
        assert code_facts['matched'] >= 1

    def test_code_mismatch_is_asked_about(self, tmp_path):
        """Что из расхождений идёт в схему — решает человек."""
        code = tmp_path / 'src'
        code.mkdir()
        (code / 'NewInCodeService.cs').write_text(
            'public class NewInCodeService { }', encoding='utf-8')

        answer = brief(schema_path=str(SCHEMA), query='orders',
                       project_root=str(code))
        question = next(q for q in answer['questions'] if q['id'] == 'code_mismatch')

        assert 'сверено файлов' in question['why']
        assert question['default'] == ''

    def test_without_code_nothing_is_invented(self):
        """Кода не дали — в ответе нет раздела про код."""
        answer = brief(schema_path=str(SCHEMA), query='orders')

        assert answer['code'] is None
        assert 'sources' in [q['id'] for q in answer['questions']]

    def test_page_without_components_says_so(self, tmp_path):
        """
        Страница бизнес-процесса не даёт состава сервиса.

        Раньше инструмент просил выбрать «нашу рамку» и показывал пустой
        список — тупик, из которого пользователю некуда идти.
        """
        schema = tmp_path / 'empty.drawio'
        schema.write_text("""<?xml version="1.0" encoding="UTF-8"?>
<mxfile><diagram name="Процесс"><mxGraphModel><root>
  <mxCell id="0"/><mxCell id="1" parent="0"/>
  <mxCell id="a" c4Type="System_Boundary" value="Процесс выдачи" parent="1">
    <mxGeometry x="0" y="0" width="400" height="300" as="geometry"/>
  </mxCell>
</root></mxGraphModel></diagram></mxfile>""", encoding='utf-8')

        draft = asyncio.run(server.dochub_draft_from_schema(
            schema_path=str(schema), domain='sales', context='orders',
            subdomain='orderFlow'))

        assert 'нет ни одного элемента' in draft
        assert 'own_root' not in draft

    def test_placement_found_by_id(self):
        """В доменной схеме названия по-русски, а идентификаторы латиницей."""
        answer = brief(schema_path=str(SCHEMA), query='orders')

        assert [c['path'] for c in answer['placement_candidates']] == \
            ['sales.orders.orderFlow']

    def test_logical_diagram_rejected_with_reason(self, tmp_path):
        path = tmp_path / 'flow.puml'
        path.write_text(
            '@startuml\nautonumber\nparticipant A\nA -> B: call\n@enduml\n',
            encoding='utf-8')

        answer = brief(schema_path=str(path))

        assert answer['schema']['physical'] is False
        assert answer['schema']['problem']


class TestQuestions:
    """Спрашиваем только то, чего не нашли сами."""

    def test_placement_not_asked_when_found(self):
        answer = brief(schema_path=str(SCHEMA), query='orders')
        assert 'placement' not in [q['id'] for q in answer['questions']]

    def test_placement_asked_when_ambiguous(self):
        answer = brief(schema_path=str(SCHEMA), query='нет такого поддомена')
        assert 'placement' in [q['id'] for q in answer['questions']]

    def test_sources_not_asked_when_code_given(self, tmp_path):
        code = tmp_path / 'src'
        code.mkdir()

        answer = brief(schema_path=str(SCHEMA), query='orders',
                       project_root=str(code))
        assert 'sources' not in [q['id'] for q in answer['questions']]

    def test_merge_not_asked_when_assignee_configured(self, isolated):
        (isolated / '.env').write_text(
            f'DOCHUB_DDD_PATH={(isolated / "ddd.drawio").as_posix()}\n'
            'DOCHUB_MR_ASSIGNEE=ivanov\n', encoding='utf-8')

        answer = brief(schema_path=str(SCHEMA), query='orders')
        assert 'merge' not in [q['id'] for q in answer['questions']]

    def test_every_question_has_reason_and_default(self):
        """Вопрос без первоисточника — это догадка, а без умолчания нет automode."""
        answer = brief(schema_path=str(SCHEMA), query='orders')

        for question in answer['questions']:
            assert question['why']
            # page и placement умолчания не имеют: какую схему заводить в
            # репозиторий, решает человек, а не самая крупная страница
            assert question['default'] != '' or question['id'] in (
                'placement', 'page', 'code_mismatch')


class TestAutomode:
    """В автоматическом режиме вопросы закрываются умолчаниями."""

    def test_automode_does_not_pick_a_page(self, isolated):
        """
        В репозиторий заводится конкретная схема.

        Страницы — разные задачи; выбрать за пользователя нельзя даже в
        automode, поэтому вопрос остаётся вопросом.
        """
        ddd = (isolated / 'ddd.drawio').as_posix()
        (isolated / '.env').write_text(
            f"DOCHUB_DDD_PATH={ddd}\nDOCHUB_AUTOMODE=true\n", encoding='utf-8')
        schema = isolated / 'pages.drawio'
        schema.write_text(PAGES_SCHEMA, encoding='utf-8')

        answer = brief(schema_path=str(schema), query='orders')
        question = next(q for q in answer['questions'] if q['id'] == 'page')

        assert answer['automode'] is True
        assert question['default'] == ''
        assert 'Какая страница' in answer['brief_markdown']

    def test_defaults_shown_instead_of_questions(self, isolated):
        (isolated / '.env').write_text(
            f'DOCHUB_DDD_PATH={(isolated / "ddd.drawio").as_posix()}\n'
            'DOCHUB_AUTOMODE=true\n', encoding='utf-8')

        answer = brief(schema_path=str(SCHEMA), query='orders')

        assert answer['automode'] is True
        assert 'automode' in answer['brief_markdown']
        assert answer['questions'], 'вопросы остаются в ответе — с умолчаниями'

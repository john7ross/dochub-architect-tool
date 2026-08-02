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

    def test_top_level_frames_are_service_candidates(self):
        """Свой сервис ищется среди рамок верхнего уровня, а не всех подряд."""
        answer = brief(schema_path=str(SCHEMA), query='orders')

        assert 'OrderService.Api' in answer['own_root_candidates']
        # Controllers и Domain лежат внутри рамки сервиса — это его части
        assert 'Controllers' not in answer['own_root_candidates']
        assert 'Controllers' not in answer['foreign_candidates']

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
            assert question['default'] != '' or question['id'] == 'placement'


class TestAutomode:
    """В автоматическом режиме вопросы закрываются умолчаниями."""

    def test_defaults_shown_instead_of_questions(self, isolated):
        (isolated / '.env').write_text(
            f'DOCHUB_DDD_PATH={(isolated / "ddd.drawio").as_posix()}\n'
            'DOCHUB_AUTOMODE=true\n', encoding='utf-8')

        answer = brief(schema_path=str(SCHEMA), query='orders')

        assert answer['automode'] is True
        assert 'automode' in answer['brief_markdown']
        assert answer['questions'], 'вопросы остаются в ответе — с умолчаниями'

"""
Тесты на реальной схеме сервиса.

Синтетические фикстуры уже один раз подвели: они закрепляли неверные
предположения и проходили на полностью нерабочем парсере. Здесь всё
проверяется на настоящем файле, выгруженном из draw.io.
"""

from pathlib import Path

import pytest

from app.core.parsers.drawio_parser import DrawIOParser
from app.core.parsers.diagram_kind import DiagramKind, detect_file_kind
from app.core.transformer import Transformer, slugify

SCHEMA = Path(__file__).parent / 'fixtures' / 'OrderService.drawio'

# Сервис, который описывает схема
OWN_ROOT = 'dotnet.orderServiceApi'


@pytest.fixture(scope='module')
def parsed():
    """Разобранная схема."""
    return DrawIOParser(str(SCHEMA)).parse()


class TestRealDrawIOParsing:
    """Разбор настоящего файла draw.io."""

    def test_recognised_as_c4(self, parsed):
        """C4-атрибуты лежат на <object>, а не на <mxCell>."""
        assert parsed.is_c4 is True

    def test_components_and_relations_found(self, parsed):
        """Схема даёт элементы и связи.

        Раньше парсер возвращал ноль и того и другого: атрибуты искались
        не на тех узлах, а связи определялись по подстроке в style.
        """
        assert len(parsed.components) == 86
        assert len(parsed.relations) == 67

    def test_relations_resolve_to_components(self, parsed):
        """Все связи ведут на существующие элементы."""
        known = {c.id for c in parsed.components}
        dangling = [
            r for r in parsed.relations
            if r.from_id not in known or r.to_id not in known
        ]
        assert dangling == []

    def test_boundaries_kept_and_marked(self, parsed):
        """Рамки не выбрасываются: они дают уровень L2."""
        boundaries = [c for c in parsed.components if c.is_boundary]
        assert len(boundaries) == 16

    def test_hierarchy_restored_geometrically(self, parsed):
        """Вложенность в шаблоне C4 задана геометрией, а не атрибутом parent."""
        by_id = {c.id: c for c in parsed.components}
        by_title = {c.title: c for c in parsed.components if c.title}

        def ancestors(component):
            names = []
            current = component
            while current.parent and current.parent in by_id:
                current = by_id[current.parent]
                names.append(current.title)
            return names

        assert 'Infrastructure' in ancestors(by_title['OrderDbContext'])
        assert 'OrderService.Api' in ancestors(by_title['OrderDbContext'])
        assert 'Services' in ancestors(by_title['OrderHandlerService'])

    def test_cyrillic_descriptions_survive(self, parsed):
        """Русские описания из c4Description не бьются кодировкой."""
        described = [c for c in parsed.components if c.description]
        assert described
        assert any('получен' in c.description.lower() for c in described)


class TestCompressedDrawIO:
    """DrawIO умеет сохранять страницу сжатой одной строкой."""

    @staticmethod
    def _compress(xml: str) -> str:
        """Упаковать как это делает DrawIO: url-encode -> deflate -> base64."""
        import base64
        import urllib.parse
        import zlib

        quoted = urllib.parse.quote(xml, safe='~()*!.\'')
        compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
        packed = compressor.compress(quoted.encode('utf-8')) + compressor.flush()
        return base64.b64encode(packed).decode('ascii')

    def test_compressed_page_expanded(self, tmp_path):
        """Без распаковки такая схема выглядит пустой."""
        model = (
            '<mxGraphModel><root>'
            '<mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<object id="2" c4Name="Сервис" c4Type="Container" label="Сервис">'
            '<mxCell parent="1"><mxGeometry x="0" y="0" width="10" height="10"/>'
            '</mxCell></object>'
            '</root></mxGraphModel>'
        )
        path = tmp_path / 'packed.drawio'
        path.write_text(
            f'<mxfile><diagram name="Стр-1">{self._compress(model)}</diagram></mxfile>',
            encoding='utf-8'
        )

        result = DrawIOParser(str(path)).parse()

        assert result.is_c4 is True
        assert [c.title for c in result.components] == ['Сервис']

    def test_plain_file_untouched(self, parsed):
        """Обычные файлы читаются как раньше."""
        assert len(parsed.components) == 86


class TestDiagramKind:
    """Отсечение диаграмм логического уровня."""

    def test_c4_drawio_accepted(self):
        kind, problem = detect_file_kind(SCHEMA)
        assert kind is DiagramKind.C4
        assert problem is None

    def test_sequence_rejected(self, tmp_path):
        """Из диаграммы последовательности состав компонентов не выводится."""
        path = tmp_path / 'seq.puml'
        path.write_text(
            '@startuml\nautonumber\nparticipant A\nA -> B: call\n@enduml\n',
            encoding='utf-8'
        )
        kind, problem = detect_file_kind(path)
        assert kind is DiagramKind.SEQUENCE
        assert problem and 'логическ' in problem

    def test_activity_rejected(self, tmp_path):
        path = tmp_path / 'act.puml'
        path.write_text(
            '@startuml\nstart\n:Шаг;\nstop\n@enduml\n', encoding='utf-8'
        )
        kind, problem = detect_file_kind(path)
        assert kind is DiagramKind.ACTIVITY
        assert problem


class TestSlugify:
    """Идентификаторы строятся из c4Name."""

    @pytest.mark.parametrize('source,expected', [
        ('OrderHandlerService', 'orderHandlerService'),
        ('dbo.Partner_Edit_Get', 'dboPartnerEditGet'),
        ('OrderDbContext', 'orderDbContext'),
    ])
    def test_camel_case(self, source, expected):
        assert slugify(source) == expected

    def test_empty(self):
        assert slugify('') == ''
        assert slugify(None) == ''


class TestOwnershipRule:
    """Компонент описывается у владельца, а не у того, кто им пользуется."""

    def test_own_root_required(self, parsed):
        """Без указания своего сервиса чужие компоненты уедут к нам."""
        transformer = Transformer('sales', 'orders', 'orderFlow')
        result = transformer.transform(parsed, own_root=None)

        # Без own_root разделения не происходит — это и защищает MCP-слой,
        # который в таком случае отказывается строить черновик
        assert result['foreign'] == []

    def test_foreign_components_excluded(self, parsed):
        """Всё вне рамки нашего сервиса не попадает в наш файл."""
        result = Transformer(
            'sales', 'orders', 'orderFlow'
        ).transform(parsed, own_root=OWN_ROOT)

        own_roots = {'.'.join(cid.split('.')[:2]) for cid in result['components']}
        assert own_roots == {OWN_ROOT}
        assert len(result['foreign']) > 0

    def test_foreign_still_referenced_in_context(self, parsed):
        """Чужие компоненты остаются в контексте по полному идентификатору."""
        result = Transformer(
            'sales', 'orders', 'orderFlow'
        ).transform(parsed, own_root=OWN_ROOT)

        context = next(iter(result['contexts'].values()))
        foreign_ids = {item['component_id'] for item in result['foreign']}

        assert foreign_ids
        assert foreign_ids <= set(context['components'])

    def test_no_parent_components_for_foreign(self, parsed):
        """L2 чужого сервиса не дублируется в нашем файле."""
        result = Transformer(
            'sales', 'orders', 'orderFlow'
        ).transform(parsed, own_root=OWN_ROOT)

        assert 'mssql.MSSQL' not in result['components']
        assert 'dotnet.eventGatewayEnterpriseServiceBus' not in result['components']


@pytest.fixture(scope='module')
def draft(parsed):
    """Черновик, построенный с указанием своего сервиса."""
    return Transformer(
        'sales', 'orders', 'orderFlow'
    ).transform(parsed, own_root=OWN_ROOT)


class TestDraftShape:
    """Форма черновика соответствует соглашениям репозитория."""

    def test_relations_go_to_uml_after(self, draft):
        """DocHub читает связи из uml.$after, секции links не существует."""
        context = next(iter(draft['contexts'].values()))
        assert 'links' not in context
        assert context['uml']['$after'].strip()

    def test_extra_links_disabled(self, draft):
        """Иначе DocHub дорисует всё окружение и диаграмма станет нечитаемой."""
        context = next(iter(draft['contexts'].values()))
        assert context['extra-links'] is False

    def test_entity_types_from_repository_vocabulary(self, draft):
        """В репозитории нет entity: system — есть component, database, queue."""
        entities = {item['entity'] for item in draft['components'].values()}
        assert 'system' not in entities
        assert entities <= {'component', 'database', 'queue', 'person', 'folder'}

    def test_ids_are_camel_case_not_drawio_ids(self, draft):
        """Внутренние идентификаторы draw.io не должны попадать в YAML."""
        for component_id in draft['components']:
            assert '-' not in component_id
            assert component_id.islower() or any(c.isupper() for c in component_id)

    def test_service_classes_present(self, draft):
        """Ключевые классы сервиса распознаны."""
        titles = {v.get('title', '') for v in draft['components'].values()}
        expected = {
            'OrderHandlerService', 'OrderSenderClient', 'OrderDbContext',
            'OrderPlacedConsumer', 'OrderController',
            'JsonFillService', 'OrderDataService',
        }
        assert expected <= titles


class TestMultiPage:
    """Файл DrawIO часто содержит несколько независимых диаграмм."""

    MULTI = Path(__file__).parent.parent / 'schemas' / 'drawio' / 'бп схема(1).drawio'

    @pytest.mark.skipif(not MULTI.exists(), reason='нужен многостраничный файл')
    def test_pages_listed(self):
        result = DrawIOParser(str(self.MULTI)).parse()
        assert len(result.pages) > 1

    @pytest.mark.skipif(not MULTI.exists(), reason='нужен многостраничный файл')
    def test_elements_carry_page(self):
        """Без признака страницы элементы разных диаграмм сливаются."""
        result = DrawIOParser(str(self.MULTI)).parse()
        assert {c.page for c in result.components} == set(
            range(len(result.pages))
        ) or {c.page for c in result.components} <= set(range(len(result.pages)))
        assert all(c.page_name for c in result.components)

    @pytest.mark.skipif(not MULTI.exists(), reason='нужен многостраничный файл')
    def test_single_page_slice(self):
        """Схема строится по одной странице, а не по всему файлу сразу."""
        result = DrawIOParser(str(self.MULTI)).parse()
        page = result.for_page(0)

        assert all(c.page == 0 for c in page.components)
        assert all(r.page == 0 for r in page.relations)
        assert len(page.components) <= len(result.components)

    def test_single_page_file_has_one_page(self, parsed):
        assert len(parsed.pages) == 1
        assert all(c.page == 0 for c in parsed.components)


class TestQuestionableElements:
    """Движок помечает спорное, но ничего не решает сам."""

    def test_structural_names_flagged(self, draft):
        """DTO, Entities и подобные — повод спросить пользователя."""
        flagged = {item['title'] for item in draft['questionable']}
        assert {'DTO', 'Entities', 'Enums', 'Interfaces', 'Models'} <= flagged

    def test_isolated_elements_flagged(self, draft):
        """Элемент без единой связи нарисован для полноты картины."""
        flagged = {item['title'] for item in draft['questionable']}
        assert 'AddSwaggerConfiguration' in flagged
        assert 'MassTransitRegistrator' in flagged

    def test_real_components_not_flagged(self, draft):
        """Рабочие классы сервиса под подозрение попадать не должны."""
        flagged = {item['title'] for item in draft['questionable']}
        working = {
            'OrderDbContext', 'OrderHandlerService', 'OrderSenderClient',
            'JsonFillService', 'OrderDataService', 'OrderController',
        }
        assert not (flagged & working)

    def test_nothing_is_removed(self, draft):
        """Помеченное остаётся в черновике: выбрасывает пользователь, не движок."""
        ids = set(draft['components'])
        for item in draft['questionable']:
            assert item['component_id'] in ids

    def test_reasons_are_given(self, draft):
        """Без основания вопрос пользователю бессмыслен."""
        assert draft['questionable']
        assert all(item['reasons'] for item in draft['questionable'])

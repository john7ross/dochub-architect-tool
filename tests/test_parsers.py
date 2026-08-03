"""
Тесты для парсеров схем.
"""

import pytest
from pathlib import Path

from app.core.parsers.plantuml_parser import PlantUMLParser
from app.core.parsers.drawio_parser import DrawIOParser
from app.core.parsers.ddd_parser import DDDParser


class TestPlantUMLParser:
    """Тесты PlantUML парсера."""

    def test_c4_notation_detection(self, tmp_path):
        """Тест определения C4 нотации."""
        # C4 схема
        c4_content = """
        @startuml
        !include C4_Container.puml

        Person(user, "User", "End user")
        System(system, "System", "Main system")
        Rel(user, system, "Uses")
        @enduml
        """

        c4_file = tmp_path / "c4_schema.puml"
        c4_file.write_text(c4_content)

        parser = PlantUMLParser(str(c4_file))
        assert parser.is_c4_notation() is True

    def test_non_c4_notation_detection(self, tmp_path):
        """Тест определения не-C4 схемы."""
        # Обычная PlantUML схема
        non_c4_content = """
        @startuml
        class User {
            +name: string
            +email: string
        }

        class Order {
            +id: int
            +total: float
        }

        User --> Order
        @enduml
        """

        non_c4_file = tmp_path / "non_c4_schema.puml"
        non_c4_file.write_text(non_c4_content)

        parser = PlantUMLParser(str(non_c4_file))
        assert parser.is_c4_notation() is False

    def test_parse_c4_schema(self, tmp_path):
        """Тест парсинга C4 схемы."""
        c4_content = """
        @startuml
        !include C4_Container.puml

        Person(user, "User", "End user")
        System(backend, "Backend System", "Main backend")
        Container(api, "API", "REST API", "Handles requests")

        Rel(user, api, "Makes requests", "HTTPS")
        Rel(api, backend, "Uses")
        @enduml
        """

        c4_file = tmp_path / "c4_schema.puml"
        c4_file.write_text(c4_content)

        parser = PlantUMLParser(str(c4_file))
        result = parser.parse()

        assert result.is_c4 is True
        assert len(result.components) == 3
        assert len(result.relations) == 2

        # Проверяем компоненты
        user = next(c for c in result.components if c.id == "user")
        assert user.title == "User"
        assert user.type == "Person"
        assert user.description == "End user"

        api = next(c for c in result.components if c.id == "api")
        assert api.title == "API"
        assert api.type == "Container"
        assert api.technology == "REST API"
        assert api.description == "Handles requests"

        # Проверяем связи
        rel1 = result.relations[0]
        assert rel1.from_id == "user"
        assert rel1.to_id == "api"
        assert rel1.label == "Makes requests"

    def test_parse_non_c4_schema(self, tmp_path):
        """Тест парсинга не-C4 схемы."""
        non_c4_content = """
        @startuml
        class User {
            +name: string
        }
        @enduml
        """

        non_c4_file = tmp_path / "non_c4_schema.puml"
        non_c4_file.write_text(non_c4_content)

        parser = PlantUMLParser(str(non_c4_file))
        result = parser.parse()

        assert result.is_c4 is False
        assert result.error_message is not None
        assert "C4 макросов" in result.error_message


class TestDrawIOParser:
    """Тесты DrawIO парсера."""

    def test_c4_notation_detection(self, tmp_path):
        """Тест определения C4 нотации."""
        # C4 схема
        c4_content = """<?xml version="1.0" encoding="UTF-8"?>
        <mxfile>
          <diagram>
            <mxGraphModel>
              <root>
                <mxCell id="0"/>
                <mxCell id="1" parent="0"/>
                <mxCell id="2" c4Name="User" c4Type="person" value="User" parent="1"/>
              </root>
            </mxGraphModel>
          </diagram>
        </mxfile>
        """

        c4_file = tmp_path / "c4_schema.drawio"
        c4_file.write_text(c4_content)

        parser = DrawIOParser(str(c4_file))
        assert parser.is_c4_notation() is True

    def test_non_c4_notation_detection(self, tmp_path):
        """Тест определения не-C4 схемы."""
        # Обычная DrawIO схема
        non_c4_content = """<?xml version="1.0" encoding="UTF-8"?>
        <mxfile>
          <diagram>
            <mxGraphModel>
              <root>
                <mxCell id="0"/>
                <mxCell id="1" parent="0"/>
                <mxCell id="2" value="Some box" style="rounded=0" parent="1"/>
              </root>
            </mxGraphModel>
          </diagram>
        </mxfile>
        """

        non_c4_file = tmp_path / "non_c4_schema.drawio"
        non_c4_file.write_text(non_c4_content)

        parser = DrawIOParser(str(non_c4_file))
        assert parser.is_c4_notation() is False

    def test_parse_c4_schema(self, tmp_path):
        """Тест парсинга C4 схемы."""
        c4_content = """<?xml version="1.0" encoding="UTF-8"?>
        <mxfile>
          <diagram>
            <mxGraphModel>
              <root>
                <mxCell id="0"/>
                <mxCell id="1" parent="0"/>
                <mxCell id="2" c4Name="User" c4Type="person" c4Description="End user"
                        value="User" parent="1"/>
                <mxCell id="3" c4Name="API" c4Type="container" c4Technology="REST"
                        c4Description="API Gateway" value="API" parent="1"/>
                <mxCell id="4" value="Uses" style="edgeStyle=orthogonalEdgeStyle;edge=1"
                        source="2" target="3" parent="1"/>
              </root>
            </mxGraphModel>
          </diagram>
        </mxfile>
        """

        c4_file = tmp_path / "c4_schema.drawio"
        c4_file.write_text(c4_content)

        parser = DrawIOParser(str(c4_file))
        result = parser.parse()

        assert result.is_c4 is True
        assert len(result.components) == 2
        assert len(result.relations) == 1

        # Проверяем компоненты
        user = next(c for c in result.components if c.id == "2")
        assert user.title == "User"
        assert user.type == "Person"

        api = next(c for c in result.components if c.id == "3")
        assert api.title == "API"
        assert api.type == "Container"
        assert api.technology == "REST"


class TestDrawIOLabels:
    """Подпись в DrawIO — это HTML, а из неё строятся заголовок и id."""

    @staticmethod
    def _schema(value: str) -> str:
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<mxfile><diagram><mxGraphModel><root>
  <mxCell id="0"/><mxCell id="1" parent="0"/>
  <mxCell id="2" c4Type="container" value="{value}" parent="1">
    <mxGeometry x="0" y="0" width="200" height="100" as="geometry"/>
  </mxCell>
</root></mxGraphModel></diagram></mxfile>"""

    def test_markup_stripped_from_title(self, tmp_path):
        """Иначе идентификатор получался вида fontStyleFontSize11px..."""
        f = tmp_path / 'label.drawio'
        f.write_text(self._schema(
            '&lt;font style=&quot;font-size: 11px&quot;&gt;&lt;b&gt;Order&lt;/b&gt;'
            '&amp;nbsp;Service&lt;/font&gt;'), encoding='utf-8')

        result = DrawIOParser(str(f)).parse()

        assert result.components[0].title == 'Order Service'

    def test_line_break_becomes_space(self, tmp_path):
        """
        Перенос строки в названии рвал YAML черновика.

        Вторая половина уходила на строку без `#`, и сохранённый файл уже
        никто не мог прочитать — ошибка всплывала в dochub_validate_context.
        """
        f = tmp_path / 'break.drawio'
        f.write_text(self._schema(
            'PaymentGateway_Client.&lt;br&gt;onStateChanged'),
            encoding='utf-8')

        title = DrawIOParser(str(f)).parse().components[0].title

        assert '\n' not in title
        assert title == 'PaymentGateway_Client. onStateChanged'


class TestDDDParser:
    """Тесты DDD парсера."""

    def test_parse_hierarchy(self, tmp_path):
        """Тест парсинга иерархии."""
        ddd_content = """<?xml version="1.0" encoding="UTF-8"?>
        <mxfile>
          <diagram>
            <mxGraphModel>
              <root>
                <mxCell id="0"/>
                <mxCell id="1" parent="0"/>

                <mxCell id="domain1" value="&lt;b&gt;Продажи&lt;/b&gt;&lt;br&gt;&lt;b&gt;sales&lt;/b&gt;"
                        style="rounded=1;dashed=1;strokeWidth=10" parent="1"/>

                <mxCell id="context1" value="&lt;b&gt;Андеррайтинг&lt;/b&gt;&lt;br&gt;&lt;b&gt;orders&lt;/b&gt;"
                        style="rounded=0;fillColor=#f5f5f5;verticalAlign=bottom" parent="domain1"/>

                <mxCell id="subdomain1" value="&lt;b&gt;Скоринг&lt;/b&gt;&lt;br&gt;&lt;b&gt;orderFlow&lt;/b&gt;"
                        style="rounded=1;fillColor=#d5e8d4;verticalAlign=top" parent="context1"/>
              </root>
            </mxGraphModel>
          </diagram>
        </mxfile>
        """

        ddd_file = tmp_path / "ddd_schema.drawio"
        ddd_file.write_text(ddd_content, encoding='utf-8')

        parser = DDDParser(str(ddd_file))
        domains = parser.parse()

        assert len(domains) == 1

        domain = domains[0]
        assert domain.id == "sales"
        assert len(domain.contexts) == 1

        context = domain.contexts[0]
        assert context.id == "orders"
        assert len(context.subdomains) == 1

        subdomain = context.subdomains[0]
        assert subdomain.id == "orderFlow"

    def test_get_domain_tree(self, tmp_path):
        """Тест получения дерева доменов."""
        ddd_content = """<?xml version="1.0" encoding="UTF-8"?>
        <mxfile>
          <diagram>
            <mxGraphModel>
              <root>
                <mxCell id="0"/>
                <mxCell id="1" parent="0"/>
                <mxCell id="domain1" value="&lt;b&gt;Domain&lt;/b&gt;&lt;br&gt;&lt;b&gt;domain&lt;/b&gt;"
                        style="rounded=1;dashed=1;strokeWidth=10" parent="1"/>
                <mxCell id="context1" value="&lt;b&gt;Context&lt;/b&gt;&lt;br&gt;&lt;b&gt;context&lt;/b&gt;"
                        style="rounded=0;fillColor=#f5f5f5;verticalAlign=bottom" parent="domain1"/>
                <mxCell id="subdomain1" value="&lt;b&gt;Subdomain&lt;/b&gt;&lt;br&gt;&lt;b&gt;subdomain&lt;/b&gt;"
                        style="rounded=1;fillColor=#d5e8d4;verticalAlign=top" parent="context1"/>
              </root>
            </mxGraphModel>
          </diagram>
        </mxfile>
        """

        ddd_file = tmp_path / "ddd_schema.drawio"
        ddd_file.write_text(ddd_content)

        parser = DDDParser(str(ddd_file))
        tree = parser.get_domain_tree()

        assert 'domains' in tree
        assert len(tree['domains']) == 1

        domain = tree['domains'][0]
        assert domain['id'] == 'domain'
        assert len(domain['contexts']) == 1

        context = domain['contexts'][0]
        assert context['id'] == 'context'
        assert len(context['subdomains']) == 1

    def test_find_subdomain_info(self, tmp_path):
        """Тест поиска информации о поддомене."""
        ddd_content = """<?xml version="1.0" encoding="UTF-8"?>
        <mxfile>
          <diagram>
            <mxGraphModel>
              <root>
                <mxCell id="0"/>
                <mxCell id="1" parent="0"/>
                <mxCell id="domain1" value="&lt;b&gt;sales&lt;/b&gt;&lt;br&gt;&lt;b&gt;sales&lt;/b&gt;"
                        style="rounded=1;dashed=1;strokeWidth=10" parent="1"/>
                <mxCell id="context1" value="&lt;b&gt;orders&lt;/b&gt;&lt;br&gt;&lt;b&gt;orders&lt;/b&gt;"
                        style="rounded=0;fillColor=#f5f5f5;verticalAlign=bottom" parent="domain1"/>
                <mxCell id="subdomain1" value="&lt;b&gt;orderFlow&lt;/b&gt;&lt;br&gt;&lt;b&gt;orderFlow&lt;/b&gt;"
                        style="rounded=1;fillColor=#d5e8d4;verticalAlign=top" parent="context1"/>
              </root>
            </mxGraphModel>
          </diagram>
        </mxfile>
        """

        ddd_file = tmp_path / "ddd_schema.drawio"
        ddd_file.write_text(ddd_content)

        parser = DDDParser(str(ddd_file))
        info = parser.find_subdomain_info("sales", "orders", "orderFlow")

        assert info is not None
        assert info['domain'] == "sales"
        assert info['context'] == "orders"
        assert info['subdomain'] == "orderFlow"
        assert info['full_path'] == "sales.orders.orderFlow"


class TestDDDPagesAndUnnamed:
    """Страницы и безымянные рамки доменной схемы."""

    PAGE = '''<mxCell id="{d}" value="{title}&lt;div&gt;{code}&lt;/div&gt;"
                style="strokeWidth=10" vertex="1" parent="1">
          <mxGeometry x="0" y="0" width="800" height="600" as="geometry"/>
        </mxCell>
        <mxCell id="{s}" value="{sub}&lt;div&gt;{subcode}&lt;/div&gt;"
                style="rounded=1;verticalAlign=top" vertex="1" parent="1">
          <mxGeometry x="40" y="40" width="700" height="500" as="geometry"/>
        </mxCell>'''

    def _two_pages(self, tmp_path):
        """Две страницы с одинаковой геометрией — как в настоящей схеме."""
        first = self.PAGE.format(d='d1', title='Продажи', code='sales',
                                 s='s1', sub='Заказы', subcode='orders')
        second = self.PAGE.format(d='d2', title='Финансы', code='finance',
                                  s='s2', sub='Платежи', subcode='payments')
        path = tmp_path / 'ddd.drawio'
        path.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<mxfile>'
            f'<diagram name="Первая"><mxGraphModel><root>'
            f'<mxCell id="0"/><mxCell id="1" parent="0"/>{first}'
            '</root></mxGraphModel></diagram>'
            f'<diagram name="Вторая"><mxGraphModel><root>'
            f'<mxCell id="0"/><mxCell id="1" parent="0"/>{second}'
            '</root></mxGraphModel></diagram></mxfile>',
            encoding='utf-8'
        )
        return path

    def test_pages_do_not_mix(self, tmp_path):
        """Координаты у страниц свои: чужой поддомен не должен попасть в домен."""
        domains = DDDParser(str(self._two_pages(tmp_path))).parse()

        by_id = {d.id: d for d in domains}
        assert set(by_id) == {'sales', 'finance'}

        assert {s.id for s in by_id['sales'].subdomains} == {'orders'}
        assert {s.id for s in by_id['finance'].subdomains} == {'payments'}

    def test_unnamed_domain_frames_reported(self, tmp_path):
        """Рамка без подписи не домен, но и молча пропадать не должна."""
        path = tmp_path / 'ddd.drawio'
        path.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<mxfile><diagram name="Стр">'
            '<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="d1" value="Продажи&lt;div&gt;sales&lt;/div&gt;"'
            ' style="strokeWidth=10" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="800" height="600" as="geometry"/></mxCell>'
            '<mxCell id="d2" value="" style="strokeWidth=10" vertex="1" parent="1">'
            '<mxGeometry x="900" y="0" width="800" height="600" as="geometry"/></mxCell>'
            '</root></mxGraphModel></diagram></mxfile>',
            encoding='utf-8'
        )

        tree = DDDParser(str(path)).get_domain_tree()

        assert [d['id'] for d in tree['domains']] == ['sales']
        assert tree['unnamed_domains'] == 1

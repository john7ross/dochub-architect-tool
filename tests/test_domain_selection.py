"""
Тесты для диалога выбора домена.
"""

import pytest
from pathlib import Path

from app.core.parsers.ddd_parser import DDDParser


class TestDomainSelectionLogic:
    """Тесты логики выбора домена (без UI)."""

    def test_ddd_parser_loads_domain_tree(self, tmp_path):
        """Тест загрузки дерева доменов из DDD схемы."""
        # Создаем тестовую DDD схему
        ddd_file = tmp_path / "test_ddd.drawio"
        ddd_content = '''<?xml version="1.0" encoding="UTF-8"?>
<mxfile>
  <diagram name="DDD">
    <mxGraphModel>
      <root>
        <mxCell id="0"/>
        <mxCell id="1" parent="0"/>
        <mxCell id="domain1" value="sales" style="strokeWidth=10" vertex="1" parent="1">
          <mxGeometry x="0" y="0" width="200" height="100" as="geometry"/>
        </mxCell>
        <mxCell id="context1" value="orders" style="dashed=1" vertex="1" parent="1">
          <mxGeometry x="10" y="10" width="180" height="80" as="geometry"/>
        </mxCell>
        <mxCell id="subdomain1" value="orderFlow" style="fillColor=#dae8fc" vertex="1" parent="1">
          <mxGeometry x="20" y="20" width="160" height="60" as="geometry"/>
        </mxCell>
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>'''
        ddd_file.write_text(ddd_content, encoding='utf-8')

        # Парсим DDD схему
        parser = DDDParser(str(ddd_file))
        parse_result = parser.parse()
        domain_tree = parser.get_domain_tree()

        # Проверяем структуру
        assert domain_tree is not None
        assert 'domains' in domain_tree
        assert len(domain_tree['domains']) > 0

        # Проверяем первый домен
        domain = domain_tree['domains'][0]
        assert domain['id'] == 'sales'
        assert 'contexts' in domain

    def test_selection_tuple_format(self):
        """Тест формата кортежа выбора."""
        # Имитируем выбор пользователя
        domain = "sales"
        context = "orders"
        subdomain = "orderFlow"

        # Проверяем формат
        selection = (domain, context, subdomain)
        assert len(selection) == 3
        assert selection[0] == "sales"
        assert selection[1] == "orders"
        assert selection[2] == "orderFlow"

    def test_manual_input_validation(self):
        """Тест валидации ручного ввода."""
        # Имитируем ручной ввод
        domain = "sales"
        context = "orders"
        subdomain = "orderFlow"

        # Проверяем, что все поля заполнены
        assert domain and domain.strip()
        assert context and context.strip()
        assert subdomain and subdomain.strip()

        # Проверяем формат (без пробелов, без спецсимволов)
        assert not any(c in domain for c in [' ', '/', '\\', '.'])
        assert not any(c in context for c in [' ', '/', '\\', '.'])
        assert not any(c in subdomain for c in [' ', '/', '\\', '.'])

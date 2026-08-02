"""
Тесты для трансформации и работы с YAML.
"""

import pytest
from pathlib import Path

from app.core.transformer import Transformer
from app.core.parsers.base_parser import Component, Relation, ParseResult


class TestTransformer:
    """Тесты трансформера."""

    def test_transform_simple_schema(self):
        """Тест трансформации простой схемы."""
        # Создаем результат парсинга
        components = [
            Component(
                id="apiGateway",
                title="API Gateway",
                type="Container",
                technology="C#, ASP.NET Core",
                description="REST API"
            ),
            Component(
                id="database",
                title="Database",
                type="ContainerDb",
                technology="PostgreSQL"
            )
        ]

        relations = [
            Relation(
                from_id="apiGateway",
                to_id="database",
                label="Reads/Writes",
                direction="-->"
            )
        ]

        parse_result = ParseResult(
            is_c4=True,
            components=components,
            relations=relations
        )

        # Трансформируем
        transformer = Transformer("sales", "orders", "orderFlow")
        result = transformer.transform(parse_result)

        # Проверяем структуру
        assert 'aspects' in result
        assert 'components' in result
        assert 'contexts' in result

        # Проверяем компоненты
        assert len(result['components']) == 2
        assert 'dotnet.apiGateway' in result['components']
        assert 'postgres.database' in result['components']

        # Проверяем аспекты
        assert len(result['aspects']) == 2
        assert 'sales.orders.orderFlow.apiGateway' in result['aspects']

        # Проверяем контекст
        assert 'sales.orders.orderFlow' in result['contexts']
        context = result['contexts']['sales.orders.orderFlow']
        assert len(context['components']) == 2
        # DocHub читает связи из uml.$after; секции links в его формате нет
        assert 'links' not in context
        assert context['uml']['$after'].strip()

    def test_transform_with_reused_components(self):
        """Тест трансформации с переиспользованием компонентов."""
        components = [
            Component(
                id="okbChecker",
                title="OKB Checker",
                type="Component",
                technology="MSSQL"
            ),
            Component(
                id="newComponent",
                title="New Component",
                type="Component",
                technology="C#"
            )
        ]

        parse_result = ParseResult(
            is_c4=True,
            components=components,
            relations=[]
        )

        # Указываем что okbChecker переиспользуется
        reused_components = [
            {
                'schema_id': 'okbChecker',
                'id': 'mssql.okbChecker',
                'action': 'reuse'
            }
        ]

        transformer = Transformer("sales", "orders", "orderFlow")
        result = transformer.transform(parse_result, reused_components)

        # Проверяем что создан только новый компонент
        assert len(result['components']) == 1
        assert 'dotnet.newComponent' in result['components']
        assert 'mssql.okbChecker' not in result['components']

        # Проверяем что в контексте оба компонента
        context = result['contexts']['sales.orders.orderFlow']
        assert len(context['components']) == 2
        assert 'mssql.okbChecker' in context['components']
        assert 'dotnet.newComponent' in context['components']

    def test_extract_technology(self):
        """Тест извлечения технологии."""
        transformer = Transformer("test", "test", "test")

        assert transformer._extract_technology("C#, ASP.NET Core") == "dotnet"
        assert transformer._extract_technology("Java, Spring Boot") == "java"
        assert transformer._extract_technology("PostgreSQL") == "postgres"
        assert transformer._extract_technology("Node.js") == "nodejs"
        assert transformer._extract_technology("Unknown Tech") == "component"
        assert transformer._extract_technology(None) == "component"

    def test_map_entity_type(self):
        """Тест маппинга типов."""
        transformer = Transformer("test", "test", "test")

        # В репозитории принято person (7 вхождений против 2 у actor)
        assert transformer._map_entity_type("Person") == "person"
        # entity: system в репозитории не используется вовсе — верхнеуровневые
        # блоки описываются обычными компонентами
        assert transformer._map_entity_type("System") == "component"
        assert transformer._map_entity_type("Container") == "component"
        assert transformer._map_entity_type("ContainerDb") == "database"
        assert transformer._map_entity_type("Component") == "component"



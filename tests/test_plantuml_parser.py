"""
Тесты для PlantUML парсера.
"""

import pytest
from pathlib import Path
from app.core.parsers.plantuml_parser import PlantUMLParser


class TestPlantUMLParser:
    """Тесты PlantUML парсера."""

    def test_parse_c4_container(self, tmp_path):
        """Тест парсинга C4 Container диаграммы."""
        # Создаем тестовый файл
        puml_content = """@startuml
!include https://raw.githubusercontent.com/plantuml-stdlib/C4-PlantUML/master/C4_Container.puml

Person(user, "User", "End user")
System_Boundary(system, "System") {
    Container(web, "Web App", "React", "Frontend")
    Container(api, "API", "Python", "Backend")
}

Rel(user, web, "Uses")
Rel(web, api, "Calls")

@enduml"""

        test_file = tmp_path / "test.puml"
        test_file.write_text(puml_content, encoding='utf-8')

        # Парсим
        parser = PlantUMLParser(str(test_file))
        result = parser.parse()

        # Проверки
        assert result.is_c4 is True
        assert len(result.components) == 3  # user, web, api
        assert len(result.relations) == 2

        # Проверяем компоненты
        component_ids = [c.id for c in result.components]
        assert "user" in component_ids
        assert "web" in component_ids
        assert "api" in component_ids

        # Проверяем типы
        web_comp = next(c for c in result.components if c.id == "web")
        assert web_comp.type == "Container"
        assert web_comp.technology == "React"
        assert web_comp.title == "Web App"

    def test_parse_non_c4(self, tmp_path):
        """Тест парсинга не-C4 диаграммы."""
        puml_content = """@startuml
class User {
    +name: string
    +email: string
}
@enduml"""

        test_file = tmp_path / "test.puml"
        test_file.write_text(puml_content, encoding='utf-8')

        parser = PlantUMLParser(str(test_file))
        result = parser.parse()

        assert result.is_c4 is False
        assert "C4" in result.error_message

    def test_parse_invalid_file(self):
        """Тест парсинга несуществующего файла."""
        with pytest.raises(FileNotFoundError):
            parser = PlantUMLParser("nonexistent.puml")

    def test_parse_relations(self, tmp_path):
        """Тест парсинга связей."""
        puml_content = """@startuml
!include https://raw.githubusercontent.com/plantuml-stdlib/C4-PlantUML/master/C4_Container.puml

Container(a, "A")
Container(b, "B")

Rel(a, b, "calls", "HTTP")

@enduml"""

        test_file = tmp_path / "test.puml"
        test_file.write_text(puml_content, encoding='utf-8')

        parser = PlantUMLParser(str(test_file))
        result = parser.parse()

        assert len(result.relations) >= 1

        # Проверяем первую связь
        rel1 = result.relations[0]
        assert rel1.from_id == "a"
        assert rel1.to_id == "b"
        assert rel1.label == "calls"

    def test_parse_system_boundary(self, tmp_path):
        """Тест парсинга System_Boundary."""
        puml_content = """@startuml
!include https://raw.githubusercontent.com/plantuml-stdlib/C4-PlantUML/master/C4_Container.puml

System_Boundary(sys, "My System") {
    Container(web, "Web")
    Container(db, "Database")
}

@enduml"""

        test_file = tmp_path / "test.puml"
        test_file.write_text(puml_content, encoding='utf-8')

        parser = PlantUMLParser(str(test_file))
        result = parser.parse()

        assert result.is_c4 is True
        assert len(result.components) >= 2


class TestRealWorldMacros:
    """
    Как макросы C4 пишут на настоящих схемах.

    Обязательными аргументами дело не ограничивается: дальше идут именованные
    ($link, $tags, $sprite), а описание бывает пустым. На таких файлах разбор
    возвращал ноль элементов, и инструмент просил выбрать корень из пустого
    списка — 12 схем из набора компании.
    """

    def test_named_arguments_after_description(self, tmp_path):
        path = tmp_path / 'link.puml'
        path.write_text("""@startuml
System_Boundary(b, "WEB") {
  Component(site, "Sayt", "React, JS", "Vidzhet", $link="")
}
@enduml
""", encoding='utf-8')

        result = PlantUMLParser(str(path)).parse()

        assert [c.title for c in result.components] == ['Sayt']
        assert result.components[0].technology == 'React, JS'

    def test_empty_description(self, tmp_path):
        path = tmp_path / 'empty.puml'
        path.write_text("""@startuml
Boundary(p, "Prodazha") {
  Container(p1, "Zagotovka", "Process", "")
}
@enduml
""", encoding='utf-8')

        result = PlantUMLParser(str(path)).parse()

        assert [c.title for c in result.components] == ['Zagotovka']

"""
Парсер PlantUML схем с поддержкой C4 нотации.

Проверяет наличие C4 макросов и извлекает компоненты и связи.
"""

import re
from typing import List, Optional

from app.core.parsers.base_parser import (
    BaseParser,
    Component,
    Relation,
    ParseResult
)
from app.utils.logger import get_logger


class PlantUMLParser(BaseParser):
    """Парсер PlantUML схем."""

    # C4 макросы
    C4_MACROS = [
        'Person',
        'Person_Ext',
        'System',
        'System_Ext',
        'System_Boundary',
        'Container',
        'ContainerDb',
        'Container_Boundary',
        'Component',
        'Rel',
        'BiRel',
        'Rel_U',
        'Rel_D',
        'Rel_L',
        'Rel_R'
    ]

    def __init__(self, file_path: str):
        """Инициализация парсера."""
        super().__init__(file_path)
        self.logger = get_logger()
        self.content = self._read_file()

    def _read_file(self) -> str:
        """Прочитать файл."""
        with open(self.file_path, 'r', encoding='utf-8') as f:
            return f.read()

    def is_c4_notation(self) -> bool:
        """
        Проверка наличия C4 макросов.

        Returns:
            True если найдены C4 макросы
        """
        for macro in self.C4_MACROS:
            # Ищем макрос с открывающей скобкой
            pattern = rf'\b{macro}\s*\('
            if re.search(pattern, self.content):
                self.logger.debug(f"Found C4 macro: {macro}")
                return True

        return False

    def parse(self) -> ParseResult:
        """
        Парсинг PlantUML схемы.

        Returns:
            ParseResult с компонентами и связями
        """
        # Проверка C4 нотации
        if not self.is_c4_notation():
            return ParseResult(
                is_c4=False,
                error_message="Схема не содержит C4 макросов.\n\n"
                             "Приложение поддерживает только физические схемы "
                             "в нотации C4 (PlantUML).\n\n"
                             "Ожидаемые макросы: Person(), Container(), System(), Rel()"
            )

        # Парсинг компонентов
        components = self._parse_components()

        # Парсинг связей
        relations = self._parse_relations()

        self.logger.info(
            f"Parsed PlantUML: {len(components)} components, "
            f"{len(relations)} relations"
        )

        return ParseResult(
            is_c4=True,
            components=components,
            relations=relations
        )

    def _parse_components(self) -> List[Component]:
        """
        Парсинг компонентов.

        Форматы:
        - Person(id, "Title", "Description")
        - Container(id, "Title", "Technology", "Description")
        - System(id, "Title", "Description")
        - Component(id, "Title", "Technology", "Description")
        """
        components = []

        # Паттерны для разных типов компонентов
        patterns = {
            'Person': r'Person(?:_Ext)?\s*\(\s*(\w+)\s*,\s*"([^"]+)"(?:\s*,\s*"([^"]+)")?\s*\)',
            'System': r'System(?:_Ext)?\s*\(\s*(\w+)\s*,\s*"([^"]+)"(?:\s*,\s*"([^"]+)")?\s*\)',
            'Container': r'Container(?:Db)?\s*\(\s*(\w+)\s*,\s*"([^"]+)"(?:\s*,\s*"([^"]+)")?(?:\s*,\s*"([^"]+)")?\s*\)',
            'Component': r'Component\s*\(\s*(\w+)\s*,\s*"([^"]+)"(?:\s*,\s*"([^"]+)")?(?:\s*,\s*"([^"]+)")?\s*\)',
        }

        for comp_type, pattern in patterns.items():
            matches = re.finditer(pattern, self.content)

            for match in matches:
                groups = match.groups()
                comp_id = groups[0]
                title = groups[1]

                # Для Container и Component: technology, description
                # Для Person и System: description
                if comp_type in ['Container', 'Component']:
                    technology = groups[2] if len(groups) > 2 and groups[2] else None
                    description = groups[3] if len(groups) > 3 and groups[3] else None
                else:
                    technology = None
                    description = groups[2] if len(groups) > 2 and groups[2] else None

                component = Component(
                    id=comp_id,
                    title=title,
                    type=comp_type,
                    technology=technology,
                    description=description
                )

                components.append(component)
                self.logger.debug(f"Found component: {comp_id} ({comp_type})")

        return components

    def _parse_relations(self) -> List[Relation]:
        """
        Парсинг связей.

        Форматы:
        - Rel(from, to, "Label")
        - Rel(from, to, "Label", "Protocol")
        - BiRel(from, to, "Label")
        - Rel_U/D/L/R(from, to, "Label")
        """
        relations = []

        # Паттерн для всех типов связей
        pattern = r'(?:Bi)?Rel(?:_[UDLR])?\s*\(\s*(\w+)\s*,\s*(\w+)\s*(?:,\s*"([^"]+)")?(?:,\s*"([^"]+)")?\s*\)'

        matches = re.finditer(pattern, self.content)

        for match in matches:
            from_id = match.group(1)
            to_id = match.group(2)
            label = match.group(3) if match.group(3) else None

            # Определяем направление
            rel_type = match.group(0).split('(')[0].strip()
            if rel_type.startswith('BiRel'):
                direction = '<-->'
            else:
                direction = '-->'

            relation = Relation(
                from_id=from_id,
                to_id=to_id,
                label=label,
                direction=direction
            )

            relations.append(relation)
            self.logger.debug(f"Found relation: {from_id} {direction} {to_id}")

        return relations

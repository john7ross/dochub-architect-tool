"""
Парсер DrawIO схем с поддержкой C4 нотации.

Проверяет наличие C4 атрибутов и извлекает компоненты и связи.
Поддерживает многостраничные файлы.
"""

import base64
import html
import re
import urllib.parse
import xml.etree.ElementTree as ET
import zlib
from typing import List, Optional
from pathlib import Path

from app.core.parsers.base_parser import (
    BaseParser,
    Component,
    Relation,
    ParseResult
)
from app.utils.logger import get_logger


#: Разделители текста внутри подписи: без них соседние слова слипаются
_BREAKS = re.compile(r'<\s*(br|/div|/p|/li)\s*/?>', re.I)
#: Любой другой тег разметки
_TAGS = re.compile(r'<[^>]+>')


def clean_label(text: Optional[str]) -> str:
    """
    Привести подпись DrawIO к обычному тексту.

    В DrawIO подпись — это HTML: переносы строк, <b>, <font>, &nbsp;. Из неё
    строятся и заголовок, и идентификатор DocHub, поэтому разметка попадала в
    оба: идентификаторы вида `fontStyleFontSize11px...`, а перенос строки в
    заголовке разрывал YAML, который потом никто не мог прочитать.

    Args:
        text: Подпись как она лежит в файле

    Returns:
        Однострочный текст без разметки
    """
    if not text:
        return ''

    without_markup = _TAGS.sub('', _BREAKS.sub(' ', text))
    return ' '.join(html.unescape(without_markup).split())


class DrawIOParser(BaseParser):
    """Парсер DrawIO схем."""

    # C4 атрибуты для определения нотации
    C4_ATTRIBUTES = ['c4Name', 'c4Type', 'c4Technology', 'c4Description']

    def __init__(self, file_path: str):
        """Инициализация парсера."""
        super().__init__(file_path)
        self.logger = get_logger()
        self.tree = self._parse_xml()
        self.root = self.tree.getroot()

    def _parse_xml(self) -> ET.ElementTree:
        """Парсинг XML файла."""
        try:
            tree = ET.parse(self.file_path)
        except ET.ParseError as e:
            self.logger.error(f"Failed to parse DrawIO file: {e}")
            raise

        return self._expand_compressed(tree)

    def _expand_compressed(self, tree: ET.ElementTree) -> ET.ElementTree:
        """
        Развернуть страницы, сохранённые в сжатом виде.

        DrawIO умеет класть содержимое страницы в <diagram> одной строкой:
        base64 -> deflate -> url-encoding. В таком файле нет ни mxCell, ни
        object, и без распаковки схема выглядит пустой.

        Args:
            tree: Разобранное дерево файла

        Returns:
            Дерево, где страницы заменены распакованным содержимым
        """
        root = tree.getroot()

        for diagram in root.iter('diagram'):
            payload = (diagram.text or '').strip()

            # У несжатой страницы содержимое лежит дочерними узлами
            if not payload or len(diagram):
                continue

            try:
                raw = zlib.decompress(base64.b64decode(payload), -15)
                xml_text = urllib.parse.unquote(raw.decode('utf-8'))
                model = ET.fromstring(xml_text)
            except (ValueError, zlib.error, ET.ParseError, UnicodeDecodeError) as e:
                self.logger.warning(f"Не удалось распаковать страницу схемы: {e}")
                continue

            diagram.text = None
            diagram.append(model)
            self.logger.debug("Страница схемы распакована")

        return tree

    def is_c4_notation(self) -> bool:
        """
        Проверка наличия C4 атрибутов.

        Returns:
            True если найдены C4 атрибуты
        """
        # C4-атрибуты хранятся на <object>-обёртках, но встречаются и на mxCell
        for tag in ('object', 'mxCell'):
            for element in self.root.iter(tag):
                for attr in self.C4_ATTRIBUTES:
                    if attr in element.attrib:
                        self.logger.debug(f"Found C4 attribute: {attr}")
                        return True

        return False

    def parse(self) -> ParseResult:
        """
        Парсинг DrawIO схемы.

        Returns:
            ParseResult с компонентами и связями
        """
        # Проверка C4 нотации
        if not self.is_c4_notation():
            return ParseResult(
                is_c4=False,
                error_message="Схема не содержит C4 атрибутов.\n\n"
                             "Приложение поддерживает только физические схемы "
                             "в нотации C4 (DrawIO).\n\n"
                             "Ожидаемые атрибуты: c4Name, c4Type, c4Technology"
            )

        # Парсинг всех страниц.
        # Каждая помечается номером: в одном файле бывает по десятку
        # независимых диаграмм, и смешивать их нельзя
        components = []
        relations = []
        pages = []

        for index, diagram in enumerate(self.root.iter('diagram')):
            name = diagram.attrib.get('name') or f'Страница {index + 1}'
            pages.append(name)

            page_components, page_relations = self._parse_page(diagram)

            for component in page_components:
                component.page = index
                component.page_name = name
            for relation in page_relations:
                relation.page = index

            components.extend(page_components)
            relations.extend(page_relations)

        self.logger.info(
            f"Parsed DrawIO: {len(components)} components, "
            f"{len(relations)} relations, {len(pages)} pages"
        )

        return ParseResult(
            is_c4=True,
            components=components,
            relations=relations,
            pages=pages
        )

    def _parse_page(self, diagram: ET.Element) -> tuple[List[Component], List[Relation]]:
        """
        Парсинг одной страницы диаграммы.

        Args:
            diagram: XML элемент diagram

        Returns:
            Кортеж (компоненты, связи)
        """
        components = []
        relations = []

        # Получаем mxGraphModel
        graph_model = diagram.find('.//mxGraphModel')
        if graph_model is None:
            return components, relations

        # Компоненты: <object>-обёртки с C4-атрибутами (и mxCell для совместимости)
        geometries = {}

        for tag in ('object', 'mxCell'):
            for element in graph_model.iter(tag):
                if any(attr in element.attrib for attr in self.C4_ATTRIBUTES):
                    component = self._parse_component(element)
                    if component:
                        components.append(component)
                        geometries[component.id] = self._get_geometry(element)

        self._assign_parents(components, geometries)

        # Связи: обычно это атрибут edge="1", но встречается и запись
        # внутри style. Проверять просто вхождение "edge" в style нельзя —
        # под него попадает edgeStyle у обычных фигур.
        for cell in graph_model.iter('mxCell'):
            style = cell.attrib.get('style', '')
            is_edge = (
                cell.attrib.get('edge')
                or re.search(r'(^|;)\s*edge\s*=\s*1', style)
            )
            if is_edge:
                relation = self._parse_relation(cell)
                if relation:
                    relations.append(relation)

        return components, relations

    @staticmethod
    def _get_geometry(element: ET.Element) -> Optional[tuple]:
        """
        Извлечь геометрию элемента как (x, y, width, height).

        Args:
            element: <object> или <mxCell>

        Returns:
            Кортеж координат или None, если геометрии нет
        """
        geometry = element.find('.//mxGeometry')
        if geometry is None:
            return None

        try:
            return (
                float(geometry.attrib.get('x', 0)),
                float(geometry.attrib.get('y', 0)),
                float(geometry.attrib.get('width', 0)),
                float(geometry.attrib.get('height', 0))
            )
        except ValueError:
            return None

    def _assign_parents(self, components: List[Component], geometries: dict) -> None:
        """
        Восстановить иерархию: для каждого элемента найти ближайшую
        объемлющую границу.

        В C4-шаблоне DrawIO вложенность обычно не выражена атрибутом parent —
        элементы просто нарисованы внутри рамки. Поэтому принадлежность
        определяется геометрически: берётся наименьшая по площади граница,
        полностью содержащая элемент.

        Args:
            components: Компоненты страницы (изменяются на месте)
            geometries: Словарь {id компонента: (x, y, w, h)}
        """
        boundaries = [
            comp for comp in components
            if comp.is_boundary and geometries.get(comp.id)
        ]

        if not boundaries:
            return

        def contains(outer: tuple, inner: tuple) -> bool:
            ox, oy, ow, oh = outer
            ix, iy, iw, ih = inner
            return (
                ox <= ix and oy <= iy
                and ix + iw <= ox + ow
                and iy + ih <= oy + oh
            )

        for comp in components:
            geometry = geometries.get(comp.id)
            if not geometry:
                continue

            enclosing = [
                boundary for boundary in boundaries
                if boundary.id != comp.id
                and contains(geometries[boundary.id], geometry)
            ]

            if enclosing:
                # Ближайшая граница — наименьшая по площади
                comp.parent = min(
                    enclosing,
                    key=lambda b: geometries[b.id][2] * geometries[b.id][3]
                ).id

    def _parse_component(self, cell: ET.Element) -> Optional[Component]:
        """
        Парсинг компонента из mxCell.

        Args:
            cell: XML элемент mxCell

        Returns:
            Component или None
        """
        cell_id = cell.attrib.get('id')
        if not cell_id:
            return None

        # Извлекаем C4 атрибуты
        c4_name = clean_label(cell.attrib.get('c4Name'))
        c4_type = cell.attrib.get('c4Type')
        c4_technology = clean_label(cell.attrib.get('c4Technology'))
        c4_description = clean_label(cell.attrib.get('c4Description'))

        # Если нет имени, пытаемся взять из value (mxCell) или label (object)
        title = c4_name or clean_label(
            cell.attrib.get('value') or cell.attrib.get('label', '')
        )

        # Определяем тип компонента
        comp_type = self._map_c4_type(c4_type)

        component = Component(
            id=cell_id,
            title=title,
            type=comp_type,
            technology=c4_technology,
            description=c4_description,
            is_boundary=comp_type in ('System_Boundary', 'Container_Boundary')
        )

        self.logger.debug(f"Found component: {cell_id} ({comp_type})")
        return component

    def _parse_relation(self, cell: ET.Element) -> Optional[Relation]:
        """
        Парсинг связи из mxCell.

        Args:
            cell: XML элемент mxCell с edge

        Returns:
            Relation или None
        """
        source = cell.attrib.get('source')
        target = cell.attrib.get('target')

        if not source or not target:
            return None

        # Извлекаем label
        label = clean_label(cell.attrib.get('value', ''))

        # Определяем направление по стилю
        style = cell.attrib.get('style', '')
        if 'endArrow=none' in style and 'startArrow=classic' in style:
            direction = '<--'
        elif 'endArrow=classic' in style and 'startArrow=classic' in style:
            direction = '<-->'
        else:
            direction = '-->'

        relation = Relation(
            from_id=source,
            to_id=target,
            label=label,
            direction=direction
        )

        self.logger.debug(f"Found relation: {source} {direction} {target}")
        return relation

    def _map_c4_type(self, c4_type: Optional[str]) -> str:
        """
        Маппинг C4 типа в стандартный тип.

        Args:
            c4_type: Тип из c4Type атрибута

        Returns:
            Стандартный тип компонента
        """
        if not c4_type:
            return 'Component'

        # Маппинг типов
        type_map = {
            'person': 'Person',
            'external_person': 'Person_Ext',
            'system': 'System',
            'external_system': 'System_Ext',
            'container': 'Container',
            'database': 'ContainerDb',
            'component': 'Component',
            'system_boundary': 'System_Boundary',
            'container_boundary': 'Container_Boundary',
            # Значения, которые реально пишет DrawIO C4-шаблон
            'software system': 'System',
            'systemscopeboundary': 'System_Boundary',
            'containerscopeboundary': 'Container_Boundary'
        }

        return type_map.get(c4_type.lower(), 'Component')

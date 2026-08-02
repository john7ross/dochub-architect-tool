"""
Парсер DDD схемы для извлечения иерархии доменов.

Извлекает 3-уровневую иерархию:
- Домен (Domain) - strokeWidth=10
- Ограниченный контекст (Bounded Context) - dashed=1
- Поддомен (Subdomain) - обычные блоки

Также извлекает команды и ответственных.
"""

import xml.etree.ElementTree as ET
from typing import Dict, List, Optional
from dataclasses import dataclass
from pathlib import Path

from app.core.parsers.geometry import find_innermost, get_geometry
from app.utils.logger import get_logger


@dataclass
class Subdomain:
    """Поддомен."""
    id: str
    title: str
    team: Optional[str] = None
    responsible: Optional[str] = None


@dataclass
class BoundedContext:
    """Ограниченный контекст."""
    id: str
    title: str
    subdomains: List[Subdomain] = None

    def __post_init__(self):
        if self.subdomains is None:
            self.subdomains = []


@dataclass
class Domain:
    """Домен."""
    id: str
    title: str
    contexts: List[BoundedContext] = None
    # Часть поддоменов нарисована прямо в домене, минуя ограниченный контекст
    subdomains: List[Subdomain] = None

    def __post_init__(self):
        if self.contexts is None:
            self.contexts = []
        if self.subdomains is None:
            self.subdomains = []


class DDDParser:
    # Блоки нередко выступают за рамку на несколько пикселей —
    # без допуска такие поддомены теряются
    TOLERANCE = 12.0

    # Заливка блока ограниченного контекста в шаблоне DDD-схемы
    CONTEXT_FILL = 'fillColor=#f5f5f5'

    """Парсер DDD схемы."""

    def __init__(self, file_path: str):
        """
        Инициализация парсера.

        Args:
            file_path: Путь к DDD схеме (.drawio)
        """
        self.file_path = Path(file_path)
        self.logger = get_logger()

        if not self.file_path.exists():
            raise FileNotFoundError(f"DDD schema not found: {file_path}")

        self.tree = self._parse_xml()
        self.root = self.tree.getroot()
        self.cells = {}  # id -> cell mapping
        # Рамки доменов без подписи: о них сообщаем, а не выбрасываем молча
        self._unnamed = []

    def _parse_xml(self) -> ET.ElementTree:
        """Парсинг XML файла."""
        try:
            return ET.parse(self.file_path)
        except ET.ParseError as e:
            self.logger.error(f"Failed to parse DDD schema: {e}")
            raise

    def parse(self) -> List[Domain]:
        """
        Парсинг DDD схемы.

        Страницы разбираются по отдельности. Вложенность здесь геометрическая,
        а система координат у каждой страницы своя: если пройти файл целиком,
        блоки соседней страницы окажутся внутри рамок этой, и в дереве
        появятся поддомены, которых на схеме нет.

        Returns:
            Список доменов с иерархией
        """
        self._build_cell_map()
        self._unnamed = []

        pages = self.root.findall('diagram') or [self.root]
        domains: List[Domain] = []

        for page in pages:
            self._page = page
            self._index_cells()
            domains.extend(self._find_domains())

        self.logger.info(
            f"Parsed DDD: {len(domains)} domains from {len(pages)} pages"
        )
        return domains

    def _build_cell_map(self):
        """Построить карту всех ячеек."""
        for cell in self.root.iter('mxCell'):
            cell_id = cell.attrib.get('id')
            if cell_id:
                self.cells[cell_id] = cell

    def _find_domains(self) -> List[Domain]:
        """
        Найти все домены.

        Returns:
            Список доменов
        """
        domains = []

        for cell in self._domain_cells:
            domain = self._parse_domain(cell)
            if domain:
                domains.append(domain)

        return domains

    def _parse_domain(self, cell: ET.Element) -> Optional[Domain]:
        """
        Парсинг домена.

        Args:
            cell: XML элемент домена

        Returns:
            Domain или None
        """
        domain_id, domain_title = self._extract_id_and_title(cell)
        if not domain_id:
            # Рамка домена без подписи. Достраивать название по содержимому
            # нельзя — внутри лежат поддомены и плашки «Ответственные», и
            # выбор был бы гаданием. Но и терять рамку молча нельзя: домена
            # просто не окажется среди вариантов размещения
            self._unnamed.append(cell.attrib.get('id', '?'))
            return None

        domain = Domain(id=domain_id, title=domain_title)

        # Находим ограниченные контексты внутри домена
        domain.contexts = self._find_bounded_contexts(cell)

        # Поддомены, лежащие в домене напрямую: если их не подобрать,
        # теряется около трети дерева
        domain.subdomains = self._find_direct_subdomains(cell)

        self.logger.debug(
            f"Found domain: {domain_id} with {len(domain.contexts)} contexts"
        )
        return domain

    def _find_bounded_contexts(self, domain_cell: ET.Element) -> List[BoundedContext]:
        """
        Найти ограниченные контексты внутри домена.

        Вложенность в DDD-схеме не выражена атрибутом parent — блоки просто
        нарисованы внутри рамки домена, поэтому определяем её геометрически.

        Args:
            domain_cell: XML элемент домена

        Returns:
            Список ограниченных контекстов
        """
        domain_id = domain_cell.attrib.get('id')
        contexts = []

        for cell in self._context_cells:
            if self._owner_of(cell.attrib.get('id'), self._domain_ids) != domain_id:
                continue

            context = self._parse_bounded_context(cell)
            if context:
                contexts.append(context)

        return contexts

    def _index_cells(self) -> None:
        """Построить индексы ячеек и их геометрии."""
        self._geometries = {}
        self._parents = {}
        self._domain_cells = []
        self._context_cells = []
        self._plain_cells = []
        # Пунктирные плашки «Ответственные: ...» — не часть дерева,
        # но из них берутся команда и ответственный
        self._note_cells = []

        # Только ячейки текущей страницы: см. parse()
        source = getattr(self, '_page', None) or self.root

        for cell in source.iter('mxCell'):
            cell_id = cell.attrib.get('id')
            if not cell_id:
                continue

            geometry = get_geometry(cell)
            if geometry:
                self._geometries[cell_id] = geometry

            parent = cell.attrib.get('parent')
            # parent="1" — это корень листа, а не вложенность
            if parent and parent not in ('0', '1'):
                self._parents[cell_id] = parent

            style = cell.attrib.get('style', '')
            if cell.attrib.get('edge'):
                continue

            # Классы блоков различаются оформлением шаблона DDD-схемы.
            # Пунктирные рамки — это плашки «Ответственные: ...», а не
            # ограниченные контексты, и в дерево они не входят.
            if 'strokeWidth=10' in style:
                self._domain_cells.append(cell)
            elif self.CONTEXT_FILL in style and 'verticalAlign=bottom' in style:
                self._context_cells.append(cell)
            elif 'rounded=1' in style and 'verticalAlign=top' in style:
                self._plain_cells.append(cell)
            elif 'dashed=1' in style:
                self._note_cells.append(cell)

        self._domain_ids = [c.attrib['id'] for c in self._domain_cells]
        self._context_ids = [c.attrib['id'] for c in self._context_cells]

    def _owner_of(self, cell_id: Optional[str], candidates: List[str]) -> Optional[str]:
        """
        Найти ближайшую объемлющую рамку.

        Args:
            cell_id: Идентификатор ячейки
            candidates: Идентификаторы возможных родителей

        Returns:
            Идентификатор родителя или None
        """
        if not cell_id:
            return None

        # Явная вложенность через parent, если она задана: так рисуют
        # некоторые редакторы. В шаблоне DDD её нет, там всё плоское
        explicit = self._parents.get(cell_id)
        if explicit and explicit in candidates:
            return explicit

        return find_innermost(
            cell_id, self._geometries, candidates, tolerance=self.TOLERANCE
        )

    def _find_direct_subdomains(self, domain_cell: ET.Element) -> List[Subdomain]:
        """
        Найти поддомены, лежащие в домене вне ограниченных контекстов.

        Args:
            domain_cell: XML элемент домена

        Returns:
            Список поддоменов
        """
        domain_id = domain_cell.attrib.get('id')
        subdomains = []

        for cell in self._plain_cells:
            cell_id = cell.attrib.get('id')

            # Поддомены внутри ОК разбираются отдельно
            if self._owner_of(cell_id, self._context_ids):
                continue
            if self._owner_of(cell_id, self._domain_ids) != domain_id:
                continue

            subdomain = self._parse_subdomain(cell)
            if subdomain:
                subdomains.append(subdomain)

        return subdomains

    def _parse_bounded_context(self, cell: ET.Element) -> Optional[BoundedContext]:
        """
        Парсинг ограниченного контекста.

        Args:
            cell: XML элемент контекста

        Returns:
            BoundedContext или None
        """
        context_id, context_title = self._extract_id_and_title(cell)
        if not context_id:
            return None

        context = BoundedContext(id=context_id, title=context_title)

        # Находим поддомены внутри контекста
        context.subdomains = self._find_subdomains(cell)

        self.logger.debug(
            f"Found context: {context_id} with {len(context.subdomains)} subdomains"
        )
        return context

    def _find_subdomains(self, context_cell: ET.Element) -> List[Subdomain]:
        """
        Найти поддомены внутри контекста.

        Args:
            context_cell: XML элемент контекста

        Returns:
            Список поддоменов
        """
        context_id = context_cell.attrib.get('id')
        subdomains = []

        for cell in self._plain_cells:
            if self._owner_of(cell.attrib.get('id'), self._context_ids) != context_id:
                continue

            subdomain = self._parse_subdomain(cell)
            if subdomain:
                subdomains.append(subdomain)

        return subdomains

    def _parse_subdomain(self, cell: ET.Element) -> Optional[Subdomain]:
        """
        Парсинг поддомена.

        Args:
            cell: XML элемент поддомена

        Returns:
            Subdomain или None
        """
        subdomain_id, subdomain_title = self._extract_id_and_title(cell)
        if not subdomain_id:
            return None

        # Ищем команду и ответственного рядом
        team, responsible = self._find_team_and_responsible(cell)

        subdomain = Subdomain(
            id=subdomain_id,
            title=subdomain_title,
            team=team,
            responsible=responsible
        )

        self.logger.debug(f"Found subdomain: {subdomain_id}")
        return subdomain

    def _extract_id_and_title(self, cell: ET.Element) -> tuple[Optional[str], str]:
        """
        Извлечь ID и название из ячейки.

        Формат value: "<b>Название</b><br><b>id</b>"

        Args:
            cell: XML элемент

        Returns:
            Кортеж (id, title)
        """
        value = cell.attrib.get('value', '')

        # Подпись размечена HTML: перевод строки задаётся тегами, а не \n
        import html
        import re

        # Открывающий <div> тоже начинает строку: в подписях вида
        # "Маркетинг<div>orders</div>" иначе склеятся название и id
        text = re.sub(r'<br\s*/?>|</?div[^>]*>|</?p[^>]*>', '\n', value, flags=re.I)
        text = re.sub(r'<[^>]+>', '', text)
        # Без раскодирования сущностей &nbsp; попадает прямо в заголовок
        text = html.unescape(text).replace('\xa0', ' ')

        lines = [line.strip() for line in text.split('\n') if line.strip()]

        # Идентификатор — латиницей и без пробелов, обычно последней строкой
        for index in range(len(lines) - 1, -1, -1):
            candidate = lines[index]
            if re.fullmatch(r'[A-Za-z][A-Za-z0-9_.\-]*', candidate):
                title = ' '.join(lines[:index] + lines[index + 1:]).strip()
                return candidate, title or candidate

        parts = [p for line in lines for p in line.split() if p]

        if len(parts) >= 2:
            # Последняя часть - ID (обычно на английском)
            # Остальное - название
            cell_id = parts[-1]
            title = ' '.join(parts[:-1])
            return cell_id, title
        elif len(parts) == 1:
            # Только одна часть - используем как ID и title
            return parts[0], parts[0]

        return None, ''

    def _find_team_and_responsible(
        self,
        cell: ET.Element
    ) -> tuple[Optional[str], Optional[str]]:
        """
        Найти команду и ответственного за поддомен.

        Ответственные выписаны отдельными пунктирными плашками вида
        «Ответственные: Шишатский / ФД». Внутрь блока поддомена они не
        вложены — лежат рядом, поэтому ищем ближайшую по расстоянию между
        центрами и берём её, только если она не дальше размера самого блока.

        Args:
            cell: XML элемент поддомена

        Returns:
            Кортеж (team, responsible)
        """
        geometry = self._geometries.get(cell.attrib.get('id'))
        if not geometry or not self._note_cells:
            return None, None

        def centre(rect):
            return rect[0] + rect[2] / 2, rect[1] + rect[3] / 2

        own_x, own_y = centre(geometry)
        reach = max(geometry[2], geometry[3])

        nearest, best = None, None
        for note in self._note_cells:
            note_geometry = self._geometries.get(note.attrib.get('id'))
            if not note_geometry:
                continue
            note_x, note_y = centre(note_geometry)
            distance = ((own_x - note_x) ** 2 + (own_y - note_y) ** 2) ** 0.5
            if best is None or distance < best:
                nearest, best = note, distance

        if nearest is None or best > reach:
            return None, None

        return self._parse_responsible(nearest)

    @staticmethod
    def _parse_responsible(cell: ET.Element) -> tuple[Optional[str], Optional[str]]:
        """
        Разобрать плашку ответственных.

        Встречаются оба написания: фамилия одной строкой с подписью
        «Ответственные:» и подпись, разложенная на несколько строк. Короткая
        последняя строка — это обозначение команды.

        Args:
            cell: XML элемент плашки

        Returns:
            Кортеж (team, responsible)
        """
        import html
        import re

        raw = cell.attrib.get('value', '')
        text = re.sub(r'<br\s*/?>|</?div[^>]*>|</?p[^>]*>', '\n', raw, flags=re.I)
        text = re.sub(r'<[^>]+>', '', text)
        lines = [
            line.strip()
            for line in html.unescape(text).replace('\xa0', ' ').split('\n')
            if line.strip()
        ]

        if not lines:
            return None, None

        lines[0] = re.sub(r'^Ответственные\s*:?\s*', '', lines[0], flags=re.I)
        lines = [line for line in lines if line]

        if not lines:
            return None, None

        team = None
        if len(lines) > 1 and len(lines[-1]) <= 12:
            team = lines.pop()

        responsible = ' '.join(lines) or None
        return team, responsible

    def get_domain_tree(self) -> Dict:
        """
        Получить дерево доменов для UI.

        Returns:
            Словарь с деревом доменов
        """
        domains = self.parse()

        tree = []
        for domain in domains:
            domain_node = {
                'id': domain.id,
                'title': domain.title,
                'contexts': []
            }

            for context in domain.contexts:
                context_node = {
                    'id': context.id,
                    'title': context.title,
                    'subdomains': []
                }

                for subdomain in context.subdomains:
                    subdomain_node = {
                        'id': subdomain.id,
                        'title': subdomain.title,
                        'team': subdomain.team,
                        'responsible': subdomain.responsible
                    }
                    context_node['subdomains'].append(subdomain_node)

                domain_node['contexts'].append(context_node)

            tree.append(domain_node)

        return {
            'domains': tree,
            # Рамки, размеченные как домен, но без названия: их не разместить,
            # и человек должен знать, что они есть
            'unnamed_domains': len(self._unnamed),
        }

    def find_subdomain_info(self, domain_id: str, context_id: str,
                           subdomain_id: str) -> Optional[Dict]:
        """
        Найти информацию о поддомене.

        Args:
            domain_id: ID домена
            context_id: ID контекста
            subdomain_id: ID поддомена

        Returns:
            Словарь с информацией или None
        """
        domains = self.parse()

        for domain in domains:
            if domain.id != domain_id:
                continue

            for context in domain.contexts:
                if context.id != context_id:
                    continue

                for subdomain in context.subdomains:
                    if subdomain.id == subdomain_id:
                        return {
                            'domain': domain.id,
                            'context': context.id,
                            'subdomain': subdomain.id,
                            'team': subdomain.team,
                            'responsible': subdomain.responsible,
                            'full_path': f"{domain.id}.{context.id}.{subdomain.id}"
                        }

        return None

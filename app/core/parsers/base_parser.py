"""
Базовый класс для парсеров схем.

Все парсеры должны наследоваться от BaseParser и реализовывать метод parse().
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass


@dataclass
class Component:
    """Компонент на схеме."""
    id: str
    title: str
    type: str  # Person, Container, System, Component, etc.
    technology: Optional[str] = None
    description: Optional[str] = None
    # Идентификатор ближайшей объемлющей границы (boundary), если есть.
    # Восстанавливает иерархию L2 -> L3 для DocHub.
    parent: Optional[str] = None
    # Границы (System_Boundary / Container_Boundary) не являются
    # компонентами сами по себе, но задают группировку.
    is_boundary: bool = False
    # Страница схемы: в одном файле DrawIO их бывает много, и без этого
    # признака элементы разных диаграмм сливаются в общую кучу
    page: int = 0
    page_name: str = ''


@dataclass
class Relation:
    """Связь между компонентами."""
    from_id: str
    to_id: str
    label: Optional[str] = None
    direction: str = "-->"  # -->, <--, <-->
    page: int = 0


@dataclass
class ParseResult:
    """Результат парсинга схемы."""
    is_c4: bool
    error_message: Optional[str] = None
    components: List[Component] = None
    relations: List[Relation] = None
    # Названия страниц файла по порядку
    pages: List[str] = None

    def __post_init__(self):
        if self.components is None:
            self.components = []
        if self.relations is None:
            self.relations = []
        if self.pages is None:
            self.pages = []

    def for_page(self, page: int) -> 'ParseResult':
        """
        Оставить только одну страницу.

        Args:
            page: Номер страницы с нуля

        Returns:
            Результат разбора этой страницы
        """
        return ParseResult(
            is_c4=self.is_c4,
            error_message=self.error_message,
            components=[c for c in self.components if c.page == page],
            relations=[r for r in self.relations if r.page == page],
            pages=self.pages
        )


class BaseParser(ABC):
    """Базовый класс для парсеров."""

    def __init__(self, file_path: str):
        """
        Инициализация парсера.

        Args:
            file_path: Путь к файлу схемы
        """
        self.file_path = Path(file_path)

        if not self.file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

    @abstractmethod
    def parse(self) -> ParseResult:
        """
        Парсинг схемы.

        Returns:
            ParseResult с компонентами и связями или ошибкой
        """
        pass

    @abstractmethod
    def is_c4_notation(self) -> bool:
        """
        Проверка что схема в нотации C4.

        Returns:
            True если схема в C4, False иначе
        """
        pass

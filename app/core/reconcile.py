"""
Сверка схемы с исходным кодом сервиса.

Схема и код расходятся почти всегда: схему рисовали раньше, код с тех пор
менялся. Задача — показать все расхождения со ссылкой на первоисточник,
а не решать за человека. Что включать в архитектурную схему, а что нет,
зависит от конкретного случая, и решение остаётся за пользователем.

Ничего не отбрасывается и не добавляется молча.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

from app.core.parsers.base_parser import Component
from app.utils.logger import get_logger

# Объявления типов в популярных языках
DECLARATION_PATTERNS = {
    '.cs': re.compile(
        r'^\s*(?:public|internal|private|protected|sealed|abstract|static|partial|\s)*'
        r'(?:class|interface|record|struct|enum)\s+(\w+)', re.M
    ),
    '.java': re.compile(
        r'^\s*(?:public|private|protected|abstract|final|static|\s)*'
        r'(?:class|interface|record|enum)\s+(\w+)', re.M
    ),
    '.py': re.compile(r'^\s*class\s+(\w+)', re.M),
    '.ts': re.compile(r'^\s*(?:export\s+)?(?:abstract\s+)?(?:class|interface)\s+(\w+)', re.M),
    '.go': re.compile(r'^\s*type\s+(\w+)\s+(?:struct|interface)', re.M),
}

# Каталоги, которые не относятся к коду сервиса
SKIP_DIRS = {
    'bin', 'obj', 'node_modules', '.git', '.vs', '.idea', 'dist', 'build',
    'venv', '.venv', '__pycache__', 'packages', 'TestResults'
}

# Файлы тестов обычно не попадают в архитектурную схему
TEST_HINTS = ('test', 'spec', 'mock', 'fixture')


@dataclass
class CodeSymbol:
    """Объявление, найденное в коде."""
    name: str
    file: str
    line: int
    is_test: bool = False


@dataclass
class Discrepancy:
    """Расхождение между схемой и кодом."""
    kind: str
    name: str
    detail: str
    schema_element: Optional[str] = None
    code_locations: List[str] = field(default_factory=list)


@dataclass
class ReconcileReport:
    """Итог сверки."""
    scanned_files: int = 0
    symbols_found: int = 0
    matched: List[str] = field(default_factory=list)
    discrepancies: List[Discrepancy] = field(default_factory=list)

    @property
    def by_kind(self) -> Dict[str, int]:
        """Сводка по видам расхождений."""
        counts: Dict[str, int] = {}
        for item in self.discrepancies:
            counts[item.kind] = counts.get(item.kind, 0) + 1
        return counts


class Reconciler:
    """Сопоставляет элементы схемы с объявлениями в коде."""

    def __init__(self, project_root: Path):
        """
        Args:
            project_root: Корень репозитория сервиса
        """
        self.project_root = Path(project_root).resolve()
        self.logger = get_logger()

    def scan_code(self) -> Dict[str, List[CodeSymbol]]:
        """
        Собрать объявления типов из исходников.

        Returns:
            Словарь {имя: [где объявлено]}
        """
        symbols: Dict[str, List[CodeSymbol]] = {}
        self._scanned = 0

        for path in self.project_root.rglob('*'):
            if not path.is_file() or path.suffix not in DECLARATION_PATTERNS:
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue

            try:
                text = path.read_text(encoding='utf-8', errors='replace')
            except OSError:
                continue

            self._scanned += 1
            relative = path.relative_to(self.project_root).as_posix()
            is_test = any(hint in relative.lower() for hint in TEST_HINTS)

            for match in DECLARATION_PATTERNS[path.suffix].finditer(text):
                name = match.group(1)
                line = text.count('\n', 0, match.start()) + 1
                symbols.setdefault(name, []).append(
                    CodeSymbol(name=name, file=relative, line=line, is_test=is_test)
                )

        return symbols

    def reconcile(self, components: List[Component]) -> ReconcileReport:
        """
        Сверить компоненты схемы с кодом.

        Args:
            components: Компоненты, распознанные на схеме

        Returns:
            Отчёт с расхождениями
        """
        symbols = self.scan_code()
        report = ReconcileReport(
            scanned_files=self._scanned,
            symbols_found=len(symbols)
        )

        # Индекс без учёта регистра: на схемах регистр часто гуляет
        lowered = {name.lower(): name for name in symbols}
        on_schema: Set[str] = set()

        for component in components:
            if component.is_boundary:
                continue

            title = (component.title or '').strip()
            if not title:
                continue

            candidate = self._candidate_name(title)
            on_schema.add(candidate.lower())

            if candidate in symbols:
                report.matched.append(candidate)
                continue

            exact = lowered.get(candidate.lower())
            if exact:
                report.discrepancies.append(Discrepancy(
                    kind='case_mismatch',
                    name=title,
                    detail=f"на схеме «{candidate}», в коде «{exact}»",
                    schema_element=component.id,
                    code_locations=self._locations(symbols[exact])
                ))
                report.matched.append(exact)
                continue

            near = self._find_near(candidate, symbols)
            if near:
                report.discrepancies.append(Discrepancy(
                    kind='name_mismatch',
                    name=title,
                    detail=f"на схеме «{candidate}», похоже на «{near}» в коде",
                    schema_element=component.id,
                    code_locations=self._locations(symbols[near])
                ))
                continue

            report.discrepancies.append(Discrepancy(
                kind='on_schema_not_in_code',
                name=title,
                detail="есть на схеме, в коде не найдено: схема могла устареть, "
                       "либо это логический элемент, а не класс",
                schema_element=component.id
            ))

        # Обратная сторона: что есть в коде, но не попало на схему
        for name, locations in symbols.items():
            if name.lower() in on_schema:
                continue
            if all(item.is_test for item in locations):
                continue
            if not self._looks_significant(name):
                continue

            report.discrepancies.append(Discrepancy(
                kind='in_code_not_on_schema',
                name=name,
                detail="есть в коде, на схеме не отражено",
                code_locations=self._locations(locations)
            ))

        self.logger.info(
            f"Сверка: файлов {report.scanned_files}, объявлений "
            f"{report.symbols_found}, расхождений {len(report.discrepancies)}"
        )
        return report

    @staticmethod
    def _candidate_name(title: str) -> str:
        """
        Выделить имя класса из подписи на схеме.

        Подписи бывают вида "OrderHandlerService — отправка заказов" или
        "Queue: order-placed".
        """
        head = re.split(r'[—:(\[]', title)[0].strip()
        # Имя класса — одно слово без пробелов
        parts = head.split()
        return parts[-1] if len(parts) == 1 else head.replace(' ', '')

    @staticmethod
    def _locations(items: List[CodeSymbol], limit: int = 3) -> List[str]:
        """Сформировать ссылки вида файл:строка."""
        return [f"{item.file}:{item.line}" for item in items[:limit]]

    @staticmethod
    def _find_near(candidate: str, symbols: Dict[str, List[CodeSymbol]]) -> Optional[str]:
        """
        Найти близкое имя: опечатки и разное написание встречаются часто.

        Args:
            candidate: Имя со схемы
            symbols: Объявления из кода

        Returns:
            Имя из кода или None
        """
        from difflib import get_close_matches

        matches = get_close_matches(candidate, list(symbols), n=1, cutoff=0.85)
        return matches[0] if matches else None

    @staticmethod
    def _looks_significant(name: str) -> bool:
        """
        Стоит ли упоминать объявление в отчёте.

        Классы данных и вспомогательные типы засоряют отчёт, но решение
        всё равно за пользователем — просто не показываем совсем очевидное.
        """
        if len(name) < 4:
            return False
        noise = ('Dto', 'DTO', 'Request', 'Response', 'Options', 'Settings',
                 'Exception', 'Attribute', 'Enum', 'Constants', 'Extensions')
        return not name.endswith(noise)

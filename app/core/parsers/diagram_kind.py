"""
Определение вида диаграммы.

DocHub-схему можно построить только из диаграммы физического уровня в
нотации C4: из неё видно, какие компоненты существуют и как они связаны.
Из логических диаграмм — sequence, activity, use case, ER — состав
компонентов не выводится, и подсовывать их бессмысленно.
"""

import re
from enum import Enum
from pathlib import Path
from typing import Optional, Tuple


class DiagramKind(str, Enum):
    """Вид диаграммы."""
    C4 = "c4"
    SEQUENCE = "sequence"
    ACTIVITY = "activity"
    USE_CASE = "use_case"
    CLASS = "class"
    ER = "er"
    STATE = "state"
    MIND_MAP = "mindmap"
    UNKNOWN = "unknown"


# Что видно в исходнике PlantUML для каждого вида
PLANTUML_MARKERS = (
    (DiagramKind.SEQUENCE, (
        r'^\s*participant\s', r'^\s*actor\s+\w+\s*$', r'^\s*autonumber\b',
        r'^\s*activate\s', r'^\s*deactivate\s', r'^\s*alt\s', r'^\s*loop\s'
    )),
    (DiagramKind.ACTIVITY, (
        r'^\s*start\s*$', r'^\s*stop\s*$', r'^\s*:.*;\s*$',
        r'^\s*if\s*\(.*\)\s*then', r'^\s*repeat\s*$', r'^\s*fork\b'
    )),
    (DiagramKind.USE_CASE, (r'^\s*usecase\s', r'\(\w[^)]*\)\s+as\s+\w+')),
    (DiagramKind.STATE, (r'^\s*state\s+\w', r'\[\*\]\s*-->')),
    (DiagramKind.ER, (r'^\s*entity\s+\w', r'\|\|--o\{', r'\}o--\|\|')),
    (DiagramKind.MIND_MAP, (r'@startmindmap', r'@startwbs')),
    (DiagramKind.CLASS, (r'^\s*class\s+\w', r'^\s*interface\s+\w')),
)

# Макросы C4-PlantUML
C4_MARKERS = (
    r'!include.*C4[_-]', r'\bContainer(_Boundary|Db|Queue)?\s*\(',
    r'\bComponent(_Ext|Db)?\s*\(', r'\bSystem(_Boundary|_Ext)?\s*\(',
    r'\bPerson(_Ext)?\s*\(', r'\bRel(_[UDLR])?\s*\(', r'\$Entity\s*\('
)

# Подписи-подсказки в DrawIO
DRAWIO_LOGICAL_HINTS = (
    (DiagramKind.SEQUENCE, ('shape=umlLifeline', 'shape=umlFrame')),
    (DiagramKind.ACTIVITY, ('shape=startState', 'shape=endState', 'shape=umlActivity')),
    (DiagramKind.USE_CASE, ('shape=umlActor', 'ellipse;whiteSpace=wrap;html=1;'
                            'verticalAlign=middle;usecase')),
    (DiagramKind.ER, ('shape=table', 'childLayout=tableLayout')),
)

HUMAN_NAMES = {
    DiagramKind.SEQUENCE: "диаграмма последовательности (sequence)",
    DiagramKind.ACTIVITY: "диаграмма активности (activity)",
    DiagramKind.USE_CASE: "диаграмма вариантов использования (use case)",
    DiagramKind.CLASS: "диаграмма классов",
    DiagramKind.ER: "ER-диаграмма",
    DiagramKind.STATE: "диаграмма состояний",
    DiagramKind.MIND_MAP: "интеллект-карта",
    DiagramKind.UNKNOWN: "неизвестный вид",
}


def detect_plantuml_kind(source: str) -> DiagramKind:
    """
    Определить вид диаграммы PlantUML.

    Args:
        source: Исходный код диаграммы

    Returns:
        Вид диаграммы
    """
    if any(re.search(marker, source, re.M) for marker in C4_MARKERS):
        return DiagramKind.C4

    for kind, markers in PLANTUML_MARKERS:
        if any(re.search(marker, source, re.M) for marker in markers):
            return kind

    return DiagramKind.UNKNOWN


def detect_drawio_kind(source: str) -> DiagramKind:
    """
    Определить вид диаграммы DrawIO.

    Args:
        source: XML схемы

    Returns:
        Вид диаграммы
    """
    if 'c4Name=' in source or 'c4Type=' in source:
        return DiagramKind.C4

    for kind, hints in DRAWIO_LOGICAL_HINTS:
        if any(hint in source for hint in hints):
            return kind

    return DiagramKind.UNKNOWN


def explain_unsupported(kind: DiagramKind, file_name: str) -> str:
    """
    Составить объяснение, почему схема не подходит.

    Args:
        kind: Определённый вид диаграммы
        file_name: Имя файла

    Returns:
        Текст с причиной и что делать дальше
    """
    if kind == DiagramKind.UNKNOWN:
        return (
            f"В файле {file_name} не найдено признаков нотации C4.\n\n"
            "Для DrawIO нужны атрибуты c4Name / c4Type — они появляются, если "
            "рисовать из набора фигур C4. Для PlantUML — макросы C4 "
            "(Container, Component, System, Rel) или include C4-PlantUML.\n\n"
            "Схему DocHub можно построить только из диаграммы физического "
            "уровня: из неё видно состав компонентов и связи между ними."
        )

    return (
        f"Файл {file_name} — это {HUMAN_NAMES.get(kind, kind.value)}, "
        "то есть диаграмма логического уровня.\n\n"
        "Из неё нельзя построить схему DocHub: она показывает порядок "
        "взаимодействий или поведение, но не состав компонентов системы и не "
        "их принадлежность сервисам.\n\n"
        "Нужна диаграмма физического уровня в нотации C4 — контейнеров или "
        "компонентов."
    )


def detect_file_kind(path: Path) -> Tuple[DiagramKind, Optional[str]]:
    """
    Определить вид диаграммы по файлу.

    Args:
        path: Путь к схеме

    Returns:
        Кортеж (вид, текст ошибки или None если это C4)
    """
    try:
        source = path.read_text(encoding='utf-8', errors='replace')
    except OSError as e:
        return DiagramKind.UNKNOWN, f"Не удалось прочитать {path.name}: {e}"

    suffix = path.suffix.lower()
    if suffix in ('.puml', '.plantuml', '.pu'):
        kind = detect_plantuml_kind(source)
    else:
        kind = detect_drawio_kind(source)

    if kind == DiagramKind.C4:
        return kind, None

    return kind, explain_unsupported(kind, path.name)

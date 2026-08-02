"""
Восстановление вложенности элементов DrawIO по геометрии.

В схемах, нарисованных по шаблонам (C4, DDD), элементы обычно лежат плоско:
атрибут parent у всех указывает на корень, а принадлежность рамке выражена
только тем, что фигура нарисована внутри неё. Поэтому иерархию приходится
восстанавливать по координатам.
"""

import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple

Rect = Tuple[float, float, float, float]


def get_geometry(element: ET.Element) -> Optional[Rect]:
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


def contains(outer: Rect, inner: Rect, tolerance: float = 0.0) -> bool:
    """
    Проверить, что outer полностью содержит inner.

    Args:
        outer: Внешний прямоугольник
        inner: Внутренний прямоугольник
        tolerance: Допуск в пикселях — рисуя от руки, элементы часто
                   выступают за рамку на несколько пикселей

    Returns:
        True если inner лежит внутри outer
    """
    ox, oy, ow, oh = outer
    ix, iy, iw, ih = inner

    return (
        ox - tolerance <= ix
        and oy - tolerance <= iy
        and ix + iw <= ox + ow + tolerance
        and iy + ih <= oy + oh + tolerance
    )


def area(rect: Rect) -> float:
    """Площадь прямоугольника."""
    return rect[2] * rect[3]


def find_innermost(
    target: str,
    geometries: Dict[str, Rect],
    candidates: List[str],
    tolerance: float = 0.0
) -> Optional[str]:
    """
    Найти ближайший объемлющий элемент из списка кандидатов.

    Ближайшим считается наименьший по площади из тех, что содержат цель:
    так элемент внутри вложенных рамок относится к самой внутренней.

    Args:
        target: Идентификатор искомого элемента
        geometries: Геометрия по идентификаторам
        candidates: Идентификаторы возможных родителей
        tolerance: Допуск в пикселях

    Returns:
        Идентификатор родителя или None
    """
    inner = geometries.get(target)
    if not inner:
        return None

    enclosing = [
        candidate for candidate in candidates
        if candidate != target
        and candidate in geometries
        and contains(geometries[candidate], inner, tolerance)
    ]

    if not enclosing:
        return None

    return min(enclosing, key=lambda c: area(geometries[c]))

"""
Экспорт схем DrawIO в картинку.

Отрисовать DrawIO можно только движком самого DrawIO: у формата нет
описания, из которого картинку собрал бы кто-то ещё. Поэтому есть два пути,
и они дополняют друг друга.

  - Встроенный просмотрщик в превью (vendor/viewer-static.min.js) — работает
    всегда и без внешних программ, но живёт в браузере.
  - Draw.io Desktop — умеет выгружать файл в SVG или PNG, но должен быть
    установлен.

Пересобирать схему из распознанных компонентов сознательно не делаем:
нужен оригинал, а не его реконструкция.
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

from app.utils.logger import get_logger

# Где Draw.io Desktop обычно ставится
CANDIDATE_PATHS = (
    r'C:\Program Files\draw.io\draw.io.exe',
    r'C:\Program Files (x86)\draw.io\draw.io.exe',
    '/Applications/draw.io.app/Contents/MacOS/draw.io',
    '/usr/bin/drawio',
    '/usr/local/bin/drawio',
)

SUPPORTED_FORMATS = ('svg', 'png', 'pdf')


class DrawIOExportError(Exception):
    """Не удалось выгрузить схему."""


def find_drawio() -> Optional[Path]:
    """
    Найти Draw.io Desktop.

    Returns:
        Путь к программе или None
    """
    override = os.environ.get('DRAWIO_PATH')
    if override and Path(override).exists():
        return Path(override)

    for name in ('drawio', 'draw.io'):
        found = shutil.which(name)
        if found:
            return Path(found)

    for candidate in CANDIDATE_PATHS:
        path = Path(candidate)
        if path.exists():
            return path

    return None


def export(
    schema_path: Path,
    output_path: Path,
    image_format: str = 'svg',
    page: Optional[int] = None,
    timeout: int = 180
) -> Path:
    """
    Выгрузить схему DrawIO в картинку.

    Args:
        schema_path: Файл .drawio
        output_path: Куда сохранить
        image_format: svg, png или pdf
        page: Номер страницы, если в файле их несколько
        timeout: Ограничение времени в секундах

    Returns:
        Путь к созданному файлу

    Raises:
        DrawIOExportError: если программа не найдена или экспорт не удался
    """
    if image_format not in SUPPORTED_FORMATS:
        raise DrawIOExportError(
            f"Формат {image_format!r} не поддерживается. "
            f"Доступны: {', '.join(SUPPORTED_FORMATS)}"
        )

    executable = find_drawio()
    if executable is None:
        raise DrawIOExportError(
            "Draw.io Desktop не найден, выгрузить схему в картинку нечем.\n\n"
            "Схему можно посмотреть в превью (dochub_preview): там она "
            "рисуется встроенным движком DrawIO и внешних программ не требует.\n\n"
            "Если выгрузка в файл всё же нужна, установите Draw.io Desktop "
            "или укажите путь к нему в переменной окружения DRAWIO_PATH."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)

    command: List[str] = [
        str(executable), '--export',
        '--format', image_format,
        '--output', str(output_path),
    ]
    if page is not None:
        command += ['--page-index', str(page)]
    command.append(str(schema_path))

    logger = get_logger()

    try:
        result = subprocess.run(
            command, capture_output=True,
            # Дочерний процесс не должен наследовать stdin MCP-сервера:
            # это труба протокола, и ожидание её закрытия вешает вызов
            stdin=subprocess.DEVNULL, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        raise DrawIOExportError(
            f"Draw.io не ответил за {timeout} секунд. "
            "Большие схемы выгружаются долго — попробуйте увеличить timeout"
        )

    if not output_path.exists():
        stderr = result.stderr.decode('utf-8', errors='replace').strip()
        raise DrawIOExportError(
            f"Draw.io не создал файл: {stderr or 'без описания ошибки'}"
        )

    logger.info(
        f"Схема выгружена: {output_path.name}, "
        f"{output_path.stat().st_size} байт"
    )
    return output_path

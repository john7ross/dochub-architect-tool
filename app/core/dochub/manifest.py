"""
Сборка манифеста архитектурного репозитория.

DocHub собирает единый манифест, рекурсивно разворачивая секции imports.
Без этого ссылки на компоненты из соседних файлов (Catalog.Api, mssql и прочее)
не разрешаются и попадают на диаграмму как голые идентификаторы.
"""

from pathlib import Path
from typing import Dict, List, Optional, Set

import yaml

from app.utils.logger import get_logger

# Секции, которые склеиваются между файлами репозитория
MERGED_SECTIONS = ('aspects', 'components', 'contexts', 'entities', 'docs', 'forms')


class ManifestLoader:
    """Собирает манифест репозитория, разворачивая imports."""

    def __init__(self):
        self.logger = get_logger()
        self._visited: Set[Path] = set()
        self.errors: List[str] = []

    def load(self, root_path: Path) -> Dict:
        """
        Загрузить манифест начиная с корневого файла.

        Args:
            root_path: Путь к dochub.yaml (обычно architecture/dochub.yaml)

        Returns:
            Слитый манифест со всеми aspects/components/contexts
        """
        self._visited = set()
        self.errors = []

        manifest: Dict = {}
        self._load_into(manifest, Path(root_path))

        self.logger.info(
            f"Манифест собран: {len(manifest.get('components', {}))} компонентов, "
            f"{len(manifest.get('contexts', {}))} контекстов, "
            f"{len(manifest.get('aspects', {}))} аспектов"
        )
        if self.errors:
            self.logger.warning(f"Файлов с ошибками: {len(self.errors)}")

        return manifest

    def _load_into(self, manifest: Dict, path: Path) -> None:
        """
        Загрузить один файл и его импорты в манифест.

        Args:
            manifest: Накопитель (изменяется на месте)
            path: Путь к YAML-файлу
        """
        try:
            resolved = path.resolve()
        except OSError:
            return

        # Один и тот же файл нередко импортируется из нескольких мест
        if resolved in self._visited:
            return
        self._visited.add(resolved)

        if not resolved.exists():
            self.errors.append(f"не найден: {path}")
            return

        try:
            data = yaml.safe_load(resolved.read_text(encoding='utf-8')) or {}
        except (yaml.YAMLError, UnicodeDecodeError) as e:
            self.errors.append(f"{path.name}: {e}")
            return

        if not isinstance(data, dict):
            return

        # Импорты разворачиваем первыми — свои определения файла важнее
        for relative in data.get('imports') or []:
            if isinstance(relative, str):
                self._load_into(manifest, resolved.parent / relative)

        for section in MERGED_SECTIONS:
            if section in data and isinstance(data[section], dict):
                merge_deep(manifest.setdefault(section, {}), data[section])

    def find_context_file(self, repo_root: Path, context_id: str) -> Optional[Path]:
        """
        Найти файл, в котором определён контекст.

        Args:
            repo_root: Корень репозитория
            context_id: Идентификатор контекста

        Returns:
            Путь к файлу или None
        """
        for path in repo_root.rglob('*.yaml'):
            try:
                data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
            except Exception:
                continue
            if isinstance(data, dict) and context_id in (data.get('contexts') or {}):
                return path
        return None


def merge_deep(target: Dict, source: Dict) -> Dict:
    """
    Слить source в target вглубь.

    Args:
        target: Приёмник (изменяется на месте)
        source: Источник

    Returns:
        target
    """
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            merge_deep(target[key], value)
        else:
            target[key] = value
    return target

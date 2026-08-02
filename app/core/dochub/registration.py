"""
Проверка регистрации схемы в дереве репозитория.

Мало положить YAML в каталог поддомена: DocHub его не увидит, пока файл не
подключён в architecture/dochub.yaml, а корневые компоненты и родительские
аспекты не объявлены в общих файлах. Именно на этом валидатор ругается чаще
всего, а ошибка выглядит непонятно — «аспект верхнего уровня не определён».
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

import yaml

from app.core.dochub.manifest import ManifestLoader
from app.utils.logger import get_logger

# Где по соглашению репозитория объявляются недостающие сущности
ROOT_COMPONENT_FILE = 'architecture/common/general/root.yaml'
EXTERNAL_FILE = 'architecture/common/external/root.yaml'
PLATFORMS_CONTEXT = 'platforms'
EXTERNAL_PREFIX = 'externalServices'


@dataclass
class Missing:
    """Недостающее объявление."""
    id: str
    where: str
    hint: str


@dataclass
class RegistrationReport:
    """Итог проверки регистрации."""
    imported: bool
    import_path: Optional[str] = None
    manifest_file: Optional[str] = None
    missing_roots: List[Missing] = field(default_factory=list)
    missing_parent_aspects: List[Missing] = field(default_factory=list)
    missing_parent_components: List[Missing] = field(default_factory=list)
    missing_externals: List[Missing] = field(default_factory=list)
    orphan_components: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Всё ли на месте."""
        return (
            self.imported
            and not self.missing_roots
            and not self.missing_parent_aspects
            and not self.missing_parent_components
            and not self.missing_externals
            and not self.orphan_components
        )


class RegistrationChecker:
    """Проверяет, что схема подключена и её сущности объявлены."""

    def __init__(self, repo_root: Path):
        """
        Args:
            repo_root: Корень архитектурного репозитория
        """
        self.repo_root = Path(repo_root).resolve()
        self.logger = get_logger()

    def _manifest_file(self) -> Optional[Path]:
        """Найти корневой dochub.yaml репозитория."""
        candidate = self.repo_root / 'architecture' / 'dochub.yaml'
        if candidate.exists():
            return candidate

        candidate = self.repo_root / 'dochub.yaml'
        return candidate if candidate.exists() else None

    def check(self, yaml_path: Path) -> RegistrationReport:
        """
        Проверить регистрацию файла поддомена.

        Args:
            yaml_path: Путь к YAML поддомена

        Returns:
            Отчёт о недостающих объявлениях
        """
        yaml_path = Path(yaml_path).resolve()
        manifest_file = self._manifest_file()

        if manifest_file is None:
            return RegistrationReport(
                imported=False,
                manifest_file=None
            )

        manifest = ManifestLoader().load(manifest_file)
        own = yaml.safe_load(yaml_path.read_text(encoding='utf-8')) or {}

        report = RegistrationReport(
            imported=self._is_imported(manifest_file, yaml_path),
            import_path=self._import_path(manifest_file, yaml_path),
            manifest_file=str(manifest_file.relative_to(self.repo_root))
        )

        known_components: Set[str] = set(manifest.get('components') or {})
        known_aspects: Set[str] = set(manifest.get('aspects') or {})

        own_components = own.get('components') or {}
        own_aspects = own.get('aspects') or {}

        # Если файл ещё не подключён, его сущности в манифест не попали —
        # учитываем их отдельно, иначе отчёт будет полон ложных пропусков
        known_components |= set(own_components)
        known_aspects |= set(own_aspects)

        report.missing_roots = self._missing_roots(own_components, known_components)
        report.missing_parent_components = self._missing_parents(
            own_components, known_components, ROOT_COMPONENT_FILE,
            "объявить родительский компонент"
        )
        report.missing_parent_aspects = self._missing_parents(
            own_aspects, known_aspects, str(yaml_path.name),
            "объявить родительский аспект в секции aspects"
        )
        report.missing_externals = self._missing_externals(
            own, known_components
        )
        report.orphan_components = self._orphans(own)

        return report

    def _is_imported(self, manifest_file: Path, yaml_path: Path) -> bool:
        """
        Подключён ли файл через imports (в том числе транзитивно).

        Args:
            manifest_file: Корневой dochub.yaml
            yaml_path: Проверяемый файл

        Returns:
            True если файл достижим из корня
        """
        visited: Set[Path] = set()
        target = yaml_path.resolve()

        def walk(path: Path) -> bool:
            resolved = path.resolve()
            if resolved in visited or not resolved.exists():
                return False
            visited.add(resolved)

            if resolved == target:
                return True

            try:
                data = yaml.safe_load(resolved.read_text(encoding='utf-8')) or {}
            except (yaml.YAMLError, OSError):
                return False

            if not isinstance(data, dict):
                return False

            return any(
                walk(resolved.parent / relative)
                for relative in (data.get('imports') or [])
                if isinstance(relative, str)
            )

        return walk(manifest_file)

    def _import_path(self, manifest_file: Path, yaml_path: Path) -> Optional[str]:
        """
        Строка, которую нужно добавить в imports.

        Args:
            manifest_file: Корневой dochub.yaml
            yaml_path: Подключаемый файл

        Returns:
            Относительный путь в формате imports
        """
        try:
            relative = yaml_path.resolve().relative_to(manifest_file.parent.resolve())
        except ValueError:
            return None

        return relative.as_posix()

    @staticmethod
    def _missing_roots(
        own_components: Dict,
        known_components: Set[str]
    ) -> List[Missing]:
        """
        Корневые платформы, которых нет в репозитории.

        Для компонента "python.voiceinsight" нужен компонент "python",
        иначе валидатор скажет «компонент верхнего уровня не определён».
        """
        missing = []
        seen = set()

        for component_id in own_components:
            root = component_id.split('.')[0]
            if root in seen or root in known_components:
                continue
            seen.add(root)
            missing.append(Missing(
                id=root,
                where=ROOT_COMPONENT_FILE,
                hint=f"добавить компонент {root} и включить его "
                     f"в контекст {PLATFORMS_CONTEXT}"
            ))

        return missing

    @staticmethod
    def _missing_parents(
        own_items: Dict,
        known_items: Set[str],
        where: str,
        hint: str
    ) -> List[Missing]:
        """
        Промежуточные родители, которых никто не объявил.

        Для "a.b.c" нужны "a" и "a.b".
        """
        missing = []
        seen = set()

        for item_id in own_items:
            parts = item_id.split('.')
            for depth in range(1, len(parts)):
                parent = '.'.join(parts[:depth])
                if parent in seen or parent in known_items:
                    continue
                seen.add(parent)
                missing.append(Missing(id=parent, where=where, hint=hint))

        return missing

    @staticmethod
    def _missing_externals(own: Dict, known_components: Set[str]) -> List[Missing]:
        """
        Внешние сервисы, на которые ссылаются контексты, но которых нет.
        """
        missing = []
        seen = set()

        for context in (own.get('contexts') or {}).values():
            for component_id in (context or {}).get('components') or []:
                if not component_id.startswith(f'{EXTERNAL_PREFIX}.'):
                    continue
                if component_id in known_components or component_id in seen:
                    continue
                seen.add(component_id)
                missing.append(Missing(
                    id=component_id,
                    where=EXTERNAL_FILE,
                    hint=f"объявить внешний сервис и включить "
                         f"в контекст {EXTERNAL_PREFIX}"
                ))

        return missing

    @staticmethod
    def _orphans(own: Dict) -> List[str]:
        """
        Компоненты файла, не попавшие ни в один его контекст.

        Валидатор DocHub считает это ошибкой «компоненты вне контекста».
        """
        in_contexts: Set[str] = set()

        for context in (own.get('contexts') or {}).values():
            in_contexts.update((context or {}).get('components') or [])

        return sorted(
            component_id
            for component_id in (own.get('components') or {})
            if component_id not in in_contexts
        )

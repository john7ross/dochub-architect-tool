"""
Внесение схемы в общие и чужие файлы репозитория.

Схема сервиса почти никогда не помещается в один файл поддомена. Реальный
перенос затрагивает ещё и общие файлы (корневые платформы, внешние сервисы),
подключение в дереве, а при интеграции с чужим API — файл владельца этого API.

Правки выполняются через ruamel.yaml, чтобы сохранить комментарии, порядок
ключей и оформление: файлы репозитория читают люди.

Все операции сначала собираются в план и показываются, и только потом
применяются — правка чужого поддомена требует согласования с владельцем.
"""

import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from ruamel.yaml import YAML

from app.utils.logger import get_logger

ROOT_COMPONENT_FILE = 'architecture/common/general/root.yaml'
EXTERNAL_FILE = 'architecture/common/external/root.yaml'
MANIFEST_FILE = 'architecture/dochub.yaml'


class RegistrarError(Exception):
    """Ошибка внесения правок."""


@dataclass
class Change:
    """Одна правка в файле."""
    file: str
    action: str
    target: str
    detail: str = ''
    foreign: bool = False


@dataclass
class Plan:
    """План правок."""
    changes: List[Change] = field(default_factory=list)

    @property
    def touches_foreign(self) -> bool:
        """Затрагивает ли план чужие поддомены."""
        return any(change.foreign for change in self.changes)

    @property
    def files(self) -> List[str]:
        """Список затрагиваемых файлов."""
        return sorted({change.file for change in self.changes})


class OwnerIndex:
    """
    Указатель «какой компонент в каком файле определён».

    Строится один раз обходом репозитория: искать владельца для каждого
    компонента отдельным сканированием слишком дорого — на схеме их десятки,
    а файлов в репозитории сотни.
    """

    def __init__(self, repo_root: Path):
        """
        Args:
            repo_root: Корень репозитория
        """
        self.repo_root = Path(repo_root).resolve()
        self._components: Dict[str, Path] = {}
        self._contexts: Dict[Path, List[str]] = {}
        self._build()

    def _build(self) -> None:
        """Обойти репозиторий и запомнить, где что определено."""
        # Быстрый разбор: оформление здесь не важно, важны только ключи
        import yaml as fast_yaml

        root = self.repo_root / 'architecture'
        if not root.is_dir():
            root = self.repo_root

        for path in root.rglob('*.yaml'):
            try:
                data = fast_yaml.safe_load(path.read_text(encoding='utf-8')) or {}
            except Exception:
                continue

            if not isinstance(data, dict):
                continue

            for key in (data.get('components') or {}):
                self._components.setdefault(key, path)

            listed = list(data.get('contexts') or {})
            if listed:
                self._contexts[path] = listed

    def locate(self, component_id: str) -> Dict:
        """
        Найти файл, которому принадлежит компонент.

        Ищется файл, где определён сам компонент или его ближайший родитель
        по префиксу. Для "dotnet.eventBus.newQueue" родителем будет
        "dotnet.eventBus", и писать нужно в файл, где он объявлен.

        Args:
            component_id: Полный идентификатор компонента

        Returns:
            Словарь с полями:
                file           — путь к файлу владельца относительно корня
                matched_prefix — по какому префиксу нашли
                exists         — компонент уже определён
                l2_component   — ближайший родительский компонент того же файла
                contexts       — контексты этого файла
        """
        parts = component_id.split('.')

        # Совпадение по одному сегменту — это корень платформы (dotnet, mssql).
        # Платформа объявлена в общем файле и ничем не владеет: владение
        # начинается с уровня сервиса или схемы, то есть со второго сегмента
        for depth in range(len(parts), 1, -1):
            prefix = '.'.join(parts[:depth])
            path = self._components.get(prefix)
            if path is None:
                continue

            l2 = None
            for inner in range(depth - 1, 0, -1):
                candidate = '.'.join(parts[:inner])
                if self._components.get(candidate) == path:
                    l2 = candidate
                    break

            return {
                'file': path.relative_to(self.repo_root).as_posix(),
                'matched_prefix': prefix,
                'exists': prefix == component_id,
                'l2_component': l2 or (prefix if prefix != component_id else None),
                'contexts': self._contexts.get(path, [])
            }

        return {
            'file': None,
            'matched_prefix': None,
            'exists': False,
            'l2_component': None,
            'contexts': []
        }


def locate_owner(repo_root: Path, component_id: str) -> Dict:
    """
    Найти владельца одного компонента.

    Для нескольких компонентов используйте OwnerIndex: он строит указатель
    один раз, а не на каждый запрос.

    Args:
        repo_root: Корень репозитория
        component_id: Полный идентификатор компонента

    Returns:
        Тот же словарь, что возвращает OwnerIndex.locate
    """
    return OwnerIndex(repo_root).locate(component_id)


def _yaml() -> YAML:
    """Настроенный round-trip парсер."""
    parser = YAML()
    parser.preserve_quotes = True
    parser.width = 4096
    parser.indent(mapping=2, sequence=4, offset=2)
    return parser


class Registrar:
    """Вносит схему в общие и чужие файлы репозитория."""

    def __init__(self, repo_root: Path):
        """
        Args:
            repo_root: Корень архитектурного репозитория
        """
        self.repo_root = Path(repo_root).resolve()
        self.logger = get_logger()
        self.yaml = _yaml()

    def _load(self, relative: str):
        """
        Прочитать файл репозитория с сохранением оформления.

        Args:
            relative: Путь относительно корня

        Returns:
            Дерево документа
        """
        path = self.repo_root / relative
        if not path.exists():
            raise RegistrarError(f"Файл не найден: {relative}")

        with path.open(encoding='utf-8') as handle:
            return self.yaml.load(handle) or {}

    def _save(self, relative: str, data) -> None:
        """
        Записать файл репозитория.

        Args:
            relative: Путь относительно корня
            data: Дерево документа
        """
        path = self.repo_root / relative
        buffer = io.StringIO()
        self.yaml.dump(data, buffer)
        path.write_text(buffer.getvalue(), encoding='utf-8')

    # --- Планирование -----------------------------------------------------

    def plan_roots(self, roots: Dict[str, Dict]) -> Plan:
        """
        Запланировать добавление корневых платформ.

        Args:
            roots: {id: {title, entity}} — например {"python": {...}}

        Returns:
            План
        """
        data = self._load(ROOT_COMPONENT_FILE)
        existing = data.get('components') or {}

        return Plan([
            Change(
                file=ROOT_COMPONENT_FILE,
                action='add_component',
                target=root_id,
                detail=f"{spec.get('title', root_id)} "
                       f"({spec.get('entity', 'component')})"
            )
            for root_id, spec in roots.items()
            if root_id not in existing
        ])

    def plan_externals(self, services: Dict[str, Dict]) -> Plan:
        """
        Запланировать добавление внешних сервисов.

        Args:
            services: {id: {title, aspects}} — id вида externalServices.name

        Returns:
            План
        """
        data = self._load(EXTERNAL_FILE)
        existing = data.get('components') or {}

        return Plan([
            Change(
                file=EXTERNAL_FILE,
                action='add_component',
                target=service_id,
                detail=spec.get('title', service_id)
            )
            for service_id, spec in services.items()
            if service_id not in existing
        ])

    def plan_import(self, yaml_path: Path) -> Plan:
        """
        Запланировать подключение файла поддомена.

        Args:
            yaml_path: Путь к файлу поддомена

        Returns:
            План
        """
        manifest_path = self.repo_root / MANIFEST_FILE
        data = self._load(MANIFEST_FILE)

        try:
            relative = Path(yaml_path).resolve().relative_to(
                manifest_path.parent.resolve()
            ).as_posix()
        except ValueError:
            raise RegistrarError(
                f"Файл вне каталога architecture: {yaml_path}"
            )

        if relative in (data.get('imports') or []):
            return Plan()

        return Plan([Change(
            file=MANIFEST_FILE,
            action='add_import',
            target=relative
        )])

    def plan_foreign_component(
        self,
        owner_file: str,
        component_id: str,
        title: str,
        entity: str = 'component',
        aspect_id: Optional[str] = None,
        aspect_title: Optional[str] = None,
        aspect_location: Optional[str] = None,
        owner_component: Optional[str] = None,
        owner_context: Optional[str] = None,
        summary: Optional[str] = None
    ) -> Plan:
        """
        Запланировать компонент, принадлежащий чужому поддомену.

        Общий случай правила: компонент описывается в файле того поддомена,
        которому принадлежит по смыслу, даже если заведён под наш сервис.
        Очередь в шине событий, хранимая процедура в чужой схеме БД, метод
        чужого API, дашборд чужой команды — всё это живёт в файле владельца,
        а в своём поддомене мы только ссылаемся на полный идентификатор.

        Args:
            owner_file: Файл владельца относительно корня репозитория
            component_id: Полный идентификатор компонента
            title: Название компонента
            entity: Тип: component, database, queue, actor, folder
            aspect_id: Аспект в пространстве владельца, если нужен
            aspect_title: Название аспекта
            aspect_location: Путь аспекта в дереве
            owner_component: L2-компонент владельца для связи с аспектом
            owner_context: Каталожный контекст владельца
            summary: Уточнение, например "GET — получение документа"

        Returns:
            План
        """
        data = self._load(owner_file)
        changes: List[Change] = []

        if aspect_id and aspect_id not in (data.get('aspects') or {}):
            changes.append(Change(
                file=owner_file, action='add_aspect', target=aspect_id,
                detail=aspect_title or aspect_id, foreign=True
            ))

        if component_id not in (data.get('components') or {}):
            detail = f"{title} [{entity}]"
            if summary:
                detail += f" | {summary}"
            changes.append(Change(
                file=owner_file, action='add_component', target=component_id,
                detail=detail, foreign=True
            ))

        if owner_component and aspect_id:
            owner = (data.get('components') or {}).get(owner_component)
            if owner is None:
                raise RegistrarError(
                    f"В {owner_file} нет компонента {owner_component}"
                )
            if aspect_id not in (owner.get('aspects') or []):
                changes.append(Change(
                    file=owner_file, action='link_aspect_to_component',
                    target=owner_component, detail=aspect_id, foreign=True
                ))

        if owner_context:
            context = (data.get('contexts') or {}).get(owner_context)
            if context is None:
                raise RegistrarError(
                    f"В {owner_file} нет контекста {owner_context}"
                )
            if component_id not in (context.get('components') or []):
                changes.append(Change(
                    file=owner_file, action='add_to_context',
                    target=owner_context, detail=component_id, foreign=True
                ))

        return Plan(changes)

    def plan_foreign_endpoint(
        self,
        owner_file: str,
        aspect_id: str,
        aspect_title: str,
        aspect_location: str,
        component_id: str,
        component_title: str,
        summary: str,
        owner_component: Optional[str] = None,
        owner_context: Optional[str] = None
    ) -> Plan:
        """
        Запланировать добавление метода чужого API в файл его владельца.

        Повторяет то, что делается вручную: аспект в пространстве имён
        владельца, ссылка на него в L2-компоненте и в каталожном контексте,
        плюс сам L3-компонент метода.

        Args:
            owner_file: Путь к файлу владельца относительно корня
            aspect_id: Идентификатор аспекта в пространстве владельца
            aspect_title: Название аспекта
            aspect_location: Путь аспекта в дереве
            component_id: Идентификатор L3-компонента
            component_title: Путь эндпоинта, он же title
            summary: Строка вида "GET — получение документа"
            owner_component: L2-компонент владельца, куда добавить аспект
            owner_context: Каталожный контекст владельца

        Returns:
            План
        """
        data = self._load(owner_file)
        changes: List[Change] = []

        if aspect_id not in (data.get('aspects') or {}):
            changes.append(Change(
                file=owner_file, action='add_aspect', target=aspect_id,
                detail=aspect_title, foreign=True
            ))

        if component_id not in (data.get('components') or {}):
            changes.append(Change(
                file=owner_file, action='add_component', target=component_id,
                detail=f"{component_title} | {summary}", foreign=True
            ))

        if owner_component:
            owner = (data.get('components') or {}).get(owner_component)
            if owner is None:
                raise RegistrarError(
                    f"В {owner_file} нет компонента {owner_component}"
                )
            if aspect_id not in (owner.get('aspects') or []):
                changes.append(Change(
                    file=owner_file, action='link_aspect_to_component',
                    target=owner_component, detail=aspect_id, foreign=True
                ))

        if owner_context:
            context = (data.get('contexts') or {}).get(owner_context)
            if context is None:
                raise RegistrarError(
                    f"В {owner_file} нет контекста {owner_context}"
                )
            if component_id not in (context.get('components') or []):
                changes.append(Change(
                    file=owner_file, action='add_to_context',
                    target=owner_context, detail=component_id, foreign=True
                ))

        return Plan(changes)

    # --- Применение -------------------------------------------------------

    def apply(self, plan: Plan, payloads: Dict[str, Dict]) -> List[str]:
        """
        Применить план.

        Args:
            plan: План правок
            payloads: Данные для вставки, ключ — "<файл>|<target>"

        Returns:
            Список изменённых файлов
        """
        by_file: Dict[str, List[Change]] = {}
        for change in plan.changes:
            by_file.setdefault(change.file, []).append(change)

        touched = []

        for relative, changes in by_file.items():
            data = self._load(relative)

            for change in changes:
                self._apply_change(data, change, payloads)

            self._save(relative, data)
            touched.append(relative)
            self.logger.info(f"Обновлён {relative}: правок {len(changes)}")

        return touched

    def _apply_change(self, data, change: Change, payloads: Dict[str, Dict]) -> None:
        """
        Применить одну правку к дереву документа.

        Args:
            data: Дерево документа
            change: Правка
            payloads: Данные для вставки
        """
        key = f"{change.file}|{change.target}"

        if change.action == 'add_import':
            data.setdefault('imports', []).append(change.target)

        elif change.action == 'add_component':
            body = payloads.get(key)
            if body is None:
                raise RegistrarError(f"Нет данных для компонента {change.target}")
            data.setdefault('components', {})[change.target] = body

        elif change.action == 'add_aspect':
            body = payloads.get(key)
            if body is None:
                raise RegistrarError(f"Нет данных для аспекта {change.target}")
            data.setdefault('aspects', {})[change.target] = body

        elif change.action == 'link_aspect_to_component':
            component = data['components'][change.target]
            component.setdefault('aspects', []).append(change.detail)

        elif change.action == 'add_to_context':
            context = data['contexts'][change.target]
            context.setdefault('components', []).append(change.detail)

        else:
            raise RegistrarError(f"Неизвестное действие: {change.action}")

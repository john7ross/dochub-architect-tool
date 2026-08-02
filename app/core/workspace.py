"""
Рабочее пространство: постоянные пути.

Архитектурный репозиторий и доменная схема не меняются от задачи к задаче,
а спрашивать их в каждой сессии — то же самое, что заново объяснять
соглашения. Пути задаются один раз и потом подставляются сами.

Пути к схеме сервиса и его коду сюда не входят: они у каждой задачи свои.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from app.utils.logger import get_logger

# Где ищем настройки, в порядке убывания приоритета
ENV_VAR = 'DOCHUB_WORKSPACE'
LOCAL_NAME = 'dochub-workspace.yaml'
USER_PATH = Path.home() / '.dochub' / 'workspace.yaml'

FIELDS = {
    'repo_root': 'корень архитектурного репозитория',
    'manifest_path': 'корневой dochub.yaml (обычно <репозиторий>/architecture/dochub.yaml)',
    'ddd_path': 'доменная схема компании ddd.drawio',
    'projects_root': 'каталог, где лежат репозитории сервисов (необязательно)',
}


@dataclass
class Workspace:
    """Постоянные пути."""
    repo_root: Optional[str] = None
    manifest_path: Optional[str] = None
    ddd_path: Optional[str] = None
    projects_root: Optional[str] = None
    source: Optional[str] = None
    warnings: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Optional[str]]:
        """Только сами пути, без служебных полей."""
        return {name: getattr(self, name) for name in FIELDS}

    @property
    def missing(self) -> Dict[str, str]:
        """Чего не хватает — с человеческим описанием."""
        return {
            name: description
            for name, description in FIELDS.items()
            if not getattr(self, name) and name != 'projects_root'
        }


def config_path(explicit: Optional[str] = None) -> Path:
    """
    Определить файл настроек.

    Args:
        explicit: Явно указанный путь

    Returns:
        Путь к файлу настроек
    """
    if explicit:
        return Path(explicit).expanduser()

    from_env = os.environ.get(ENV_VAR)
    if from_env:
        return Path(from_env).expanduser()

    local = Path.cwd() / LOCAL_NAME
    if local.exists():
        return local

    return USER_PATH


def load(explicit: Optional[str] = None) -> Workspace:
    """
    Прочитать настройки.

    Args:
        explicit: Явно указанный файл настроек

    Returns:
        Рабочее пространство; пустое, если файла нет
    """
    path = config_path(explicit)
    workspace = Workspace(source=str(path))

    if not path.exists():
        return workspace

    try:
        data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    except (yaml.YAMLError, OSError) as e:
        workspace.warnings.append(f'не удалось прочитать {path}: {e}')
        return workspace

    for name in FIELDS:
        value = data.get(name)
        if value:
            setattr(workspace, name, str(Path(str(value)).expanduser()))

    # Манифест выводится из корня, если не задан явно
    if workspace.repo_root and not workspace.manifest_path:
        guess = Path(workspace.repo_root) / 'architecture' / 'dochub.yaml'
        if guess.exists():
            workspace.manifest_path = str(guess)

    for name, description in FIELDS.items():
        value = getattr(workspace, name)
        if value and not Path(value).exists():
            workspace.warnings.append(f'{description}: путь не существует — {value}')

    return workspace


def save(values: Dict[str, Optional[str]], explicit: Optional[str] = None) -> Path:
    """
    Записать настройки, сохранив уже заданное.

    Args:
        values: Пути для записи; None и пустые значения игнорируются
        explicit: Явно указанный файл настроек

    Returns:
        Путь к записанному файлу
    """
    path = config_path(explicit)
    current = load(explicit).as_dict()

    for name, value in values.items():
        if name in FIELDS and value:
            current[name] = str(Path(str(value)).expanduser())

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {k: v for k, v in current.items() if v},
            allow_unicode=True, sort_keys=False, default_flow_style=False
        ),
        encoding='utf-8'
    )

    get_logger().info(f'Рабочее пространство сохранено: {path}')
    return path


def resolve(name: str, given: Optional[str] = None) -> Optional[str]:
    """
    Подставить путь из настроек, если он не передан.

    Args:
        name: Имя поля рабочего пространства
        given: Значение, переданное вызывающей стороной

    Returns:
        Путь или None
    """
    if given:
        return given

    return getattr(load(), name, None)

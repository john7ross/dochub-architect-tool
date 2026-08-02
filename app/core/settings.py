"""
Настройки сервера.

Читаются из `.env` в корне проекта. Файл выбран сознательно: его правит
человек один раз при установке, он не уезжает в git и его формат знают все.

Порядок источников, по убыванию приоритета:

1. переменные окружения — их задаёт клиент MCP или системный администратор;
2. `.env` — установка на конкретной машине;
3. `dochub-workspace.yaml` — пути, сохранённые инструментом `dochub_workspace`
   в прошлых сессиях; остаётся ради совместимости;
4. умолчания.

Разбор `.env` свой: внешняя библиотека ради двадцати строк не нужна.
"""

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Dict, List, Optional

from app.core import workspace
from app.utils.logger import get_logger
from app.utils.paths import PROJECT_ROOT

# Где искать .env, в порядке убывания приоритета
ENV_FILE_VAR = 'DOCHUB_ENV'
ENV_FILE_NAME = '.env'

TRUE_WORDS = {'1', 'true', 'yes', 'on', 'да', 'вкл'}
FALSE_WORDS = {'0', 'false', 'no', 'off', 'нет', 'выкл'}

PROTOCOLS = ('https', 'ssh')

# Имя переменной -> (поле, описание). Описание уходит в ответ инструмента:
# пользователь должен видеть, что именно ему настраивать
KEYS = {
    'DOCHUB_REPO_ROOT': ('repo_root', 'корень архитектурного репозитория'),
    'DOCHUB_MANIFEST': ('manifest_path', 'корневой dochub.yaml репозитория'),
    'DOCHUB_DDD_PATH': ('ddd_path', 'доменная схема компании (ddd.drawio)'),
    'DOCHUB_PROJECTS_ROOT': ('projects_root', 'каталог с репозиториями сервисов'),
    'DOCHUB_REMOTE_PROTOCOL': ('remote_protocol', 'как ходим в GitLab: https или ssh'),
    'DOCHUB_TARGET_BRANCH': ('target_branch', 'ветка, в которую идёт merge request'),
    'DOCHUB_BRANCH_PREFIX': ('branch_prefix', 'префикс рабочей ветки'),
    'DOCHUB_COMMIT_TEMPLATE': ('commit_template', 'шаблон сообщения коммита, {schema} — название схемы'),
    'DOCHUB_MR_ASSIGNEE': ('mr_assignee', 'кто отвечает за merge request (username в GitLab)'),
    'DOCHUB_MR_REVIEWERS': ('mr_reviewers', 'кто ревьюит, через запятую (username в GitLab)'),
    'DOCHUB_MR_SQUASH': ('mr_squash', 'схлопывать ли коммиты при мерже'),
    'DOCHUB_MR_REMOVE_SOURCE_BRANCH': ('mr_remove_source_branch', 'удалять ли ветку после мержа'),
    'DOCHUB_AUTOMODE': ('automode', 'агент доводит работу до merge request сам'),
    'DRAWIO_PATH': ('drawio_path', 'путь к Draw.io Desktop для выгрузки картинок'),
}

PATH_FIELDS = ('repo_root', 'manifest_path', 'ddd_path', 'projects_root', 'drawio_path')
BOOL_FIELDS = ('mr_squash', 'mr_remove_source_branch', 'automode')


@dataclass
class Settings:
    """Настройки сервера с указанием, откуда взято каждое значение."""

    # Пути
    repo_root: Optional[str] = None
    manifest_path: Optional[str] = None
    ddd_path: Optional[str] = None
    projects_root: Optional[str] = None
    drawio_path: Optional[str] = None

    # Работа с GitLab
    remote_protocol: str = 'https'
    target_branch: str = 'main'
    branch_prefix: str = 'feature/'
    commit_template: str = 'Добавление схемы {schema}'
    mr_assignee: Optional[str] = None
    mr_reviewers: Optional[str] = None
    mr_squash: bool = False
    mr_remove_source_branch: bool = True

    # Поведение
    automode: bool = False

    # Служебное
    env_file: Optional[str] = None
    sources: Dict[str, str] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    @property
    def reviewers(self) -> List[str]:
        """Ревьюверы списком."""
        if not self.mr_reviewers:
            return []
        return [name.strip() for name in self.mr_reviewers.split(',') if name.strip()]

    def commit_message(self, schema: str) -> str:
        """
        Сообщение коммита для схемы.

        Args:
            schema: Название схемы — сервис или функционал

        Returns:
            Готовое сообщение
        """
        try:
            return self.commit_template.format(schema=schema)
        except (KeyError, IndexError):
            # Шаблон правит человек, и ошибиться в нём легко. Терять из-за
            # этого публикацию нельзя
            self.warnings.append(
                f'шаблон коммита не удалось подставить: {self.commit_template!r}; '
                'разрешена только подстановка {schema}'
            )
            return f'Добавление схемы {schema}'

    def as_dict(self) -> Dict[str, object]:
        """Значения без служебных полей."""
        skip = {'env_file', 'sources', 'warnings'}
        return {f.name: getattr(self, f.name) for f in fields(self) if f.name not in skip}

    @property
    def missing_paths(self) -> Dict[str, str]:
        """Обязательные пути, которых нет."""
        required = {
            'repo_root': 'корень архитектурного репозитория',
            'manifest_path': 'корневой dochub.yaml репозитория',
            'ddd_path': 'доменная схема компании (ddd.drawio)',
        }
        return {
            name: description
            for name, description in required.items()
            if not getattr(self, name)
        }


def env_file_path(explicit: Optional[str] = None) -> Path:
    """
    Определить файл .env.

    Args:
        explicit: Явно указанный путь

    Returns:
        Путь к .env — существующий или тот, где его ждут
    """
    if explicit:
        return Path(explicit).expanduser()

    from_env = os.environ.get(ENV_FILE_VAR)
    if from_env:
        return Path(from_env).expanduser()

    local = Path.cwd() / ENV_FILE_NAME
    if local.exists():
        return local

    return PROJECT_ROOT / ENV_FILE_NAME


def parse_env(text: str) -> Dict[str, str]:
    """
    Разобрать содержимое .env.

    Понимает комментарии, пустые строки, префикс export и кавычки. Значение
    может содержать знак равенства — делим только по первому.

    Args:
        text: Содержимое файла

    Returns:
        Пары имя-значение
    """
    values: Dict[str, str] = {}

    for line in text.splitlines():
        line = line.strip().lstrip('﻿')

        if not line or line.startswith('#'):
            continue

        if line.startswith('export '):
            line = line[len('export '):].lstrip()

        name, sep, value = line.partition('=')
        if not sep:
            continue

        name = name.strip()
        value = value.strip()

        if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
            value = value[1:-1]
        else:
            # Комментарий в конце строки — только для значений без кавычек
            value = value.split(' #')[0].strip()

        if name:
            values[name] = value

    return values


def _as_bool(value: str, name: str, warnings: List[str]) -> Optional[bool]:
    """Привести значение к да/нет, не угадывая молча."""
    lowered = value.strip().lower()

    if lowered in TRUE_WORDS:
        return True
    if lowered in FALSE_WORDS:
        return False

    warnings.append(
        f'{name}: не понял значение {value!r}, жду одно из '
        f'{sorted(TRUE_WORDS | FALSE_WORDS)}'
    )
    return None


def load(explicit_env: Optional[str] = None) -> Settings:
    """
    Прочитать настройки из всех источников.

    Args:
        explicit_env: Явно указанный файл .env

    Returns:
        Настройки; поле sources говорит, откуда взято каждое значение
    """
    settings = Settings()

    # 3. Пути из workspace.yaml — то, что сохранили прошлые сессии
    saved = workspace.load()
    for name, value in saved.as_dict().items():
        if value:
            setattr(settings, name, value)
            settings.sources[name] = 'dochub-workspace.yaml'

    # 2. Файл .env
    path = env_file_path(explicit_env)
    settings.env_file = str(path)
    from_file: Dict[str, str] = {}

    if path.exists():
        try:
            from_file = parse_env(path.read_text(encoding='utf-8'))
        except OSError as e:
            settings.warnings.append(f'не удалось прочитать {path}: {e}')

    # 1. Переменные окружения — последнее слово
    for key, (name, _description) in KEYS.items():
        for value, source in ((from_file.get(key), '.env'),
                              (os.environ.get(key), 'переменная окружения')):
            if value is None or value == '':
                continue

            if name in BOOL_FIELDS:
                parsed = _as_bool(value, key, settings.warnings)
                if parsed is None:
                    continue
                setattr(settings, name, parsed)
            elif name in PATH_FIELDS:
                setattr(settings, name, str(Path(value).expanduser()))
            else:
                setattr(settings, name, value)

            settings.sources[name] = source

    if settings.remote_protocol not in PROTOCOLS:
        settings.warnings.append(
            f'DOCHUB_REMOTE_PROTOCOL: {settings.remote_protocol!r} — '
            f'жду {" или ".join(PROTOCOLS)}, беру https'
        )
        settings.remote_protocol = 'https'

    # Манифест выводится из корня репозитория, если не задан явно
    if settings.repo_root and not settings.manifest_path:
        guess = Path(settings.repo_root) / 'architecture' / 'dochub.yaml'
        if guess.exists():
            settings.manifest_path = str(guess)
            settings.sources['manifest_path'] = 'выведен из repo_root'

    for name in PATH_FIELDS:
        value = getattr(settings, name)
        if value and not Path(value).exists():
            settings.warnings.append(f'{name}: путь не существует — {value}')

    get_logger().info(
        f'Настройки прочитаны: {path if path.exists() else "без .env"}, '
        f'automode={"вкл" if settings.automode else "выкл"}'
    )
    return settings


def resolve(name: str, given: Optional[str] = None) -> Optional[str]:
    """
    Подставить настройку, если значение не передано явно.

    Args:
        name: Имя поля настроек
        given: Значение, переданное вызывающей стороной

    Returns:
        Значение или None
    """
    if given:
        return given

    return getattr(load(), name, None)

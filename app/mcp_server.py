#!/usr/bin/env python3
"""
MCP-сервер DocHub Architect.

Отдаёт агенту детерминированные операции над архитектурным репозиторием:
разбор схем, поиск существующих компонентов, проверку и рендер контекстов,
предпросмотр перед пушем. Смысловую часть — привязку к доменам, бизнес-названия,
группировку в сценарии — делает агент, у которого есть схема, репозиторий
и, если он есть, исходный код сервиса.

Транспорт stdio: сервер запускается клиентом (Cursor, Claude Code, Codex) как
подпроцесс. Логи идут в stderr, потому что stdout занят протоколом.
"""

import json
import re
import subprocess
import sys
from difflib import SequenceMatcher
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator  # noqa: F401

# В SDK 2.x сервер переехал: FastMCP стал MCPServer. Объявление инструментов
# и запуск у них одинаковые, поэтому поддерживаются обе версии — свежая
# установка получает 2.x, а на машине с уже настроенным 1.x ничего не ломается
try:
    from mcp.server import MCPServer as _Server
except ImportError:  # pragma: no cover - зависит от версии пакета на машине
    from mcp.server.fastmcp import FastMCP as _Server

from app.core.dochub import DocHubNativeRenderer, ManifestLoader
from app.core.dochub.native_renderer import DocHubRenderError
from app.core.dochub.registration import RegistrationChecker
from app.core.drawio_export import DrawIOExportError, export as drawio_export
from app.core.knowledge import CATEGORIES, KnowledgeStore
from app.core.dochub.registrar import (
    Plan, Registrar, RegistrarError, locate_owner
)
from app.core.parsers.ddd_parser import DDDParser
from app.core.parsers.diagram_kind import detect_file_kind
from app.core.parsers.drawio_parser import DrawIOParser
from app.core.parsers.plantuml_parser import PlantUMLParser
from app.core.publisher import Publisher, PublishError, changed_paths
from app.core.reconcile import Reconciler
from app.core import ddd_source
from app.core import settings as settings_module
from app.utils.paths import git_command
from app.core import workspace as workspace_module
from app.core.transformer import CYRILLIC, Transformer
from app.preview.renderer import PreviewError, PreviewRenderer
from app.preview.server import PreviewServer

mcp = _Server("dochub_mcp")

DRAWIO_SUFFIXES = ('.drawio', '.xml')
PLANTUML_SUFFIXES = ('.puml', '.plantuml', '.pu')

# Пути, которые не меняются от задачи к задаче, живут в настройках
FROM_WORKSPACE = (
    " Если не указан, берётся из рабочего пространства — см. dochub_workspace"
)

# Живые серверы превью: ключ — пара исходник/YAML
_previews: Dict[str, PreviewServer] = {}


class ResponseFormat(str, Enum):
    """Формат ответа инструмента."""
    MARKDOWN = "markdown"
    JSON = "json"


class ExistingPath(BaseModel):
    """Базовая модель с проверкой существования пути."""

    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')

    @staticmethod
    def _check(value: str, what: str) -> str:
        path = Path(value).expanduser()
        if not path.exists():
            raise ValueError(f"{what} не найден: {value}")
        return str(path)


class ParseSchemaInput(ExistingPath):
    """Параметры разбора схемы."""

    schema_path: str = Field(
        ...,
        description="Путь к схеме: .drawio или .puml "
                    "(например, C:/schemas/OrderService.drawio)"
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="markdown — читаемая сводка, json — полные данные для обработки"
    )
    page: Optional[int] = Field(
        default=None,
        description="Номер страницы с нуля. Без него возвращаются все страницы "
                    "и их состав — в файле DrawIO часто несколько независимых диаграмм",
        ge=0
    )

    @field_validator('schema_path')
    @classmethod
    def validate_schema_path(cls, v: str) -> str:
        return cls._check(v, "Файл схемы")


class ScanRepositoryInput(ExistingPath):
    """Параметры сканирования репозитория."""

    manifest_path: Optional[str] = Field(
        default=None,
        description="Путь к корневому dochub.yaml репозитория."
                    + FROM_WORKSPACE
    )
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)

    @field_validator('manifest_path')
    @classmethod
    def validate_manifest_path(cls, v: Optional[str]) -> Optional[str]:
        return cls._check(v, "Манифест") if v else None


class SearchComponentsInput(ExistingPath):
    """Параметры поиска компонентов."""

    manifest_path: Optional[str] = Field(
        default=None,
        description="Путь к корневому dochub.yaml." + FROM_WORKSPACE
    )
    query: Optional[str] = Field(
        default=None,
        description="Подстрока для поиска по id и title "
                    "(например, 'catalogApi', 'Order', 'mssql')",
        min_length=2,
        max_length=200
    )
    queries: Optional[List[str]] = Field(
        default=None,
        description="Несколько подстрок сразу — например, все элементы схемы. "
                    "Манифест собирается один раз на весь список, поэтому "
                    "искать сотню названий разом дешевле, чем по одному",
        max_length=500
    )
    limit: int = Field(default=30, description="Сколько совпадений вернуть", ge=1, le=200)
    offset: int = Field(default=0, description="Сколько пропустить", ge=0)
    fuzzy: bool = Field(
        default=True,
        description="Искать и похожие названия, а не только точные вхождения. "
                    "Помогает найти дубль, записанный иначе"
    )
    fuzzy_threshold: float = Field(
        default=0.75,
        description="Порог похожести от 0 до 1",
        ge=0.0,
        le=1.0
    )

    @field_validator('manifest_path')
    @classmethod
    def validate_manifest_path(cls, v: Optional[str]) -> Optional[str]:
        return cls._check(v, "Манифест") if v else None


class ContextInput(ExistingPath):
    """Параметры работы с контекстом."""

    yaml_path: str = Field(..., description="Путь к YAML-файлу с контекстом")
    context_id: Optional[str] = Field(
        default=None,
        description="Идентификатор контекста; если не задан — берётся первый в файле"
    )
    manifest_path: Optional[str] = Field(
        default=None,
        description="Корневой dochub.yaml. Без него ссылки на компоненты "
                    "из соседних файлов не разрешатся"
    )

    @field_validator('yaml_path')
    @classmethod
    def validate_yaml_path(cls, v: str) -> str:
        return cls._check(v, "YAML-файл")


class WorkspaceInput(BaseModel):
    """Параметры рабочего пространства."""

    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')

    repo_root: Optional[str] = Field(
        default=None, description="Корень архитектурного репозитория"
    )
    manifest_path: Optional[str] = Field(
        default=None,
        description="Корневой dochub.yaml. Если не задан, выводится из repo_root"
    )
    ddd_path: Optional[str] = Field(
        default=None, description="Доменная схема компании ddd.drawio"
    )
    projects_root: Optional[str] = Field(
        default=None, description="Каталог с репозиториями сервисов"
    )
    save: bool = Field(
        default=False, description="Сохранить переданные значения в файл настроек"
    )
    config_path: Optional[str] = Field(
        default=None, description="Свой файл настроек вместо найденного"
    )


class LessonsInput(BaseModel):
    """Параметры чтения накопленных правил."""

    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')

    query: Optional[str] = Field(
        default=None, description="Подстрока для поиска", max_length=200
    )
    category: Optional[str] = Field(
        default=None,
        description="naming, ownership, inclusion, structure, integration, process"
    )
    as_markdown: bool = Field(
        default=True, description="Вернуть читаемым текстом вместо JSON"
    )
    store_path: Optional[str] = Field(
        default=None,
        description="Свой файл знаний. По умолчанию knowledge/lessons.yaml "
                    "в каталоге приложения. Команда может указать общий"
    )


class RecordLessonsInput(BaseModel):
    """Параметры записи правил."""

    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')

    lessons: List[Dict] = Field(
        ...,
        description="Правила: [{\"rule\": \"...\", \"category\": \"naming\", "
                    "\"rationale\": \"...\"}]",
        min_length=1,
        max_length=50
    )
    source: str = Field(
        default='',
        description="Откуда правила — имя сервиса или схемы",
        max_length=200
    )
    confirmed: bool = Field(
        default=False,
        description="Пользователь согласился загрузить схему. Без этого "
                    "правила не записываются"
    )
    store_path: Optional[str] = Field(
        default=None, description="Свой файл знаний"
    )


class ReconcileInput(ExistingPath):
    """Параметры сверки схемы с кодом."""

    schema_path: str = Field(..., description="Путь к .drawio или .puml")
    project_root: str = Field(
        ...,
        description="Корень репозитория сервиса — папка с исходным кодом"
    )
    kinds: Optional[List[str]] = Field(
        default=None,
        description="Фильтр по видам: on_schema_not_in_code, "
                    "in_code_not_on_schema, name_mismatch, case_mismatch"
    )
    limit: int = Field(default=50, description="Сколько вернуть", ge=1, le=500)
    offset: int = Field(default=0, description="Сколько пропустить", ge=0)

    @field_validator('schema_path')
    @classmethod
    def validate_schema_path(cls, v: str) -> str:
        return cls._check(v, "Файл схемы")

    @field_validator('project_root')
    @classmethod
    def validate_project_root(cls, v: str) -> str:
        return cls._check(v, "Проект")


class LocateOwnerInput(ExistingPath):
    """Параметры поиска владельца компонента."""

    repo_root: Optional[str] = Field(
        default=None,
        description="Корень архитектурного репозитория." + FROM_WORKSPACE
    )
    component_ids: List[str] = Field(
        ...,
        description="Идентификаторы компонентов, например "
                    "[\"dotnet.eventBus.myQueue\", \"mssql.schema.myProc\"]",
        min_length=1,
        max_length=200
    )
    yaml_path: Optional[str] = Field(
        default=None,
        description="Наш файл поддомена — чтобы отметить, какие компоненты чужие"
    )

    @field_validator('repo_root')
    @classmethod
    def validate_repo_root(cls, v: Optional[str]) -> Optional[str]:
        return cls._check(v, "Репозиторий") if v else None


class RegisterSchemaInput(ExistingPath):
    """Параметры внесения схемы в общие и чужие файлы."""

    repo_root: Optional[str] = Field(
        default=None,
        description="Корень архитектурного репозитория." + FROM_WORKSPACE
    )
    yaml_path: Optional[str] = Field(
        default=None,
        description="Файл поддомена — будет подключён в imports, если ещё не подключён"
    )
    roots: Optional[Dict[str, Dict]] = Field(
        default=None,
        description="Корневые платформы: {\"python\": "
                    "{\"title\": \"Python\", \"entity\": \"component\"}}"
    )
    externals: Optional[Dict[str, Dict]] = Field(
        default=None,
        description="Внешние сервисы: {\"externalServices.partner\": "
                    "{\"title\": \"Партнёр\", \"entity\": \"component\", "
                    "\"aspects\": [\"домен.ок.поддомен.функция\"]}}"
    )
    foreign_endpoints: Optional[List[Dict]] = Field(
        default=None,
        description="Методы чужих API. Каждый элемент: owner_file, aspect_id, "
                    "aspect_title, aspect_location, component_id, "
                    "component_title, summary, owner_component, owner_context"
    )
    foreign_components: Optional[List[Dict]] = Field(
        default=None,
        description="Любые компоненты чужих поддоменов: очереди шины событий, "
                    "хранимые процедуры, дашборды. Каждый элемент: owner_file, "
                    "component_id, title, entity (component|database|queue|actor), "
                    "и опционально aspect_id, aspect_title, aspect_location, "
                    "owner_component, owner_context, summary"
    )
    apply: bool = Field(
        default=False,
        description="Применить правки. По умолчанию возвращается только план"
    )

    @field_validator('repo_root')
    @classmethod
    def validate_repo_root(cls, v: Optional[str]) -> Optional[str]:
        return cls._check(v, "Репозиторий") if v else None


class RenderSchemaInput(ExistingPath):
    """Параметры рендера исходной схемы."""

    schema_path: str = Field(..., description="Путь к .puml или .drawio")
    save_to: Optional[str] = Field(
        default=None,
        description="Куда сохранить SVG. Без него содержимое вернётся в ответе — "
                    "для больших схем лучше сохранять в файл"
    )
    page: Optional[int] = Field(
        default=None,
        description="Номер страницы с нуля. По умолчанию первая",
        ge=0
    )
    all_pages: bool = Field(
        default=False,
        description="Выгрузить каждую страницу отдельным файлом. Имена "
                    "получают номер и название страницы"
    )

    @field_validator('schema_path')
    @classmethod
    def validate_schema_path(cls, v: str) -> str:
        return cls._check(v, "Файл схемы")


class RegistrationInput(ExistingPath):
    """Параметры проверки регистрации."""

    repo_root: Optional[str] = Field(
        default=None,
        description="Корень архитектурного репозитория." + FROM_WORKSPACE
    )
    yaml_path: str = Field(..., description="Проверяемый YAML поддомена")

    @field_validator('repo_root')
    @classmethod
    def validate_repo_root(cls, v: Optional[str]) -> Optional[str]:
        return cls._check(v, "Репозиторий") if v else None

    @field_validator('yaml_path')
    @classmethod
    def validate_yaml_path(cls, v: str) -> str:
        return cls._check(v, "YAML-файл")


class DDDTreeInput(ExistingPath):
    """Параметры разбора DDD-схемы."""

    ddd_path: Optional[str] = Field(
        default=None,
        description="Путь к доменной схеме компании ddd.drawio." + FROM_WORKSPACE
    )
    query: Optional[str] = Field(
        default=None,
        description="Фильтр по подстроке в id или названии "
                    "(например, 'orders', 'отказ')",
        max_length=200
    )

    @field_validator('ddd_path')
    @classmethod
    def validate_ddd_path(cls, v: Optional[str]) -> Optional[str]:
        # Схему задают и ссылкой: файла на диске тогда ещё нет
        if not v or ddd_source.is_url(v):
            return v
        return cls._check(v, "DDD-схема")


class PreviewInput(ExistingPath):
    """Параметры запуска превью."""

    schema_path: str = Field(..., description="Исходная схема (.drawio или .puml)")
    yaml_path: str = Field(..., description="Сгенерированный DocHub YAML")
    port: int = Field(
        default=0,
        description="Порт; 0 — выбрать свободный автоматически",
        ge=0,
        le=65535
    )

    @field_validator('schema_path')
    @classmethod
    def validate_schema_path(cls, v: str) -> str:
        return cls._check(v, "Файл схемы")

    @field_validator('yaml_path')
    @classmethod
    def validate_yaml_path(cls, v: str) -> str:
        return cls._check(v, "YAML-файл")


class ChangedEntitiesInput(ExistingPath):
    """Параметры сравнения ревизий."""

    repo_path: str = Field(..., description="Корень git-репозитория")
    base: str = Field(
        ...,
        description="Базовая ревизия: ветка, тег или коммит "
                    "(например, 'main' или 'origin/main')",
        min_length=1,
        max_length=200
    )
    head: str = Field(
        default='HEAD',
        description="Сравниваемая ревизия",
        min_length=1,
        max_length=200
    )

    @field_validator('repo_path')
    @classmethod
    def validate_repo_path(cls, v: str) -> str:
        return cls._check(v, "Репозиторий")


class PublishInput(ExistingPath):
    """Параметры публикации."""

    repo_path: str = Field(..., description="Корень архитектурного репозитория")
    branch: str = Field(
        ...,
        description="Имя ветки, например 'feature/orderServiceScheme'",
        min_length=1,
        max_length=200
    )
    message: Optional[str] = Field(
        default=None,
        description="Текст коммита. Если не задан, собирается из шаблона "
                    "настроек и schema_name",
        max_length=2000
    )
    confirmed: bool = Field(
        default=False,
        description="Пользователь явно подтвердил публикацию. "
                    "Без этого инструмент ничего не делает"
    )
    files: Optional[List[str]] = Field(
        default=None,
        description="Файлы для коммита относительно корня; None — все изменения"
    )
    push: bool = Field(default=True, description="Отправлять ли ветку в GitLab")
    merge_request_title: Optional[str] = Field(
        default=None,
        description="Заголовок merge request; None — MR не создавать",
        max_length=500
    )
    merge_request_description: Optional[str] = Field(
        default=None,
        description="Описание merge request",
        max_length=10000
    )
    target_branch: str = Field(
        default='main',
        description="Ветка назначения merge request",
        max_length=200
    )

    assignee: Optional[str] = Field(
        default=None,
        description="Ответственный за MR (username в GitLab); "
                    "None — берётся из настроек",
        max_length=200
    )
    reviewers: Optional[List[str]] = Field(
        default=None,
        description="Ревьюверы MR (username в GitLab); None — из настроек"
    )
    schema_name: Optional[str] = Field(
        default=None,
        description="Название схемы для шаблона коммита из настроек "
                    "(например, 'OrderService'). Если задано, message можно не передавать",
        max_length=200
    )

    @field_validator('repo_path')
    @classmethod
    def validate_repo_path(cls, v: str) -> str:
        return cls._check(v, "Репозиторий")


class RepoSyncInput(ExistingPath):
    """Параметры синхронизации с архитектурным репозиторием."""

    repo_root: Optional[str] = Field(
        default=None,
        description="Корень архитектурного репозитория." + FROM_WORKSPACE
    )
    branch: Optional[str] = Field(
        default=None,
        description="Рабочая ветка: будет создана от ветки назначения или "
                    "выбрана, если уже есть",
        max_length=200
    )
    target_branch: Optional[str] = Field(
        default=None,
        description="Ветка, относительно которой считается отставание; "
                    "None — из настроек",
        max_length=200
    )
    update: bool = Field(
        default=False,
        description="Подтянуть изменения из origin (только fast-forward)"
    )

    @field_validator('repo_root')
    @classmethod
    def validate_repo_root(cls, v: Optional[str]) -> Optional[str]:
        return cls._check(v, "Репозиторий") if v else None


class BriefInput(ExistingPath):
    """Параметры сбора брифа."""

    schema_path: str = Field(
        ...,
        description="Схема сервиса: .drawio или .puml"
    )
    query: Optional[str] = Field(
        default=None,
        description="Подсказка, где искать место в доменной модели "
                    "(например, 'orders')",
        max_length=200
    )
    ddd_path: Optional[str] = Field(
        default=None,
        description="Доменная схема компании." + FROM_WORKSPACE
    )
    manifest_path: Optional[str] = Field(
        default=None,
        description="Корневой dochub.yaml." + FROM_WORKSPACE
    )
    project_root: Optional[str] = Field(
        default=None,
        description="Каталог с кодом сервиса, если он уже написан"
    )

    @field_validator('schema_path')
    @classmethod
    def validate_schema_path(cls, v: str) -> str:
        return cls._check(v, "Файл схемы")

    @field_validator('ddd_path')
    @classmethod
    def validate_brief_ddd(cls, v: Optional[str]) -> Optional[str]:
        # Схему задают и ссылкой: файла на диске тогда ещё нет
        if not v or ddd_source.is_url(v):
            return v
        return cls._check(v, "DDD-схема")

    @field_validator('manifest_path')
    @classmethod
    def validate_brief_manifest(cls, v: Optional[str]) -> Optional[str]:
        return cls._check(v, "Манифест") if v else None

    @field_validator('project_root')
    @classmethod
    def validate_brief_project(cls, v: Optional[str]) -> Optional[str]:
        return cls._check(v, "Каталог с кодом") if v else None


def _one_line(text: object) -> str:
    """
    Свернуть текст в одну строку.

    Комментарии черновика перечисляют названия со схемы. Перенос строки внутри
    названия уводит вторую половину на строку без `#`, и сохранённый файл
    перестаёт быть YAML.

    Args:
        text: Любое значение со схемы

    Returns:
        Однострочная запись
    """
    return ' '.join(str(text or '').split())


def _read_revision(repo_path: Path, revision: str, relative: str) -> Optional[Dict]:
    """
    Прочитать YAML-файл на заданной ревизии.

    Args:
        repo_path: Корень репозитория
        revision: Ревизия git
        relative: Путь к файлу относительно корня

    Returns:
        Разобранный YAML или None, если файла на этой ревизии нет
    """
    result = subprocess.run(
        [git_command(), 'show', f'{revision}:{relative}'],
        cwd=repo_path,
        capture_output=True,
        # См. Publisher._git: наследованный stdin MCP-сервера вешает git
        stdin=subprocess.DEVNULL,
        timeout=60
    )

    if result.returncode != 0:
        return None

    try:
        return yaml.safe_load(result.stdout.decode('utf-8', errors='replace'))
    except yaml.YAMLError:
        return None


def _need(value: Optional[str], key: str, what: str) -> str:
    """
    Взять путь из параметра или из рабочего пространства.

    Args:
        value: Значение, переданное вызывающей стороной
        key: Имя поля в настройках
        what: Человеческое название для сообщения об ошибке

    Returns:
        Путь

    Raises:
        ValueError: если пути нет ни там, ни там
    """
    resolved = settings_module.resolve(key, value)
    if not resolved:
        raise ValueError(
            f"Не указан {what}. Передайте его параметром, пропишите в .env "
            f"(см. .env.example) или задайте через dochub_workspace — тогда "
            f"он будет подставляться сам"
        )
    return resolved


def _rendered_by_preview(schema_path: Path) -> Optional[str]:
    """
    Взять картинку схемы у открытого превью.

    Страница превью рисует DrawIO встроенным движком и присылает результат
    серверу. Если превью для этого файла запущено, изображение уже есть.

    Args:
        schema_path: Исходная схема

    Returns:
        SVG или None, если превью не открыто либо ещё не отрисовало
    """
    target = schema_path.resolve()

    for server in _previews.values():
        if server.source_path.resolve() == target and server.source_svg:
            return server.source_svg

    return None


def _make_parser(path: Path):
    """
    Подобрать парсер по расширению.

    Args:
        path: Путь к схеме

    Returns:
        Парсер

    Raises:
        ValueError: если формат не поддержан
    """
    suffix = path.suffix.lower()

    if suffix in DRAWIO_SUFFIXES:
        return DrawIOParser(str(path))
    if suffix in PLANTUML_SUFFIXES:
        return PlantUMLParser(str(path))

    raise ValueError(
        f"Формат {suffix} не поддерживается. "
        f"Ожидается один из: {', '.join(DRAWIO_SUFFIXES + PLANTUML_SUFFIXES)}"
    )


def _load_manifest(manifest_path: Optional[str], fallback: Path) -> Dict:
    """
    Собрать манифест или прочитать один файл.

    Args:
        manifest_path: Путь к корневому dochub.yaml
        fallback: Файл, который читается если манифест не задан

    Returns:
        Манифест
    """
    own = yaml.safe_load(fallback.read_text(encoding='utf-8')) or {}

    resolved = workspace_module.resolve('manifest_path', manifest_path)
    if not resolved:
        return own

    manifest = ManifestLoader().load(Path(resolved))

    # Новая схема ещё не подключена к дереву репозитория — в манифесте её
    # нет. Без подмешивания редактируемого файла его собственный контекст
    # «не находится» ровно до регистрации, то есть на всём этапе показа
    return _merge_manifest(manifest, own)


def _merge_manifest(manifest: Dict, own: Dict) -> Dict:
    """
    Наложить редактируемый файл поверх манифеста репозитория.

    Args:
        manifest: Манифест репозитория
        own: Содержимое редактируемого файла

    Returns:
        Копия манифеста с разделами файла
    """
    merged = dict(manifest)
    for section, values in own.items():
        current = merged.get(section)
        if isinstance(values, dict) and isinstance(current, dict):
            merged[section] = {**current, **values}
        else:
            merged[section] = values
    return merged


@mcp.tool(
    name="dochub_parse_schema",
    annotations={
        "title": "Разобрать схему DrawIO или PlantUML",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def dochub_parse_schema(params: ParseSchemaInput) -> str:
    """Разобрать C4-схему и вернуть её элементы, связи и вложенность.

    Для DrawIO восстанавливается иерархия рамок: элементы внутри рамки сервиса
    становятся кандидатами в L3, сама рамка — в L2. Вложенность в C4-шаблоне
    DrawIO не выражена атрибутом parent, она определяется геометрически.

    Args:
        params (ParseSchemaInput): параметры:
            - schema_path (str): путь к .drawio или .puml
            - response_format (ResponseFormat): markdown или json

    Returns:
        str: JSON или markdown со структурой:
        {
            "is_c4": bool,           # схема в нотации C4
            "components": [
                {
                    "id": str,           # внутренний id из схемы
                    "title": str,        # c4Name
                    "type": str,         # Container / Component / System / Person
                    "technology": str,   # c4Technology, может быть null
                    "description": str,  # c4Description, может быть null
                    "parent": str,       # id объемлющей рамки, может быть null
                    "is_boundary": bool  # это рамка, а не компонент
                }
            ],
            "relations": [{"from": str, "to": str, "label": str, "direction": str}]
        }

        Если схема не в C4: {"is_c4": false, "error": "<пояснение>"}

    Examples:
        - "разбери схему сервиса" -> schema_path=".../OrderService.drawio"
        - Не использовать для готового DocHub YAML — это dochub_render_context
    """
    path = Path(params.schema_path)

    # Логические диаграммы отсекаем до разбора и объясняем, почему они не подходят
    kind, problem = detect_file_kind(path)
    if problem:
        return json.dumps(
            {"is_c4": False, "diagram_kind": kind.value, "error": problem},
            ensure_ascii=False, indent=2
        )

    try:
        parser = _make_parser(path)
    except ValueError as e:
        return f"Ошибка: {e}"

    try:
        result = parser.parse()
    except Exception as e:
        return f"Ошибка разбора схемы: {type(e).__name__}: {e}"

    if not result.is_c4:
        return json.dumps(
            {"is_c4": False, "error": result.error_message},
            ensure_ascii=False, indent=2
        )

    if params.page is not None:
        result = result.for_page(params.page)

    components = [
        {
            "id": c.id,
            "title": c.title,
            "type": c.type,
            "technology": c.technology,
            "description": c.description,
            "parent": c.parent,
            "is_boundary": c.is_boundary,
            "page": c.page,
            "page_name": c.page_name
        }
        for c in result.components
    ]
    relations = [
        {"from": r.from_id, "to": r.to_id, "label": r.label, "direction": r.direction}
        for r in result.relations
    ]

    # Один файл DrawIO часто содержит несколько независимых диаграмм.
    # Смешивать их в одну схему нельзя, поэтому показываем состав страниц
    pages = [
        {
            "index": index,
            "name": name,
            "components": sum(
                1 for c in result.components if c.page == index and not c.is_boundary
            ),
            "relations": sum(1 for r in result.relations if r.page == index)
        }
        for index, name in enumerate(result.pages)
    ]

    if params.response_format == ResponseFormat.JSON:
        return json.dumps(
            {"is_c4": True, "pages": pages,
             "components": components, "relations": relations},
            ensure_ascii=False, indent=2
        )

    boundaries = [c for c in components if c["is_boundary"]]
    leaves = [c for c in components if not c["is_boundary"]]
    by_id = {c["id"]: c for c in components}

    lines = [
        f"# Схема {path.name}",
        "",
        f"Элементов: {len(leaves)}, рамок: {len(boundaries)}, связей: {len(relations)}",
        ""
    ]

    if len(pages) > 1:
        lines += [
            f"## Страниц в файле: {len(pages)}",
            "",
            "Это независимые диаграммы. Строить схему сразу по всем нельзя —",
            "спросите пользователя, какая нужна, и передайте её номер в page.",
            ""
        ]
        for item in pages:
            note = ' — без C4-элементов' if not item['components'] else ''
            lines.append(
                f"- **{item['index']}. {item['name']}** — "
                f"элементов {item['components']}, связей {item['relations']}{note}"
            )
        lines.append("")

    for boundary in boundaries:
        children = [c for c in leaves if c["parent"] == boundary["id"]]
        if not children:
            continue
        lines.append(f"## {boundary['title']} ({boundary['type']})")
        for child in children:
            tech = f" [{child['technology']}]" if child["technology"] else ""
            lines.append(f"- **{child['title']}**{tech}")
            if child["description"]:
                lines.append(f"  - {child['description']}")
        lines.append("")

    orphans = [c for c in leaves if not c["parent"] or c["parent"] not in by_id]
    if orphans:
        lines.append("## Вне рамок")
        for c in orphans:
            lines.append(f"- **{c['title']}** ({c['type']})")

    return "\n".join(lines)


@mcp.tool(
    name="dochub_scan_repository",
    annotations={
        "title": "Просканировать архитектурный репозиторий",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def dochub_scan_repository(params: ScanRepositoryInput) -> str:
    """Собрать манифест репозитория и показать, что в нём уже есть.

    Разворачивает imports рекурсивно — так же, как это делает сам DocHub.
    Нужно вызывать до создания новых компонентов, чтобы понимать наличные
    домены и не плодить дубли.

    Args:
        params (ScanRepositoryInput): параметры:
            - manifest_path (str): путь к корневому dochub.yaml
            - response_format (ResponseFormat): markdown или json

    Returns:
        str: JSON или markdown со структурой:
        {
            "components": int,   # всего компонентов
            "contexts": int,     # всего контекстов
            "aspects": int,      # всего аспектов
            "roots": {str: int}, # корневые префиксы id и их количество
            "errors": [str]      # файлы, которые не удалось прочитать
        }

    Examples:
        - "что уже есть в репозитории" -> manifest_path=".../architecture/dochub.yaml"
        - Для поиска конкретного компонента лучше dochub_search_components
    """
    loader = ManifestLoader()

    try:
        manifest = loader.load(Path(_need(
            params.manifest_path, 'manifest_path', 'корневой dochub.yaml')))
    except Exception as e:
        return f"Ошибка сборки манифеста: {type(e).__name__}: {e}"

    components = manifest.get('components') or {}
    contexts = manifest.get('contexts') or {}
    aspects = manifest.get('aspects') or {}

    roots: Dict[str, int] = {}
    for component_id in components:
        root = component_id.split('.')[0]
        roots[root] = roots.get(root, 0) + 1

    summary = {
        "components": len(components),
        "contexts": len(contexts),
        "aspects": len(aspects),
        "roots": dict(sorted(roots.items(), key=lambda kv: -kv[1])),
        "errors": loader.errors
    }

    if params.response_format == ResponseFormat.JSON:
        return json.dumps(summary, ensure_ascii=False, indent=2)

    lines = [
        "# Архитектурный репозиторий",
        "",
        f"Компонентов: {summary['components']}",
        f"Контекстов: {summary['contexts']}",
        f"Аспектов: {summary['aspects']}",
        "",
        "## Корневые префиксы"
    ]
    for root, count in summary["roots"].items():
        lines.append(f"- `{root}` — {count}")

    if summary["errors"]:
        lines += ["", f"## Не прочитано файлов: {len(summary['errors'])}"]
        lines += [f"- {e}" for e in summary["errors"][:20]]

    return "\n".join(lines)


@mcp.tool(
    name="dochub_search_components",
    annotations={
        "title": "Найти компоненты в репозитории",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def dochub_search_components(params: SearchComponentsInput) -> str:
    """Найти существующие компоненты по подстроке в id или названии.

    Главное применение — переиспользование: прежде чем описывать чужой сервис
    (API, базу, очередь), надо найти его готовый id и сослаться на него,
    а не заводить дубль.

    Искать сразу списком дешевле, чем по одному: манифест собирается один раз
    на весь список. На настоящем репозитории (больше тысячи компонентов) сборка
    занимает около полсекунды, и сотня отдельных вызовов — это минута впустую.
    Перед сборкой черновика передавай все названия со схемы одним queries.

    Args:
        params (SearchComponentsInput): параметры:
            - manifest_path (str): путь к корневому dochub.yaml
            - query (str): подстрока для поиска, минимум 2 символа
            - queries (List[str]): несколько подстрок сразу вместо query
            - limit (int): сколько вернуть на запрос, 1..200 (по умолчанию 30)
            - offset (int): сколько пропустить (только для одиночного query)

    Returns:
        str: для одиночного query JSON со структурой:
        {
            "total": int,      # всего совпадений
            "count": int,      # сколько в этом ответе
            "offset": int,
            "has_more": bool,
            "next_offset": int | null,
            "components": [
                {"id": str, "title": str, "entity": str, "aspects": [str]}
            ]
        }

        для queries — то же по каждому запросу:
        {
            "queries": int,               # сколько запросов обработано
            "with_matches": int,          # по скольким что-то нашлось
            "results": [
                {"query": str, "total": int, "components": [...]}
            ]
        }

    Examples:
        - "есть ли уже компонент для Catalog API" -> query="catalogApi"
        - перед сборкой черновика -> queries=[все названия элементов схемы]
    """
    if not params.query and not params.queries:
        return json.dumps(
            {"error": "Нечего искать: передайте query или queries"},
            ensure_ascii=False, indent=2
        )

    try:
        manifest = ManifestLoader().load(Path(_need(
            params.manifest_path, 'manifest_path', 'корневой dochub.yaml')))
    except Exception as e:
        return f"Ошибка сборки манифеста: {type(e).__name__}: {e}"

    components = manifest.get('components') or {}

    def find(needle: str) -> List[Dict]:
        """
        Совпадения по одной подстроке.

        Args:
            needle: Что искать

        Returns:
            Совпадения: точные первыми, дальше по алфавиту
        """
        needle = needle.lower()
        matches = []

        for component_id, data in components.items():
            title = str((data or {}).get('title', ''))
            haystacks = (component_id.lower(), title.lower())

            exact = any(needle in text for text in haystacks)

            # Нечёткое сравнение ловит дубли, которые подстрока пропускает:
            # "OrderService" и "Order Service" пишутся по-разному
            similarity = 0.0
            if params.fuzzy and not exact:
                similarity = max(
                    SequenceMatcher(None, needle, text).ratio()
                    for text in haystacks
                )

            if not exact and similarity < params.fuzzy_threshold:
                continue

            matches.append({
                "id": component_id,
                "title": title,
                "entity": (data or {}).get('entity', 'component'),
                "aspects": (data or {}).get('aspects', []),
                "match": "exact" if exact else f"похоже ({similarity:.2f})"
            })

        matches.sort(key=lambda m: (m["match"] != "exact", m["id"]))
        return matches

    if params.queries:
        # Порядок запросов сохраняем: агент сопоставляет ответ со своим списком
        results = []
        for needle in params.queries:
            if not needle or len(needle.strip()) < 2:
                continue
            found = find(needle.strip())
            results.append({
                "query": needle.strip(),
                "total": len(found),
                "components": found[:params.limit]
            })

        return json.dumps({
            "queries": len(results),
            "with_matches": sum(1 for r in results if r["total"]),
            "results": results
        }, ensure_ascii=False, indent=2)

    matches = find(params.query)
    page = matches[params.offset:params.offset + params.limit]
    has_more = len(matches) > params.offset + len(page)

    return json.dumps({
        "total": len(matches),
        "count": len(page),
        "offset": params.offset,
        "has_more": has_more,
        "next_offset": params.offset + len(page) if has_more else None,
        "components": page
    }, ensure_ascii=False, indent=2)


@mcp.tool(
    name="dochub_validate_context",
    annotations={
        "title": "Проверить контекст DocHub",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def dochub_validate_context(params: ContextInput) -> str:
    """Проверить ссылочную целостность контекста до открытия DocHub.

    Ловит то, на чём валидатор DocHub спотыкается чаще всего: компонент
    контекста не определён, аспект не существует, связь в uml.$after ведёт
    на компонент вне контекста.

    Args:
        params (ContextInput): параметры:
            - yaml_path (str): путь к YAML с контекстом
            - context_id (Optional[str]): контекст; по умолчанию первый в файле
            - manifest_path (Optional[str]): корневой dochub.yaml; без него
              ссылки на соседние файлы будут ложно помечены как ненайденные

    Returns:
        str: JSON со структурой:
        {
            "context_id": str,
            "valid": bool,
            "problems": [
                {"kind": str, "detail": str}   # kind: unknown_component |
                                               # unknown_aspect | link_outside_context
            ]
        }

    Examples:
        - "проверь схему перед коммитом" -> yaml_path + manifest_path
        - Для картинки используйте dochub_render_context
    """
    yaml_path = Path(params.yaml_path)

    own = yaml.safe_load(yaml_path.read_text(encoding='utf-8')) or {}
    contexts = own.get('contexts') or {}

    if not contexts:
        return json.dumps(
            {"valid": False, "problems": [{"kind": "no_contexts",
                                           "detail": "в файле нет контекстов"}]},
            ensure_ascii=False, indent=2
        )

    context_id = params.context_id or next(iter(contexts))
    if context_id not in contexts:
        return json.dumps({
            "valid": False,
            "problems": [{"kind": "unknown_context",
                          "detail": f"контекста {context_id} нет в файле; "
                                    f"есть: {', '.join(list(contexts)[:10])}"}]
        }, ensure_ascii=False, indent=2)

    manifest = _load_manifest(params.manifest_path, yaml_path)
    known_components = set(manifest.get('components') or {})
    known_aspects = set(manifest.get('aspects') or {})

    context = contexts[context_id] or {}
    listed = list(context.get('components') or [])
    problems: List[Dict[str, str]] = []

    for component_id in listed:
        if component_id not in known_components:
            problems.append({
                "kind": "unknown_component",
                "detail": f"{component_id} не определён"
                          + ("" if params.manifest_path
                             else " (манифест не передан — возможно ложное срабатывание)")
            })
            continue

        for aspect_id in (manifest['components'][component_id] or {}).get('aspects') or []:
            if aspect_id not in known_aspects:
                problems.append({
                    "kind": "unknown_aspect",
                    "detail": f"{component_id} ссылается на аспект {aspect_id}, "
                              f"которого нет"
                })

    uml = context.get('uml')
    if isinstance(uml, dict):
        in_context = set(listed)
        for line in str(uml.get('$after') or '').splitlines():
            for token in line.replace(':', ' ').split():
                if '.' in token and token not in in_context and token in known_components:
                    problems.append({
                        "kind": "link_outside_context",
                        "detail": f"связь ссылается на {token}, "
                                  f"но его нет в components контекста"
                    })

    return json.dumps({
        "context_id": context_id,
        "valid": not problems,
        "problems": problems
    }, ensure_ascii=False, indent=2)


@mcp.tool(
    name="dochub_workspace",
    annotations={
        "title": "Постоянные пути: репозиторий и доменная схема",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def dochub_workspace(params: WorkspaceInput) -> str:
    """Прочитать или задать пути, которые не меняются от задачи к задаче.

    Вызывать В САМОМ НАЧАЛЕ, вместе с dochub_lessons. Архитектурный
    репозиторий и доменная схема одни и те же для всех схем, и спрашивать их
    каждый раз незачем — они подставляются в остальные инструменты сами.

    Если чего-то не хватает, спроси у пользователя именно это и сохрани,
    передав значения обратно: в следующий раз вопрос уже не понадобится.

    Путь к схеме сервиса и к его коду сюда не входят — они у каждой задачи
    свои и передаются напрямую.

    Настройки установки лежат в .env рядом с проектом (образец —
    .env.example): там же протокол GitLab, ветка назначения, шаблон коммита,
    параметры merge request и режим automode. Этот инструмент показывает их
    все и говорит, откуда взято каждое значение.

    Пути, сохранённые здесь с save=true, живут отдельно, в
    dochub-workspace.yaml: он ищется по переменной DOCHUB_WORKSPACE, затем в
    текущем каталоге, затем в ~/.dochub/workspace.yaml. Значения из .env и из
    окружения имеют приоритет над ним.

    Args:
        params (WorkspaceInput): параметры:
            - repo_root (Optional[str]): корень архитектурного репозитория
            - manifest_path (Optional[str]): корневой dochub.yaml; выводится
              из repo_root, если не задан
            - ddd_path (Optional[str]): доменная схема ddd.drawio
            - projects_root (Optional[str]): где лежат репозитории сервисов
            - save (bool): сохранить переданные значения

    Returns:
        str: JSON со структурой:
        {
            "source": str,              # файл сохранённых путей
            "env_file": str,            # файл .env, если он найден
            "saved": bool,
            "paths": {                  # текущие значения
                "repo_root": str | null,
                "manifest_path": str | null,
                "ddd_path": str | null,
                "projects_root": str | null
            },
            "gitlab": {
                "remote_protocol": "https" | "ssh",
                "target_branch": str,
                "branch_prefix": str,
                "commit_template": str,
                "mr_assignee": str | null,
                "mr_reviewers": [str],
                "mr_squash": bool,
                "mr_remove_source_branch": bool
            },
            "automode": bool,           # агент доводит до MR сам
            "sources": {str: str},      # откуда взято каждое значение
            "missing": {str: str},      # чего не хватает и что это такое
            "warnings": [str]           # например, путь указан, но не существует
        }

    Examples:
        - Начало работы -> без параметров, посмотреть что настроено
        - Пользователь назвал пути -> передать их с save=true
    """
    if params.save:
        workspace_module.save(
            {
                'repo_root': params.repo_root,
                'manifest_path': params.manifest_path,
                'ddd_path': params.ddd_path,
                'projects_root': params.projects_root,
            },
            params.config_path
        )

    workspace = workspace_module.load(params.config_path)
    current = settings_module.load()

    return json.dumps({
        "source": workspace.source,
        "env_file": current.env_file,
        "saved": params.save,
        "paths": {
            "repo_root": current.repo_root,
            "manifest_path": current.manifest_path,
            "ddd_path": current.ddd_path,
            "projects_root": current.projects_root,
        },
        "gitlab": {
            "remote_protocol": current.remote_protocol,
            "target_branch": current.target_branch,
            "branch_prefix": current.branch_prefix,
            "commit_template": current.commit_template,
            "mr_assignee": current.mr_assignee,
            "mr_reviewers": current.reviewers,
            "mr_squash": current.mr_squash,
            "mr_remove_source_branch": current.mr_remove_source_branch,
        },
        "automode": current.automode,
        "sources": current.sources,
        "missing": current.missing_paths,
        "warnings": workspace.warnings + current.warnings,
    }, ensure_ascii=False, indent=2)


@mcp.tool(
    name="dochub_lessons",
    annotations={
        "title": "Накопленные правила по переносу схем",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def dochub_lessons(params: LessonsInput) -> str:
    """Прочитать правила, накопленные на прошлых схемах.

    Вызывать В НАЧАЛЕ работы, до уточняющих вопросов. Здесь лежат решения,
    подтверждённые пользователем на прошлых схемах: как называют компоненты,
    чей поддомен владеет очередями, что выносят на диаграмму, а что нет.

    Правила, подтверждённые несколько раз, надёжнее: у них выше `seen`.
    Если накопленное правило противоречит тому, что видно в текущей схеме,
    не молчи — покажи противоречие пользователю.

    Args:
        params (LessonsInput): параметры:
            - query (Optional[str]): подстрока для поиска
            - category (Optional[str]): naming, ownership, inclusion,
              structure, integration, process
            - as_markdown (bool): вернуть читаемым текстом

    Returns:
        str: markdown с правилами по категориям либо JSON:
        {
            "total": int,
            "categories": {str: str},   # известные категории
            "lessons": [
                {"rule": str, "category": str, "rationale": str,
                 "source": str, "seen": int, "added": str}
            ]
        }

    Examples:
        - Начало работы -> без параметров
        - "как тут называют компоненты" -> category="naming"
    """
    store = KnowledgeStore(Path(params.store_path) if params.store_path else None)
    found = store.search(params.query or '', params.category)

    if params.as_markdown:
        return store.as_markdown(found)

    return json.dumps({
        "total": len(found),
        "categories": CATEGORIES,
        "lessons": [
            {
                "rule": item.rule,
                "category": item.category,
                "rationale": item.rationale,
                "source": item.source,
                "seen": item.seen,
                "added": item.added,
            }
            for item in found
        ]
    }, ensure_ascii=False, indent=2)


@mcp.tool(
    name="dochub_record_lessons",
    annotations={
        "title": "Записать правила, найденные при работе над схемой",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False
    }
)
async def dochub_record_lessons(params: RecordLessonsInput) -> str:
    """Сохранить решения и находки, чтобы следующая схема далась быстрее.

    Вызывать ТОЛЬКО после того, как пользователь согласился загрузить схему:
    согласие означает, что схема сделана правильно, и принятые по дороге
    решения можно считать образцом.

    Записывай то, что переиспользуемо на других схемах:

      - как пользователь просил называть сущности;
      - чей поддомен владеет очередями, API, процедурами;
      - что он попросил убрать со схемы и что добавить;
      - какие расхождения кода и схемы оказались значимыми.

    Не записывай подробности конкретного сервиса: «у OrderService
    девять классов» следующей схеме не поможет. Формулируй правилом:
    «структурные рамки DTO и Entities на схему не выносим».

    Повтор уже известного правила не создаёт дубль — увеличивается счётчик
    подтверждений.

    Args:
        params (RecordLessonsInput): параметры:
            - lessons (List[dict]): каждый элемент — rule (обязательно),
              category (naming | ownership | inclusion | structure |
              integration | process), rationale (желательно)
            - source (str): имя сервиса или схемы, откуда правило
            - confirmed (bool): пользователь согласился загрузить схему

    Returns:
        str: JSON со структурой:
        {
            "added": [str],      # новые правила
            "merged": [str],     # подтверждения уже известных
            "rejected": [str],   # что не принято и почему
            "total": int         # сколько правил в хранилище
        }

    Examples:
        - После «да, публикуй» -> confirmed=true и список правил
        - До согласия пользователя записывать нельзя
    """
    if not params.confirmed:
        return json.dumps({
            "error": "Правила записываются только после согласия пользователя "
                     "на загрузку схемы: до этого решения не считаются "
                     "подтверждёнными"
        }, ensure_ascii=False, indent=2)

    store = KnowledgeStore(Path(params.store_path) if params.store_path else None)
    result = store.record(params.lessons, source=params.source)

    return json.dumps({
        "added": result.added,
        "merged": result.merged,
        "rejected": result.rejected,
        "total": len(store.load()),
    }, ensure_ascii=False, indent=2)


@mcp.tool(
    name="dochub_reconcile_with_code",
    annotations={
        "title": "Сверить схему с кодом сервиса",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def dochub_reconcile_with_code(params: ReconcileInput) -> str:
    """Сопоставить схему с исходным кодом и показать все расхождения.

    Схема и код расходятся почти всегда: схему рисовали раньше, код менялся.
    Инструмент показывает расхождения со ссылками на файл и строку, но
    НИЧЕГО не решает сам. Что включать в архитектурную схему, а что нет,
    зависит от конкретного случая, и решение принимает пользователь.

    Показывай ему расхождения и спрашивай по каждому спорному, опираясь на
    первоисточники: код, арх.репозиторий, DDD-схему.

    Если кода нет, вызывать инструмент не нужно: схема строится строго по
    тому, что нарисовано, без домысливания.

    Виды расхождений:
      on_schema_not_in_code — есть на схеме, в коде не найдено. Схема
        устарела, либо элемент логический (очередь, внешняя система)
      in_code_not_on_schema — класс есть в коде, на схеме не отражён
      name_mismatch — похожее имя, вероятна опечатка
      case_mismatch — отличается только регистр

    Args:
        params (ReconcileInput): параметры:
            - schema_path (str): путь к .drawio или .puml
            - project_root (str): корень репозитория сервиса
            - kinds (Optional[List[str]]): какие виды показать
            - limit (int): сколько расхождений вернуть, 1..500
            - offset (int): сколько пропустить

    Returns:
        str: JSON со структурой:
        {
            "scanned_files": int,
            "symbols_found": int,
            "matched": int,            # сошлось имён
            "summary": {str: int},     # сколько расхождений каждого вида
            "total": int,
            "count": int,
            "offset": int,
            "has_more": bool,
            "discrepancies": [
                {
                    "kind": str,
                    "name": str,
                    "detail": str,
                    "schema_element": str | null,
                    "code_locations": [str]     # "файл:строка"
                }
            ]
        }

    Examples:
        - "сверь схему с кодом" -> schema_path + project_root
        - "покажи только пропущенное на схеме" -> kinds=["in_code_not_on_schema"]
    """
    path = Path(params.schema_path)

    kind, problem = detect_file_kind(path)
    if problem:
        return json.dumps({"error": problem, "diagram_kind": kind.value},
                          ensure_ascii=False, indent=2)

    try:
        parsed = _make_parser(path).parse()
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"},
                          ensure_ascii=False, indent=2)

    if not parsed.is_c4:
        return json.dumps({"error": parsed.error_message},
                          ensure_ascii=False, indent=2)

    try:
        report = Reconciler(Path(params.project_root)).reconcile(parsed.components)
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"},
                          ensure_ascii=False, indent=2)

    items = [
        d for d in report.discrepancies
        if not params.kinds or d.kind in params.kinds
    ]
    # Сначала то, что чаще требует решения
    order = {'name_mismatch': 0, 'case_mismatch': 1,
             'on_schema_not_in_code': 2, 'in_code_not_on_schema': 3}
    items.sort(key=lambda d: (order.get(d.kind, 9), d.name.lower()))

    page = items[params.offset:params.offset + params.limit]

    return json.dumps({
        "scanned_files": report.scanned_files,
        "symbols_found": report.symbols_found,
        "matched": len(report.matched),
        "summary": report.by_kind,
        "total": len(items),
        "count": len(page),
        "offset": params.offset,
        "has_more": len(items) > params.offset + len(page),
        "discrepancies": [
            {
                "kind": d.kind,
                "name": d.name,
                "detail": d.detail,
                "schema_element": d.schema_element,
                "code_locations": d.code_locations
            }
            for d in page
        ]
    }, ensure_ascii=False, indent=2)


@mcp.tool(
    name="dochub_locate_owner",
    annotations={
        "title": "Чей это компонент и в какой файл его писать",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def dochub_locate_owner(params: LocateOwnerInput) -> str:
    """Определить, какому поддомену принадлежит компонент и в чей файл его вносить.

    Ключевое правило репозитория: компонент и его аспекты описываются в файле
    того поддомена, которому принадлежат по смыслу, а не того, кто их
    использует. Очередь в шине событий, метод чужого API, хранимая процедура
    в чужой схеме БД, дашборд чужой команды — всё это заводится у владельца,
    даже если создано специально под наш сервис. В своём поддомене такой
    компонент только упоминается в `contexts.components` и в связях
    `uml.$after` по полному идентификатору.

    Инструмент ищет файл по самому длинному совпадающему префиксу
    идентификатора. Для "dotnet.eventBus.newQueue" он найдёт файл, где
    объявлен "dotnet.eventBus", и подскажет его L2-компонент и контексты.

    Args:
        params (LocateOwnerInput): параметры:
            - repo_root (str): корень архитектурного репозитория
            - component_ids (List[str]): идентификаторы для проверки

    Returns:
        str: JSON со структурой:
        {
            "results": [
                {
                    "component_id": str,
                    "file": str | null,        # куда писать; null — владелец не найден
                    "matched_prefix": str,     # по какому префиксу определили
                    "exists": bool,            # компонент уже описан
                    "is_foreign": bool,        # владелец — не наш файл
                    "l2_component": str,       # L2 владельца для связи аспекта
                    "contexts": [str]          # контексты файла владельца
                }
            ]
        }

    Examples:
        - "куда писать очередь события" -> component_ids=["dotnet.eventBus.myQueue"]
        - Владелец не найден -> компонент новый, заводится в своём поддомене
    """
    repo_root = Path(_need(params.repo_root, 'repo_root', 'корень репозитория'))
    own_file = Path(params.yaml_path).resolve() if params.yaml_path else None
    results = []

    for component_id in params.component_ids:
        try:
            found = locate_owner(repo_root, component_id)
        except Exception as e:
            results.append({"component_id": component_id,
                            "error": f"{type(e).__name__}: {e}"})
            continue

        is_foreign = False
        if found['file'] and own_file:
            is_foreign = (repo_root / found['file']).resolve() != own_file

        results.append({
            "component_id": component_id,
            "file": found['file'],
            "matched_prefix": found['matched_prefix'],
            "exists": found['exists'],
            "is_foreign": is_foreign,
            "l2_component": found['l2_component'],
            "contexts": found['contexts']
        })

    return json.dumps({"results": results}, ensure_ascii=False, indent=2)


@mcp.tool(
    name="dochub_register_schema",
    annotations={
        "title": "Внести схему в общие и чужие файлы репозитория",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def dochub_register_schema(params: RegisterSchemaInput) -> str:
    """Прописать схему в общих файлах репозитория и в файлах владельцев API.

    Схема почти никогда не помещается в один файл поддомена. Перенос сервиса
    обычно затрагивает:

    - `architecture/common/general/root.yaml` — корневые платформы
      (`python`, `postgres`, `redis`), без них валидатор ругается
      «компонент верхнего уровня не определён»;
    - `architecture/common/external/root.yaml` — внешние сервисы, со ссылкой
      на аспекты нашего поддомена;
    - `architecture/dochub.yaml` — подключение файла, если он новый;
    - файлы ЧУЖИХ доменов — когда наш сервис вызывает их API. Там нужны
      сразу четыре вещи: аспект в их пространстве имён, ссылка на него в их
      L2-компоненте, ссылка в их каталожном контексте и L3-компонент метода.

    По умолчанию работает как dry-run: возвращает план, ничего не меняя.
    Ставь apply=true только после того, как показал план пользователю.

    Правка чужого поддомена требует согласования с его владельцем — если
    в плане есть пометка foreign, скажи об этом пользователю прямо.

    Args:
        params (RegisterSchemaInput): параметры:
            - repo_root (str): корень архитектурного репозитория
            - yaml_path (Optional[str]): файл поддомена для подключения в imports
            - roots (Optional[dict]): {id: {title, entity}} — корневые платформы
            - externals (Optional[dict]): {id: {title, aspects}} — внешние сервисы
            - foreign_endpoints (Optional[list]): методы чужих API, каждый —
              {owner_file, aspect_id, aspect_title, aspect_location,
               component_id, component_title, summary,
               owner_component, owner_context}
            - apply (bool): применить правки; по умолчанию false

    Returns:
        str: JSON со структурой:
        {
            "applied": bool,
            "touches_foreign": bool,      # есть правки в чужих поддоменах
            "files": [str],               # какие файлы затронуты
            "own_subdomains": [str],      # свои файлы поддоменов
            "foreign_subdomains": [str],  # чужие — их согласуют с владельцами
            "warning": str | null,        # готовая формулировка для пользователя
            "changes": [
                {"file": str, "action": str, "target": str,
                 "detail": str, "foreign": bool}
            ]
        }

        Списки own_subdomains и foreign_subdomains показывай пользователю
        ДО apply: он должен видеть, в какие поддомены вносятся правки и на
        какие поддомены распространяется схема.

        При ошибке: {"error": "<описание>"}

    Examples:
        - "что нужно дописать в общие файлы" -> без apply
        - Пользователь одобрил план -> apply=true
    """
    try:
        registrar = Registrar(Path(_need(
            params.repo_root, 'repo_root', 'корень репозитория')))
        plan = Plan()
        payloads: Dict[str, Dict] = {}

        if params.roots:
            sub = registrar.plan_roots(params.roots)
            plan.changes.extend(sub.changes)
            for change in sub.changes:
                payloads[f"{change.file}|{change.target}"] = params.roots[change.target]

        if params.externals:
            sub = registrar.plan_externals(params.externals)
            plan.changes.extend(sub.changes)
            for change in sub.changes:
                payloads[f"{change.file}|{change.target}"] = \
                    params.externals[change.target]

        if params.yaml_path:
            plan.changes.extend(registrar.plan_import(Path(params.yaml_path)).changes)

        for component in params.foreign_components or []:
            sub = registrar.plan_foreign_component(**component)
            plan.changes.extend(sub.changes)

            owner_file = component['owner_file']
            body = {
                'title': component['title'],
                'entity': component.get('entity', 'component')
            }
            if component.get('summary'):
                body['summary'] = component['summary']
            if component.get('aspect_id'):
                body['aspects'] = [component['aspect_id']]
                payloads[f"{owner_file}|{component['aspect_id']}"] = {
                    'title': component.get('aspect_title', component['aspect_id']),
                    'location': component.get('aspect_location', '')
                }
            payloads[f"{owner_file}|{component['component_id']}"] = body

        for endpoint in params.foreign_endpoints or []:
            sub = registrar.plan_foreign_endpoint(**endpoint)
            plan.changes.extend(sub.changes)

            owner_file = endpoint['owner_file']
            payloads[f"{owner_file}|{endpoint['aspect_id']}"] = {
                'title': endpoint['aspect_title'],
                'location': endpoint['aspect_location']
            }
            payloads[f"{owner_file}|{endpoint['component_id']}"] = {
                'title': endpoint['component_title'],
                'entity': 'component',
                'summary': endpoint['summary'],
                'aspects': [endpoint['aspect_id']]
            }

    except (RegistrarError, KeyError, TypeError) as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"},
                          ensure_ascii=False, indent=2)

    # Список изменений длинный, и «правим чужой поддомен» в нём теряется.
    # Поддомен — это файл, поэтому владельцев видно по файлам: их и показываем
    # отдельно, чтобы агент назвал пользователю поимённо, что будет затронуто
    own_files = sorted({c.file for c in plan.changes if not c.foreign})
    foreign_files = sorted({c.file for c in plan.changes if c.foreign})

    result = {
        "applied": False,
        "touches_foreign": plan.touches_foreign,
        "files": plan.files,
        "own_subdomains": own_files,
        "foreign_subdomains": foreign_files,
        "warning": (
            "Правки затрагивают чужие поддомены: "
            + ", ".join(foreign_files)
            + ". Покажите этот список пользователю и согласуйте с владельцами "
              "до apply=true"
        ) if foreign_files else None,
        "changes": [
            {
                "file": c.file, "action": c.action, "target": c.target,
                "detail": c.detail, "foreign": c.foreign
            }
            for c in plan.changes
        ]
    }

    if not params.apply or not plan.changes:
        return json.dumps(result, ensure_ascii=False, indent=2)

    try:
        registrar.apply(plan, payloads)
    except RegistrarError as e:
        return json.dumps({"error": str(e), "changes": result["changes"]},
                          ensure_ascii=False, indent=2)

    result["applied"] = True
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool(
    name="dochub_render_schema",
    annotations={
        "title": "Отрисовать исходную схему в SVG",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def dochub_render_schema(params: RenderSchemaInput) -> str:
    """Отрисовать исходную схему DrawIO или PlantUML в SVG.

    Рендер полностью локальный: PlantUML из комплекта поставки, библиотека C4
    из его же stdlib. Если схема подключает файлы с недоступного сервера,
    она всё равно будет отрисована — внешние !include отключаются, потому что
    отвечают за оформление и иконки, а структура лежит в самом файле. Об этом
    сообщается в warnings.

    Схемы DrawIO возвращаются как XML: их рисует встроенный движок drawio в
    превью, отдельный рендер для них не нужен.

    Args:
        params (RenderSchemaInput): параметры:
            - schema_path (str): путь к .puml или .drawio
            - save_to (Optional[str]): куда сохранить SVG; без него содержимое
              возвращается в ответе

    Returns:
        str: JSON со структурой:
        {
            "kind": "svg" | "drawio",
            "saved_to": str | null,
            "bytes": int,
            "warnings": [str],     # например, отрисовано без внешних стилей
            "svg": str             # только если save_to не задан и kind == svg
        }

        При ошибке: {"error": "<описание>"}

    Examples:
        - "покажи исходную схему" -> schema_path
        - "сохрани картинку схемы" -> schema_path + save_to
    """
    path = Path(params.schema_path)

    # DrawIO рисует только его собственный движок. Он у нас вендорится и
    # работает в превью, поэтому картинку берём оттуда, а не ищем
    # установленное приложение
    if path.suffix.lower() in ('.drawio', '.xml') and params.save_to:
        target = Path(params.save_to)
        image_format = target.suffix.lstrip('.').lower() or 'svg'

        rendered = _rendered_by_preview(path)
        if rendered and image_format == 'svg':
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(rendered, encoding='utf-8')
            return json.dumps({
                "kind": "svg",
                "saved_to": str(target),
                "bytes": len(rendered.encode('utf-8')),
                "source": "встроенный движок DrawIO",
                "warnings": []
            }, ensure_ascii=False, indent=2)

        # Многостраничный файл выгружаем постранично: одна картинка на все
        # диаграммы бессмысленна, они между собой не связаны
        pages = PreviewRenderer.list_pages(path)
        if params.all_pages and len(pages) > 1:
            saved_files = []
            for index, name in enumerate(pages):
                safe = re.sub(r'[^\w\-. ]', '_', name).strip() or f'page{index + 1}'
                per_page = target.with_name(
                    f"{target.stem}_{index + 1}_{safe}{target.suffix}"
                )
                try:
                    drawio_export(path, per_page, image_format, page=index)
                    saved_files.append(str(per_page))
                except DrawIOExportError as e:
                    return json.dumps(
                        {"error": str(e), "saved_before_failure": saved_files},
                        ensure_ascii=False, indent=2
                    )

            return json.dumps({
                "kind": image_format,
                "pages": len(pages),
                "saved_to": saved_files,
                "source": "Draw.io Desktop"
            }, ensure_ascii=False, indent=2)

        # Запасной путь: другой формат или превью ещё не открыто
        try:
            saved = drawio_export(path, target, image_format, page=params.page)
        except DrawIOExportError as e:
            hint = str(e)
            if not rendered:
                hint += (
                    "\n\nПроще всего открыть превью (dochub_preview): схема "
                    "отрисуется встроенным движком, и SVG станет доступен "
                    "без установки чего-либо."
                )
            return json.dumps({"error": hint}, ensure_ascii=False, indent=2)

        return json.dumps({
            "kind": image_format,
            "saved_to": str(saved),
            "bytes": saved.stat().st_size,
            "source": "Draw.io Desktop",
            "warnings": []
        }, ensure_ascii=False, indent=2)

    try:
        payload = PreviewRenderer().describe_source(path)
    except PreviewError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False, indent=2)

    data = payload['data']
    result = {
        "kind": payload['kind'],
        "saved_to": None,
        "bytes": len(data.encode('utf-8')),
        "warnings": payload.get('warnings') or []
    }

    if payload['kind'] == 'drawio':
        result["warnings"] = [
            "Это исходный XML схемы, а не картинка. DrawIO рисуется своим "
            "движком: в превью (dochub_preview) он встроен, а для выгрузки "
            "в файл укажите save_to — тогда сработает Draw.io Desktop."
        ]

    if params.save_to:
        target = Path(params.save_to)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(data, encoding='utf-8')
        result["saved_to"] = str(target)
    elif payload['kind'] == 'svg':
        result["svg"] = data

    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool(
    name="dochub_check_registration",
    annotations={
        "title": "Проверить подключение схемы к дереву репозитория",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def dochub_check_registration(params: RegistrationInput) -> str:
    """Проверить, что схема подключена и её сущности объявлены в общих файлах.

    Положить YAML в каталог поддомена недостаточно: DocHub не увидит файл,
    пока он не подключён в architecture/dochub.yaml, а валидатор будет
    ругаться, если не объявлены корневые платформы, родительские аспекты и
    внешние сервисы. Формулировки валидатора при этом малопонятны, поэтому
    проверять лучше до открытия DocHub.

    Вызывать после сборки YAML и обязательно перед публикацией.

    Args:
        params (RegistrationInput): параметры:
            - repo_root (str): корень архитектурного репозитория
            - yaml_path (str): проверяемый файл поддомена

    Returns:
        str: JSON со структурой:
        {
            "ok": bool,                 # всё подключено и объявлено
            "imported": bool,           # файл достижим из корневого dochub.yaml
            "import_path": str,         # что дописать в imports, если нет
            "manifest_file": str,       # куда именно дописывать
            "missing_roots": [          # нет корневой платформы (python, mssql…)
                {"id": str, "where": str, "hint": str}
            ],
            "missing_parent_aspects": [...],     # для "a.b.c" нет "a" или "a.b"
            "missing_parent_components": [...],
            "missing_externals": [...],          # externalServices.* не объявлен
            "orphan_components": [str]           # компонент вне всех контекстов
        }

    Examples:
        - "проверь перед коммитом" -> repo_root + yaml_path
        - Ошибка валидатора «аспект верхнего уровня не определён» -> сюда же
    """
    try:
        checker = RegistrationChecker(Path(_need(
            params.repo_root, 'repo_root', 'корень репозитория')))
        report = checker.check(Path(params.yaml_path))
    except Exception as e:
        return json.dumps(
            {"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False, indent=2
        )

    # Без корневого манифеста проверять нечего, и пустой отчёт об этом не
    # говорит: агент прочитает ok=false и решит, что файл просто не подключён
    if report.manifest_file is None:
        return json.dumps({
            "error": "Корневой манифест не найден: искали "
                     "architecture/dochub.yaml и dochub.yaml в repo_root. "
                     "Проверьте repo_root — это корень архитектурного "
                     "репозитория, а не каталог поддомена"
        }, ensure_ascii=False, indent=2)

    def dump(items) -> List[Dict[str, str]]:
        return [{"id": m.id, "where": m.where, "hint": m.hint} for m in items]

    return json.dumps({
        "ok": report.ok,
        "imported": report.imported,
        "import_path": report.import_path,
        "manifest_file": report.manifest_file,
        "missing_roots": dump(report.missing_roots),
        "missing_parent_aspects": dump(report.missing_parent_aspects),
        "missing_parent_components": dump(report.missing_parent_components),
        "missing_externals": dump(report.missing_externals),
        "orphan_components": report.orphan_components
    }, ensure_ascii=False, indent=2)


@mcp.tool(
    name="dochub_render_context",
    annotations={
        "title": "Отрендерить контекст DocHub",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def dochub_render_context(params: ContextInput) -> str:
    """Собрать PlantUML контекста родным конвейером DocHub.

    Использует метамодель самого DocHub, поэтому результат совпадает с тем,
    что покажет DocHub: вложенные области, аспекты внутри блоков. Полезно,
    чтобы убедиться, что контекст вообще рендерится, и увидеть его состав.

    Args:
        params (ContextInput): параметры:
            - yaml_path (str): путь к YAML с контекстом
            - context_id (Optional[str]): контекст; по умолчанию первый
            - manifest_path (Optional[str]): корневой dochub.yaml; без него
              чужие компоненты не разрешатся

    Returns:
        str: JSON со структурой:
        {
            "context_id": str,
            "ok": bool,
            "plantuml": str,     # исходник диаграммы
            "elements": int,     # блоков на диаграмме
            "regions": int       # вложенных областей
        }

        При ошибке: {"ok": false, "error": "<описание>"}

    Examples:
        - "покажи, что получилось" -> yaml_path + manifest_path
        - Чтобы сравнить глазами с оригиналом — dochub_preview
    """
    yaml_path = Path(params.yaml_path)
    manifest = _load_manifest(params.manifest_path, yaml_path)

    own = yaml.safe_load(yaml_path.read_text(encoding='utf-8')) or {}
    context_id = params.context_id or next(iter(own.get('contexts') or {}), None)

    if not context_id:
        return json.dumps({"ok": False, "error": "в файле нет контекстов"},
                          ensure_ascii=False, indent=2)

    try:
        renderer = PreviewRenderer()
        plantuml = DocHubNativeRenderer().render_context(
            manifest,
            context_id,
            render_core=None if renderer.elk_available() else 'smetana'
        )
    except DocHubRenderError as e:
        return json.dumps({"ok": False, "context_id": context_id, "error": str(e)},
                          ensure_ascii=False, indent=2)

    return json.dumps({
        "context_id": context_id,
        "ok": True,
        "plantuml": plantuml,
        "elements": plantuml.count('$Entity('),
        "regions": plantuml.count('$Region(')
    }, ensure_ascii=False, indent=2)


@mcp.tool(
    name="dochub_preview",
    annotations={
        "title": "Открыть превью для сверки схем",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def dochub_preview(params: PreviewInput) -> str:
    """Поднять локальное превью: слева оригинал схемы, справа результат.

    Страница сама следит за YAML: когда файл меняется, правая панель
    перерисовывается. Это рабочий цикл — агент правит, человек смотрит и
    говорит, что не так. Обязательный шаг перед пушем в репозиторий.

    Сервер живёт до конца сессии. Повторный вызов с той же парой файлов
    возвращает уже открытый адрес, а не поднимает второй.

    Args:
        params (PreviewInput): параметры:
            - schema_path (str): исходная схема .drawio или .puml
            - yaml_path (str): сгенерированный DocHub YAML
            - port (int): порт, 0 — свободный автоматически

    Returns:
        str: JSON со структурой:
        {
            "url": str,          # адрес превью
            "contexts": int,     # сколько контекстов доступно в селекторе
            "reused": bool       # вернули уже запущенный сервер
        }

        При ошибке: {"error": "<описание>"}

    Examples:
        - "покажи сравнение" -> schema_path + yaml_path
        - "проверим перед коммитом" -> тот же вызов
    """
    key = f"{params.schema_path}|{params.yaml_path}"

    if key in _previews:
        server = _previews[key]
        return json.dumps({
            "url": server.url,
            "contexts": len(server.state().get('contexts') or []),
            "reused": True
        }, ensure_ascii=False, indent=2)

    try:
        server = PreviewServer(
            Path(params.schema_path),
            Path(params.yaml_path),
            port=params.port
        )
        url = server.start()
    except OSError as e:
        return json.dumps(
            {"error": f"не удалось занять порт: {e}. Попробуйте port=0"},
            ensure_ascii=False, indent=2
        )
    except PreviewError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False, indent=2)

    _previews[key] = server

    return json.dumps({
        "url": url,
        "contexts": len(server.state().get('contexts') or []),
        "reused": False
    }, ensure_ascii=False, indent=2)


@mcp.tool(
    name="dochub_ddd_tree",
    annotations={
        "title": "Дерево доменов из DDD-схемы",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def dochub_ddd_tree(params: DDDTreeInput) -> str:
    """Разобрать доменную схему компании: домены, ограниченные контексты, поддомены.

    Нужно, чтобы предложить пользователю, куда положить новую схему, и дать
    ему подтвердить выбор. Путь размещения складывается из трёх уровней:
    <домен>/<ограниченный контекст>/<поддомен>.

    Вложенность в DDD-схеме определяется геометрически: блоки нарисованы
    внутри рамок, а не связаны атрибутом parent. Часть поддоменов лежит
    прямо в домене, минуя контекст — они попадают в direct_subdomains.

    Args:
        params (DDDTreeInput): параметры:
            - ddd_path (str): путь к ddd.drawio
            - query (Optional[str]): подстрока для фильтра по id и названию

    Returns:
        str: JSON со структурой:
        {
            "domains": [
                {
                    "id": str,          # например "Sales"
                    "title": str,       # например "Привлечение клиентов"
                    "contexts": [
                        {
                            "id": str,           # например "orders"
                            "title": str,
                            "subdomains": [
                                {"id": str, "title": str,
                                 "team": str, "responsible": str}
                            ]
                        }
                    ],
                    "direct_subdomains": [...]   # поддомены вне контекстов
                }
            ],
            "unplaced_contexts": [str],  # ОК вне рамок доменов
            "totals": {"domains": int, "contexts": int, "subdomains": int}
        }

    Examples:
        - "куда положить схему сервиса заказов" -> query="order"
        - "покажи домены" -> без query
    """
    source = _need(params.ddd_path, 'ddd_path', 'путь к ddd.drawio')

    ddd_path, problem = ddd_source.local_path(source)
    if not ddd_path:
        return json.dumps({"error": problem}, ensure_ascii=False, indent=2)

    try:
        parser = DDDParser(str(ddd_path))
        domains = parser.parse()
    except Exception as e:
        return json.dumps(
            {"error": f"Не удалось разобрать DDD-схему: {type(e).__name__}: {e}"},
            ensure_ascii=False, indent=2
        )

    needle = (params.query or '').lower()

    def matches(*values: Optional[str]) -> bool:
        if not needle:
            return True
        return any(needle in str(v or '').lower() for v in values)

    def dump_subdomain(sub) -> Dict:
        return {
            "id": sub.id,
            "title": sub.title,
            "team": sub.team,
            "responsible": sub.responsible
        }

    result = []
    totals = {"domains": 0, "contexts": 0, "subdomains": 0}

    for domain in domains:
        contexts = []

        for context in domain.contexts:
            subs = [s for s in context.subdomains if matches(s.id, s.title)]
            # Контекст показываем целиком, если совпал он сам
            if matches(context.id, context.title):
                subs = context.subdomains
            if not subs and not matches(context.id, context.title):
                continue
            contexts.append({
                "id": context.id,
                "title": context.title,
                "subdomains": [dump_subdomain(s) for s in subs]
            })
            totals["subdomains"] += len(subs)

        direct = [s for s in domain.subdomains if matches(s.id, s.title)]

        if not contexts and not direct and not matches(domain.id, domain.title):
            continue

        result.append({
            "id": domain.id,
            "title": domain.title,
            "contexts": contexts,
            "direct_subdomains": [dump_subdomain(s) for s in direct]
        })
        totals["domains"] += 1
        totals["contexts"] += len(contexts)
        totals["subdomains"] += len(direct)

    unplaced = [
        parser._extract_id_and_title(cell)[0]
        for cell in parser._context_cells
        if not parser._owner_of(cell.attrib.get('id'), parser._domain_ids)
    ]

    return json.dumps({
        "domains": result,
        "unplaced_contexts": [u for u in unplaced if u],
        "totals": totals
    }, ensure_ascii=False, indent=2)


@mcp.tool(
    name="dochub_draft_from_schema",
    annotations={
        "title": "Черновик DocHub YAML из схемы",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def dochub_draft_from_schema(
    schema_path: str,
    domain: str,
    context: str,
    subdomain: str,
    repo_root: Optional[str] = None,
    yaml_path: Optional[str] = None,
    own_root: Optional[str] = None,
    page: Optional[int] = None
) -> str:
    """Построить черновик DocHub YAML из схемы — структуру, но не смысл.

    Идентификаторы выводятся из c4Name, вложенность рамок превращается в L2/L3,
    связи — в блок uml.$after. Названия остаются техническими, как на схеме.

    Это заготовка, а не готовый результат: бизнес-названия аспектов, привязку
    к доменной модели, team/prodact и разбиение на контексты-сценарии дальше
    проставляет агент, опираясь на DDD-схему, репозиторий и код сервиса.

    Args:
        schema_path (str): путь к .drawio или .puml
        domain (str): домен, например "sales"
        context (str): ограниченный контекст, например "orders"
        subdomain (str): поддомен, например "orderFlow"

    Returns:
        str: YAML-черновик с секциями aspects, components, contexts.
             В конце комментариями перечислены компоненты чужих поддоменов и
             элементы, о которых нужно спросить пользователя, — включать их
             или убрать. Инструмент не выбрасывает ничего сам.
             При ошибке — строка "Ошибка: <описание>"

    Examples:
        - "сделай заготовку по схеме" -> schema_path + domain/context/subdomain
        - Готовый YAML сразу коммитить нельзя — сначала осмыслить названия
    """
    path = Path(schema_path).expanduser()

    if not path.exists():
        return f"Ошибка: файл схемы не найден: {schema_path}"

    try:
        parser = _make_parser(path)
        result = parser.parse()
    except ValueError as e:
        return f"Ошибка: {e}"
    except Exception as e:
        return f"Ошибка разбора схемы: {type(e).__name__}: {e}"

    if not result.is_c4:
        return f"Ошибка: {result.error_message}"

    # В одном файле DrawIO обычно несколько независимых диаграмм — по сервису
    # на страницу. Слитые в один контекст, они дают схему, которой нет
    pages = result.pages or []
    if page is not None:
        result = result.for_page(page)
    elif len(pages) > 1:
        listing = "\n".join(
            f"  {index}. {name} — элементов: "
            f"{sum(1 for c in result.components if c.page == index)}"
            for index, name in enumerate(pages)
        )
        return (
            f"В файле {len(pages)} страниц, а схема строится по одной.\n\n"
            "Страницы — это разные диаграммы: слитые вместе, они дадут "
            "контекст, которого нет ни на одной из них.\n\n"
            "Спросите у пользователя, какая нужна, и повторите вызов "
            f"с page:\n\n{listing}"
        )

    owner_lookup = None
    own_file = None

    if repo_root:
        repo = Path(repo_root)
        cache: Dict[str, Optional[str]] = {}

        def owner_lookup(component_id: str) -> Optional[str]:
            """Найти файл владельца, кэшируя ответы."""
            if component_id not in cache:
                cache[component_id] = locate_owner(repo, component_id).get('file')
            return cache[component_id]

        if yaml_path:
            try:
                own_file = Path(yaml_path).resolve().relative_to(
                    repo.resolve()
                ).as_posix()
            except ValueError:
                own_file = None

    transformer = Transformer(domain, context, subdomain)

    # Схема описывает один сервис, остальные рамки — чужие поддомены.
    # Пока не сказано, какая рамка наша, черновик строить нельзя:
    # иначе чужие компоненты уедут в наш файл
    roots = sorted({
        '.'.join(cid.split('.')[:2])
        for cid in transformer.build_id_map(result.components).values()
    })

    # Ни одного пригодного элемента: спрашивать «какой из них наш» не о чем.
    # Так выглядят страницы бизнес-процессов и диаграммы уровня контекста —
    # на них люди и системы, из которых состав сервиса не построить
    if not roots:
        where = f' на странице {page}' if page is not None else ''
        return (
            f"На схеме{where} нет ни одного элемента, из которого строится "
            "состав сервиса.\n\n"
            f"Разобрано элементов: {len(result.components)}, из них рамок: "
            f"{sum(1 for c in result.components if c.is_boundary)}.\n\n"
            "Так выглядят схемы уровня контекста (люди и системы целиком) и "
            "страницы бизнес-процессов. Возьмите схему уровня контейнеров или "
            "компонентов — либо другую страницу файла."
        )

    # own_root сверяется с корнями идентификаторов, а не с подписями рамок:
    # чужое значение молча пометило бы своим чужое — и наоборот
    if not own_root or own_root not in roots:
        known = (
            "Не указано, какой сервис описывает схема (own_root)."
            if not own_root else
            f"own_root={own_root!r} не совпал ни с одним корнем схемы."
        )
        return (
            known + "\n\n"
            "Схема описывает один сервис; всё, что лежит в других рамках, "
            "принадлежит чужим поддоменам и должно заводиться у их владельцев, "
            "а у нас — только упоминаться в контексте.\n\n"
            "Спросите у пользователя, какая из рамок наша, и повторите вызов "
            "с own_root ровно в этом виде:\n\n"
            + "\n".join(f"  - {root}" for root in roots)
        )

    draft = transformer.transform(
        result,
        owner_lookup=owner_lookup,
        own_file=own_file,
        own_root=own_root
    )

    foreign = draft.pop('foreign', [])
    questionable = draft.pop('questionable', [])

    body = yaml.safe_dump(
        draft, allow_unicode=True, sort_keys=False, default_flow_style=False
    )

    # Инструмент ничего не записывает: черновик сначала осмысляют. Но дальше
    # проверка, регистрация и публикация работают с файлом, поэтому шаг
    # «сохранить» нельзя оставлять подразумеваемым
    where = f' в {yaml_path}' if yaml_path else ' в файл поддомена'
    body = (
        f"# Черновик НЕ записан на диск. Сохраните его{where} —\n"
        "# dochub_validate_context, dochub_check_registration,\n"
        "# dochub_register_schema и dochub_publish работают с файлом.\n"
        "# Строки с # в конце файла — не часть YAML, их не сохраняйте.\n"
    ) + body

    # Идентификатор из русского названия — догадка транслитерации, а имена в
    # репозитории выбирает человек. Молчать о ней нельзя: она попадёт в файл
    translit = [
        (component_id, data.get('title', ''))
        for component_id, data in (draft.get('components') or {}).items()
        if CYRILLIC.search(str(data.get('title', '')))
    ]
    if translit:
        body += (
            "\n# ИДЕНТИФИКАТОРЫ НИЖЕ ПОЛУЧЕНЫ ТРАНСЛИТЕРАЦИЕЙ русского\n"
            "# названия. Покажите их пользователю: имена в репозитории\n"
            "# выбирает он, а не инструмент.\n#\n"
        )
        for component_id, title in translit:
            body += f"#   {component_id}  <-  {_one_line(title)}\n"

    if questionable:
        body += (
            "\n# СПРОСИТЕ ПОЛЬЗОВАТЕЛЯ про элементы ниже: включать их в схему\n"
            "# или убрать. Инструмент их НЕ выбрасывает — решение зависит от\n"
            "# конкретной схемы и принимает его пользователь.\n#\n"
        )
        for item in questionable:
            body += f"#   {_one_line(item['title'])} ({item['component_id']})\n"
            for reason in item['reasons']:
                body += f"#     - {_one_line(reason)}\n"

    if not foreign:
        return body

    # Чужие компоненты не пишем в свой файл — перечисляем отдельно,
    # чтобы агент завёл их у владельцев через dochub_register_schema
    lines = [
        "# ВНИМАНИЕ: перечисленные ниже компоненты принадлежат чужим",
        "# поддоменам. В этот файл они НЕ включены — их нужно завести в",
        "# файлах владельцев, а здесь они уже используются в contexts.",
        "#"
    ]
    for item in foreign:
        # None вместо владельца читается как «владелец известен и он None»:
        # для таких компонентов владельца ищут через dochub_locate_owner
        owner = item['owner_file'] if item['owner_known'] else 'владелец не найден'
        lines.append(
            f"#   {item['component_id']}  ->  {owner}"
            f"  [{item['entity']}]  {_one_line(item['title'])}"
        )

    return body + "\n" + "\n".join(lines) + "\n"


@mcp.tool(
    name="dochub_changed_entities",
    annotations={
        "title": "Что изменилось в ветке или merge request",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def dochub_changed_entities(params: ChangedEntitiesInput) -> str:
    """Показать, что затронуто в ветке относительно базовой — по коду или по YAML.

    Работает для обоих репозиториев:

    - Репозиторий сервиса: возвращает изменённые файлы кода. По ним видно,
      какие классы, эндпоинты и хранимые процедуры правились в рамках задачи,
      и можно строить схему только по ним, а не по всему сервису.
    - Архитектурный репозиторий: дополнительно разбирает изменённые DocHub
      YAML и показывает, какие компоненты, аспекты и контексты добавлены или
      изменены. Удобно при ревью чужого merge request перед апрувом.

    Args:
        params (ChangedEntitiesInput): параметры:
            - repo_path (str): корень git-репозитория
            - base (str): базовая ревизия, обычно "main" или "origin/main"
            - head (str): сравниваемая ревизия, по умолчанию "HEAD"

    Returns:
        str: JSON со структурой:
        {
            "base": str,
            "head": str,
            "files": [str],              # все изменённые файлы
            "yaml_files": [str],         # из них DocHub YAML
            "entities": {                # только для арх.репозитория
                "components": {"added": [str], "changed": [str]},
                "aspects": {"added": [str], "changed": [str]},
                "contexts": {"added": [str], "changed": [str]}
            }
        }

        При ошибке: {"error": "<описание>"}

    Examples:
        - "что правил разработчик в задаче" -> repo_path=<репозиторий сервиса>
        - "что добавляет этот MR в архитектуру" -> repo_path=<арх.репозиторий>
    """
    repo_path = Path(params.repo_path)

    try:
        files = changed_paths(repo_path, params.base, params.head)
    except PublishError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False, indent=2)

    yaml_files = [f for f in files if f.lower().endswith(('.yaml', '.yml'))]

    entities = {
        section: {"added": [], "changed": []}
        for section in ('components', 'aspects', 'contexts')
    }

    for relative in yaml_files:
        after = _read_revision(repo_path, params.head, relative)
        before = _read_revision(repo_path, params.base, relative)

        if after is None:
            continue

        for section in entities:
            new_items = (after.get(section) or {}) if isinstance(after, dict) else {}
            old_items = (before.get(section) or {}) if isinstance(before, dict) else {}

            for key, value in new_items.items():
                if key not in old_items:
                    entities[section]["added"].append(key)
                elif old_items[key] != value:
                    entities[section]["changed"].append(key)

    return json.dumps({
        "base": params.base,
        "head": params.head,
        "files": files,
        "yaml_files": yaml_files,
        "entities": entities
    }, ensure_ascii=False, indent=2)


@mcp.tool(
    name="dochub_publish",
    annotations={
        "title": "Опубликовать схему: ветка, коммит, push, merge request",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True
    }
)
async def dochub_publish(params: PublishInput) -> str:
    """Создать ветку, закоммитить схему, отправить в GitLab и открыть merge request.

    ЕДИНСТВЕННЫЙ инструмент, который меняет состояние и выходит наружу.
    Прежде чем вызывать, обязательно: показать пользователю превью
    (dochub_preview), прогнать dochub_validate_context и получить явное
    согласие на публикацию. Не вызывать по собственной инициативе и не
    считать согласием общее «сделай схему».

    Параметр confirmed нужно ставить в true только после того, как
    пользователь прямо подтвердил публикацию в диалоге. Единственное
    исключение — включённый в настройках automode: в нём инструмент публикует
    и без подтверждения, но пишет об этом в ответе. Мерж не делается никогда.

    Сообщение коммита можно не составлять: передайте schema_name, и оно
    соберётся по шаблону из настроек (по умолчанию «Добавление схемы {schema}»).
    Ветка назначения, ответственный, ревьюверы, squash и удаление ветки после
    мержа тоже берутся из настроек, если не переданы параметрами.

    Токен GitLab берётся из переменной окружения GITLAB_TOKEN и никогда не
    передаётся параметром. Если токена нет, ветка всё равно уйдёт в
    репозиторий, а merge request нужно будет создать вручную.

    Args:
        params (PublishInput): параметры:
            - repo_path (str): корень архитектурного репозитория
            - branch (str): имя ветки, например "feature/orderServiceScheme"
            - message (Optional[str]): текст коммита; None — собрать по шаблону
            - schema_name (Optional[str]): название схемы для шаблона коммита
            - confirmed (bool): подтверждение пользователя; обязательно true,
              если не включён automode
            - files (Optional[List[str]]): что коммитить; None — все изменения
            - push (bool): отправлять ли в GitLab, по умолчанию true
            - merge_request_title (Optional[str]): заголовок MR; None — MR не создавать
            - merge_request_description (Optional[str]): описание MR
            - target_branch (Optional[str]): ветка назначения; None — из настроек
            - assignee (Optional[str]): ответственный за MR; None — из настроек
            - reviewers (Optional[List[str]]): ревьюверы; None — из настроек

    Returns:
        str: JSON со структурой:
        {
            "branch": str,
            "committed": bool,
            "pushed": bool,
            "commit_message": str,   # что ушло в коммит
            "commit_sha": str,
            "merge_request_url": str | null,
            "automode": bool,        # опубликовано без подтверждения
            "files": [str],
            "warnings": [str]        # например, отсутствие токена для MR
        }

        При ошибке: {"error": "<описание>"}

    Examples:
        - Пользователь сказал «да, публикуй» -> confirmed=true
        - Пользователь спросил «что получилось» -> это НЕ согласие, сначала превью
    """
    current = settings_module.load()

    if not params.confirmed and not current.automode:
        return json.dumps({
            "error": "Публикация не выполнена: нет подтверждения. "
                     "Покажите пользователю превью и результат проверки, "
                     "получите явное согласие, затем вызовите с confirmed=true. "
                     "Без подтверждения публикует только режим automode, "
                     "и он выключен"
        }, ensure_ascii=False, indent=2)

    if not params.message and not params.schema_name:
        return json.dumps({
            "error": "Нечего писать в коммит: передайте message или "
                     "schema_name — из него соберётся сообщение по шаблону "
                     f"{current.commit_template!r}"
        }, ensure_ascii=False, indent=2)

    message = params.message or current.commit_message(params.schema_name or '')
    automatic = not params.confirmed and current.automode

    try:
        publisher = Publisher(Path(params.repo_path))
        result = publisher.publish(
            branch=params.branch,
            message=message,
            files=params.files,
            push=params.push,
            merge_request_title=params.merge_request_title,
            merge_request_description=params.merge_request_description,
            target_branch=params.target_branch or current.target_branch,
            assignee=params.assignee or current.mr_assignee,
            reviewers=params.reviewers if params.reviewers is not None
            else current.reviewers,
            squash=current.mr_squash,
            remove_source_branch=current.mr_remove_source_branch
        )
    except PublishError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False, indent=2)

    warnings = current.warnings + result.warnings
    if automatic:
        warnings.append(
            'опубликовано без подтверждения: включён automode. '
            'Мерж всё равно за человеком'
        )

    return json.dumps({
        "branch": result.branch,
        "committed": result.committed,
        "pushed": result.pushed,
        "commit_message": message,
        "commit_sha": result.commit_sha,
        "merge_request_url": result.merge_request_url,
        "automode": automatic,
        "files": result.files,
        "warnings": warnings
    }, ensure_ascii=False, indent=2)


@mcp.tool(
    name="dochub_repo_sync",
    annotations={
        "title": "Состояние архитектурного репозитория и рабочая ветка",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def dochub_repo_sync(params: RepoSyncInput) -> str:
    """Проверить доступность репозитория, обновить его и завести рабочую ветку.

    Вызывать ПЕРЕД исследованием репозитория, а не перед публикацией. Схема,
    собранная на устаревшем состоянии, разойдётся с чужими изменениями, и
    выяснится это только при push — когда работа уже сделана.

    Инструмент ничего не сливает и не пушит: обновление делается только
    fast-forward и только когда рабочий каталог чист.

    Args:
        params (RepoSyncInput): параметры:
            - repo_root (Optional[str]): корень репозитория; из настроек
            - branch (Optional[str]): рабочая ветка — создать или выбрать
            - target_branch (Optional[str]): с чем сравнивать; из настроек
            - update (bool): подтянуть изменения из origin

    Returns:
        str: JSON со структурой:
        {
            "repo_root": str,
            "remote_url": str,
            "protocol": "https" | "ssh",
            "protocol_expected": "https" | "ssh",   # что стоит в настройках
            "reachable": bool,                      # origin ответил
            "unreachable_reason": str | null,       # VPN, прокси, права
            "fetched": bool,
            "current_branch": str,
            "target_branch": str,
            "behind": int | null,                   # на сколько отстали
            "ahead": int | null,
            "dirty_files": [str],                   # несохранённое в каталоге
            "updated": bool,
            "branch_created": str | null,
            "branch_switched": str | null,
            "warnings": [str]
        }

        При ошибке: {"error": "<описание>"}

    Examples:
        - Начало работы -> update=true, branch="feature/orderService"
        - "почему не пушится" -> без параметров, посмотреть reachable и behind
    """
    current = settings_module.load()

    try:
        publisher = Publisher(Path(_need(
            params.repo_root, 'repo_root', 'корень архитектурного репозитория')))
        report = publisher.sync(
            target_branch=params.target_branch or current.target_branch,
            branch=params.branch,
            update=params.update
        )
    except (PublishError, ValueError) as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False, indent=2)

    warnings = list(report.warnings)
    if report.protocol != current.remote_protocol:
        warnings.append(
            f'origin настроен на {report.protocol}, а в настройках '
            f'{current.remote_protocol}: при {report.protocol} нужны '
            + ('ключ SSH' if report.protocol == 'ssh' else 'токен или сохранённые учётные данные')
        )

    # Доменную схему обновляем здесь же, на подготовке. Дальше — с брифа и до
    # публикации — работа идёт по скачанной копии и в сеть не ходит
    ddd = {"source": current.ddd_path, "path": None, "updated": False}
    if current.ddd_path:
        path, problem = ddd_source.refresh(current.ddd_path)
        ddd["path"] = str(path) if path else None
        ddd["updated"] = bool(path) and problem is None
        if problem:
            warnings.append(problem)

    return json.dumps({
        "ddd": ddd,
        "repo_root": report.repo_root,
        "remote_url": report.remote_url,
        "protocol": report.protocol,
        "protocol_expected": current.remote_protocol,
        "reachable": report.reachable,
        "unreachable_reason": report.unreachable_reason,
        "fetched": report.fetched,
        "current_branch": report.current_branch,
        "target_branch": report.target_branch,
        "behind": report.behind,
        "ahead": report.ahead,
        "dirty_files": report.dirty_files,
        "updated": report.updated,
        "branch_created": report.branch_created,
        "branch_switched": report.branch_switched,
        "warnings": warnings
    }, ensure_ascii=False, indent=2)


def _brief_questions(
    facts: Dict,
    current: settings_module.Settings,
    project_root: Optional[str]
) -> List[Dict[str, str]]:
    """
    Составить вопросы, без ответов на которые схему собирать рано.

    Args:
        facts: Собранные факты о схеме, домене и репозитории
        current: Настройки — из них берутся умолчания для automode
        project_root: Каталог с кодом, если его дали

    Returns:
        Список вопросов с умолчанием на случай automode
    """
    questions: List[Dict[str, str]] = []

    # Страницы спрашиваются первыми: от ответа зависит и состав схемы, и
    # корни, из которых потом выбирается own_root.
    # Умолчания здесь нет и в automode тоже: страницы — разные задачи, и
    # какую из них заводить в репозиторий, решает только человек
    if len(facts.get('pages') or []) > 1:
        pages = facts['pages']
        questions.append({
            "id": "page",
            "question": "Какая страница файла нужна? Это разные диаграммы, "
                        "и в одну схему они не сливаются",
            "why": "страниц в файле: " + str(len(pages)) + " — "
                   + ", ".join(f"{p['index']}. {p['name']} ({p['elements']})"
                               for p in pages[:10]),
            "default": ""
        })

    if len(facts['own_root_candidates']) != 1:
        questions.append({
            "id": "own_root",
            "question": "Какой сервис описывает схема? Всё, что вне его рамки, "
                        "принадлежит чужим поддоменам",
            "why": f"корней в схеме: {len(facts['own_root_candidates'])}; "
                   f"ответ передаётся в own_root как есть",
            "default": facts['own_root_candidates'][0] if facts['own_root_candidates'] else ''
        })

    if len(facts['placement_candidates']) != 1:
        questions.append({
            "id": "placement",
            "question": "Домен, ограниченный контекст и поддомен — куда кладём схему?",
            "why": f"по доменной схеме подходит вариантов: "
                   f"{len(facts['placement_candidates'])}",
            "default": facts['placement_candidates'][0]['path']
                       if facts['placement_candidates'] else ''
        })

    code = facts.get('code') or {}
    if code.get('summary'):
        counts = code['summary']
        questions.append({
            "id": "code_mismatch",
            "question": "Код и схема расходятся — что из этого попадает в "
                        "архитектурную схему, а что нет",
            "why": "сверено файлов: " + str(code['scanned_files']) + ", "
                   + ", ".join(f'{kind}: {number}' for kind, number in counts.items()),
            # Умолчания нет: решение зависит от того, устарела схема или
            # элемент логический, и принимает его человек
            "default": ""
        })

    if not project_root:
        questions.append({
            "id": "sources",
            "question": "Сервис уже написан? Если да — где лежит код: из него "
                        "берутся точные имена классов, эндпоинты и очереди",
            "why": "код не передан, схема будет собрана строго по картинке",
            "default": "строим строго по схеме"
        })

    questions.append({
        "id": "scope",
        "question": "Вся архитектура сервиса или только то, что менялось в задаче?",
        "why": "от этого зависит, ограничивать ли схему рамками merge request",
        "default": "вся архитектура сервиса"
    })

    if facts['foreign_candidates']:
        questions.append({
            "id": "foreign",
            "question": "Схема затрагивает чужие поддомены — с кем согласуем и "
                        "можно ли править их файлы?",
            "why": f"похоже на чужое: {', '.join(facts['foreign_candidates'][:5])}",
            "default": "чужое описываем ссылками, файлы владельцев не трогаем"
        })

    # Про мерж спрашиваем, только если ответственный не задан настройками:
    # когда он есть, повторный вопрос — это то самое «объясни ещё раз»
    if not current.mr_assignee:
        questions.append({
            "id": "merge",
            "question": "Кто апрувит merge request и удалять ли ветку после мержа?",
            "why": "в настройках ответственный не задан, а MR должен сразу "
                   "уйти на нужного человека",
            "default": "ветку "
                       + ('удаляем' if current.mr_remove_source_branch
                          else 'оставляем')
                       + ", ответственного назначит автор MR"
        })

    return questions


@mcp.tool(
    name="dochub_brief",
    annotations={
        "title": "Бриф: что известно о задаче и что нужно спросить",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def dochub_brief(params: BriefInput) -> str:
    """Собрать факты по задаче и список вопросов, которые нужно закрыть до сборки.

    Вызывать ПОСЛЕ dochub_repo_sync и ДО того, как задавать вопросы
    пользователю. Инструмент сам смотрит схему, доменную модель, репозиторий и
    накопленные правила, и спрашивать остаётся только то, чего в них нет.

    Вопросы задавай одним сообщением и со ссылкой на первоисточник — поле
    "why" у каждого вопроса для этого и нужно.

    При включённом automode вопросы не задаются: у каждого есть умолчание,
    и в ответе видно, какое именно будет применено.

    Args:
        params (BriefInput): параметры:
            - schema_path (str): схема сервиса
            - query (Optional[str]): подсказка по доменной модели
            - ddd_path (Optional[str]): доменная схема; из настроек
            - manifest_path (Optional[str]): корневой dochub.yaml; из настроек
            - project_root (Optional[str]): каталог с кодом сервиса

    Returns:
        str: JSON со структурой:
        {
            "automode": bool,
            "schema": {
                "path": str, "kind": str, "physical": bool,
                "problem": str | null,       # почему схема не годится
                "elements": int, "relations": int,
                "top_level": [str]           # рамки верхнего уровня
            },
            "placement_candidates": [{"path": str, "domain": str, ...}],
            "own_root_candidates": [str],
            "known_components": [{"id": str, "title": str}],  # уже в репозитории
            "foreign_candidates": [str],
            "lessons": [str],                # применимые накопленные правила
            "questions": [{"id","question","why","default"}],
            "brief_markdown": str            # готовый текст брифа для пользователя
        }

        При ошибке: {"error": "<описание>"}

    Examples:
        - "вот схема, сделай архитектуру" -> schema_path, дальше по ответу
        - схема логическая -> в ответе physical=false и problem с объяснением
    """
    current = settings_module.load()
    schema_path = Path(params.schema_path)

    kind, problem = detect_file_kind(schema_path)
    facts: Dict = {
        "kind": kind.value if hasattr(kind, 'value') else str(kind),
        "physical": problem is None,
        "problem": problem,
        "elements": 0,
        "relations": 0,
        "top_level": [],
        "placement_candidates": [],
        "own_root_candidates": [],
        "known_components": [],
        "foreign_candidates": [],
        "code": None,
        "lessons": [],
    }

    if problem is None:
        try:
            parser = (
                DrawIOParser(str(schema_path))
                if schema_path.suffix.lower() in DRAWIO_SUFFIXES
                else PlantUMLParser(str(schema_path))
            )
            parsed = parser.parse()
        except Exception as e:  # noqa: BLE001 - причина уходит пользователю
            return json.dumps(
                {"error": f"Схему не удалось разобрать: {type(e).__name__}: {e}"},
                ensure_ascii=False, indent=2
            )

        facts["elements"] = len(parsed.components)
        facts["relations"] = len(parsed.relations)
        facts["top_level"] = [
            c.title for c in parsed.components
            if c.is_boundary and not c.parent
        ]
        # Страницы одного файла DrawIO — разные диаграммы. Если про них не
        # сказать, агент попросит черновик по всему файлу и получит отказ
        facts["pages"] = [
            {
                "index": index,
                "name": name,
                "elements": sum(1 for c in parsed.components if c.page == index)
            }
            for index, name in enumerate(parsed.pages or [])
        ]
        # Кандидаты берутся в том же виде, в каком own_root ждёт
        # dochub_draft_from_schema: это корни идентификаторов, а не подписи
        # рамок. Иначе ответ на вопрос брифа черновик не примет.
        # Порядок важен: в automode ответа ждать не от кого и берётся первый.
        # Схему называют по её сервису, поэтому сначала идут корни, созвучные
        # имени файла, а среди них — самый весомый. По одному весу первой
        # оказалась бы база данных: таблиц на схеме всегда больше
        weight: Dict[str, int] = {}
        for cid in Transformer('', '', '').build_id_map(parsed.components).values():
            root = '.'.join(cid.split('.')[:2])
            weight[root] = weight.get(root, 0) + 1

        stem = ''.join(ch for ch in schema_path.stem.lower() if ch.isalnum())

        def by_name(root: str) -> tuple:
            tail = ''.join(ch for ch in root.split('.')[-1].lower() if ch.isalnum())
            return (0 if stem and stem in tail else 1, -weight[root], root)

        facts["own_root_candidates"] = sorted(weight, key=by_name)

        # Что из схемы уже описано в репозитории — чтобы не плодить дубли
        manifest_path = settings_module.resolve('manifest_path', params.manifest_path)
        if manifest_path and Path(manifest_path).exists():
            try:
                manifest = ManifestLoader().load(Path(manifest_path))
                components = manifest.get('components') or {}
                titles = {c.title.lower() for c in parsed.components if c.title}

                for component_id, data in components.items():
                    title = str((data or {}).get('title', ''))
                    if title.lower() in titles:
                        facts["known_components"].append(
                            {"id": component_id, "title": title}
                        )
            except Exception as e:  # noqa: BLE001
                facts.setdefault("warnings", []).append(
                    f'манифест не собрался: {type(e).__name__}: {e}'
                )

        # Чужое — рамки верхнего уровня помимо своего сервиса. Вложенные
        # рамки (Controllers, Domain, Clients) — это части своего сервиса,
        # и записывать их в чужие поддомены нельзя
        # Здесь сравниваются подписи рамок, а не корни идентификаторов:
        # own_root_candidates живут в другом пространстве имён
        own = set(facts["top_level"][:1])
        facts["foreign_candidates"] = [
            title for title in facts["top_level"] if title not in own
        ][:10]

        # Дали код — сверяем с ним прямо здесь. Схему рисовали раньше, код
        # менялся: без этого бриф спросил бы «а где код», получил ответ и не
        # заглянул в него ни разу
        if params.project_root:
            try:
                report = Reconciler(Path(params.project_root)).reconcile(
                    parsed.components)
            except Exception as e:  # noqa: BLE001 - причина уходит пользователю
                facts.setdefault("warnings", []).append(
                    f'код не удалось разобрать: {type(e).__name__}: {e}')
            else:
                order = {'name_mismatch': 0, 'case_mismatch': 1,
                         'on_schema_not_in_code': 2, 'in_code_not_on_schema': 3}
                items = sorted(report.discrepancies,
                               key=lambda d: (order.get(d.kind, 9), d.name.lower()))
                facts["code"] = {
                    "project_root": str(params.project_root),
                    "scanned_files": report.scanned_files,
                    "symbols_found": report.symbols_found,
                    "matched": len(report.matched),
                    "summary": report.by_kind,
                    "discrepancies": [
                        {
                            "kind": d.kind,
                            "name": d.name,
                            "detail": d.detail,
                            "code_locations": d.code_locations[:2]
                        }
                        for d in items[:20]
                    ]
                }

    ddd_source_value = settings_module.resolve('ddd_path', params.ddd_path)
    ddd_path = None
    if ddd_source_value:
        # Бриф уже не ходит в сеть: схему обновили на подготовке
        ddd_path, problem = ddd_source.local_path(ddd_source_value)
        if problem:
            facts.setdefault("warnings", []).append(problem)

    if ddd_path and Path(ddd_path).exists():
        try:
            tree = DDDParser(str(ddd_path)).get_domain_tree()
            needle = (params.query or schema_path.stem).lower()

            for domain in tree.get('domains', []):
                for context in domain.get('contexts', []):
                    for subdomain in context.get('subdomains', []):
                        # Ищем и по названиям, и по идентификаторам: в схеме
                        # первые по-русски, а вторые латиницей, и совпасть
                        # может любое
                        haystack = ' '.join([
                            str(domain.get('title', '')), str(domain.get('id', '')),
                            str(context.get('title', '')), str(context.get('id', '')),
                            str(subdomain.get('title', '')),
                            str(subdomain.get('id', '')),
                        ]).lower()
                        if needle and needle in haystack:
                            facts["placement_candidates"].append({
                                "path": f"{domain.get('id')}.{context.get('id')}"
                                        f".{subdomain.get('id')}",
                                "domain": domain.get('title', ''),
                                "context": context.get('title', ''),
                                "subdomain": subdomain.get('title', ''),
                            })
        except Exception as e:  # noqa: BLE001
            facts.setdefault("warnings", []).append(
                f'доменная схема не разобралась: {type(e).__name__}: {e}'
            )

    try:
        facts["lessons"] = [lesson.rule for lesson in KnowledgeStore().load()][:20]
    except Exception:  # noqa: BLE001 - накопленных правил может не быть вовсе
        facts["lessons"] = []

    questions = _brief_questions(facts, current, params.project_root)

    lines = [
        f'# Бриф: {schema_path.name}',
        '',
        f'- тип схемы: {facts["kind"]}'
        + ('' if facts["physical"] else f' — не годится: {facts["problem"]}'),
        f'- элементов: {facts["elements"]}, связей: {facts["relations"]}',
        f'- рамки верхнего уровня: {", ".join(facts["top_level"]) or "нет"}',
        f'- уже есть в репозитории: {len(facts["known_components"])} компонент(ов)',
        f'- применимых накопленных правил: {len(facts["lessons"])}',
        '',
        '## Что нужно уточнить' if not current.automode
        else '## Закрыто умолчаниями (automode)',
        '',
    ]
    for item in questions:
        # Вопрос без умолчания в automode не закрывается: в репозиторий
        # заводится конкретная схема, и назвать её может только человек
        if current.automode and item["default"]:
            lines.append(f'- **{item["question"]}** → {item["default"]}')
        else:
            lines.append(f'- **{item["question"]}**  \n  _{item["why"]}_')

    return json.dumps({
        "automode": current.automode,
        "schema": {
            "path": str(schema_path),
            "kind": facts["kind"],
            "physical": facts["physical"],
            "problem": facts["problem"],
            "elements": facts["elements"],
            "relations": facts["relations"],
            "top_level": facts["top_level"],
        },
        "pages": facts.get("pages", []),
        "code": facts.get("code"),
        "placement_candidates": facts["placement_candidates"],
        "own_root_candidates": facts["own_root_candidates"],
        "known_components": facts["known_components"][:50],
        "foreign_candidates": facts["foreign_candidates"],
        "lessons": facts["lessons"],
        "questions": questions,
        "warnings": facts.get("warnings", []),
        "brief_markdown": '\n'.join(lines),
    }, ensure_ascii=False, indent=2)


def main() -> None:
    """Запустить сервер по stdio."""
    # stdout занят протоколом MCP — диагностику пишем в stderr
    print("dochub_mcp: запуск", file=sys.stderr)
    mcp.run()


if __name__ == "__main__":
    main()

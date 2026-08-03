"""
Рендеринг схем для превью.

Все три формата обрабатываются локально, без обращения к сети:
  - DrawIO  -> исходный XML отдаётся встроенному viewer'у drawio (vendor/)
  - PlantUML -> SVG через lib/plantuml.jar
  - DocHub YAML -> PlantUML C4 -> SVG через lib/plantuml.jar
"""

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from app.core.dochub import DocHubNativeRenderer, ManifestLoader
from app.core.dochub.native_renderer import DocHubRenderError
from app.utils.logger import get_logger
from app.utils.paths import PLANTUML_JAR, elk_jars, java_command

DRAWIO_SUFFIXES = ('.drawio', '.xml')
PLANTUML_SUFFIXES = ('.puml', '.plantuml', '.pu')
YAML_SUFFIXES = ('.yaml', '.yml')


class PreviewError(Exception):
    """Ошибка подготовки превью."""


@dataclass
class RenderedDiagram:
    """Отрендеренная диаграмма и замечания к ней."""
    svg: str
    warnings: List[str] = field(default_factory=list)


# Строки, подключающие внешние ресурсы: они дают оформление, а не структуру.
# Встроенная библиотека вида "!include <C4/C4_Component>" и подключение
# соседнего файла внешними не считаются — они работают офлайн.
_EXTERNAL_DIRECTIVES = (
    re.compile(r'^!include(sub|url)?\s+https?://', re.I),
    re.compile(r'^!include(sub|url)?\s+\$\w+', re.I),
    re.compile(r'^!include(sub|url)?\s+[A-Z][A-Z0-9_]*/', ),
    re.compile(r'^!global\s+\$\w*(PATH|URL)\w*\s*=', re.I),
    re.compile(r'^!define\s+\w+\s+https?://', re.I),
)


def _stub_external_includes(source: str, base_dir: Optional[Path] = None) -> tuple:
    """
    Заменить внешние подключения локальными файлами, а недоступные отключить.

    Схемы часто собираются из фрагментов .iuml, лежащих рядом, но подключаются
    по URL через переменную вроде $BASE_PATH. Если такой файл есть локально,
    подменяем путь: иначе теряются определения элементов, на которые потом
    ссылаются note и связи.

    Args:
        source: Исходный код PlantUML
        base_dir: Каталог схемы, где ищутся локальные фрагменты

    Returns:
        Кортеж (изменённый код, сколько строк отключено)
    """
    lines = []
    stubbed = 0
    replaced = 0

    for line in source.splitlines():
        stripped = line.strip()

        if any(pattern.match(stripped) for pattern in _EXTERNAL_DIRECTIVES):
            local = _local_replacement(stripped, base_dir)
            if local:
                lines.append(local)
                replaced += 1
                continue
            lines.append(f"' [отключено при офлайн-рендере] {line}")
            stubbed += 1
            continue

        lines.append(line)

    # Замены тоже считаются изменением: иначе они потеряются
    if not stubbed and not replaced:
        return source, 0

    body = '\n'.join(lines)

    # Макросы C4 приходили из отключённого include — берём их из stdlib.
    # Заодно объявляем переменные, которые задавались в отключённых файлах:
    # без них PlantUML падает с Syntax Error на первом же обращении.
    # C4_Component подключает Container и Context, поэтому даёт полный набор
    # макросов: Component() в C4_Container не определён
    preamble = ['!include <C4/C4_Component>']
    preamble += [f'!$*{name} = ""'.replace('*', '') for name in _undefined_vars(body)]

    body = re.sub(
        r'(@startuml[^\n]*\n)',
        lambda m: m.group(1) + '\n'.join(preamble) + '\n',
        body,
        count=1
    )

    return body, stubbed


def _local_replacement(directive: str, base_dir: Optional[Path]) -> Optional[str]:
    """
    Подобрать локальный файл вместо внешнего подключения.

    Args:
        directive: Строка вида "!include $BASE_PATH/.../foo.iuml"
        base_dir: Каталог, где искать

    Returns:
        Изменённая строка или None, если локального файла нет
    """
    if not base_dir:
        return None

    match = re.match(r'^!include(sub|url)?\s+(.+?)\s*$', directive, re.I)
    if not match:
        return None

    target = match.group(2).strip('"\'')

    # Подключение по имени: берём последний сегмент пути
    name = target.replace('\\', '/').split('/')[-1]
    if not name or '.' not in name:
        return None

    candidate = Path(base_dir) / name
    if not candidate.is_file():
        return None

    return f'!include {name}'


def _undefined_vars(body: str) -> List[str]:
    """
    Найти переменные, которые используются, но нигде не заданы.

    Args:
        body: Код PlantUML с отключёнными подключениями

    Returns:
        Имена переменных без определения
    """
    used = set(re.findall(r'\$([A-Za-z_]\w*)', body))

    defined = set(re.findall(r'^\s*!\s*(?:global\s+)?\$(\w+)\s*=', body, re.M))
    defined |= set(re.findall(r'^\s*!define\s+\$?(\w+)', body, re.M))
    # Имена процедур и функций тоже пишутся через доллар
    defined |= set(re.findall(r'^\s*!\s*(?:unquoted\s+)?(?:procedure|function)\s+\$?(\w+)',
                              body, re.M))

    # Макросы C4 из stdlib объявлены вне этого файла
    known = {'sprite', 'tags', 'link', 'legend'}

    return sorted(v for v in used - defined - known if v.isupper() or '_' in v)


class PreviewRenderer:
    """Готовит содержимое панелей превью."""

    def __init__(
        self,
        plantuml_jar: Optional[Path] = None,
        repo_root: Optional[Path] = None
    ):
        """
        Args:
            plantuml_jar: Путь к plantuml.jar (по умолчанию lib/plantuml.jar)
            repo_root: Корень арх.репозитория — по нему собирается манифест,
                       без которого не разрешаются ссылки на чужие компоненты
        """
        self.logger = get_logger()
        self.plantuml_jar = Path(plantuml_jar or PLANTUML_JAR)
        self.dochub_renderer = DocHubNativeRenderer()
        self.repo_root = Path(repo_root) if repo_root else None

        self._manifest: Optional[Dict] = None
        self._manifest_key: Optional[tuple] = None
        self._elk_available: Optional[bool] = None

    def _java_command(self, base_dir: Optional[Path] = None) -> List[str]:
        """
        Собрать команду запуска PlantUML.

        Если рядом лежат jar'ы ELK из плагина DocHub, подключаем их через
        classpath — тогда работает раскладка, которую DocHub просит по умолчанию.

        Args:
            base_dir: Каталог поиска подключаемых файлов

        Returns:
            Аргументы для subprocess
        """
        render_args = ['-tsvg', '-pipe', '-charset', 'UTF-8']

        # Свойства JVM задаются до -jar/-cp: после они уйдут в аргументы
        # PlantUML и будут проигнорированы. Без пути поиска !include
        # соседнего .iuml не находится — при работе через -pipe у PlantUML
        # нет каталога исходного файла.
        jvm_args = [f'-Dplantuml.include.path={base_dir}'] if base_dir else []

        jars = elk_jars()

        java = java_command()

        if not jars:
            return [java, *jvm_args, '-jar', str(self.plantuml_jar), *render_args]

        classpath = os.pathsep.join(
            [str(self.plantuml_jar)] + [str(jar) for jar in jars]
        )
        return [
            java, *jvm_args, '-cp', classpath,
            'net.sourceforge.plantuml.Run', *render_args
        ]

    def elk_available(self) -> bool:
        """
        Доступна ли раскладка ELK.

        DocHub по умолчанию просит renderCore: elk, но для неё нужен
        отдельный jar. Проверяем один раз пробным рендером.

        Returns:
            True если ELK работает
        """
        if self._elk_available is not None:
            return self._elk_available

        probe = '@startuml\n!pragma layout elk\ncomponent a\n@enduml\n'
        try:
            svg = self.render_plantuml(probe)
            self._elk_available = 'ClassNotFoundException' not in svg
        except PreviewError:
            self._elk_available = False

        if not self._elk_available:
            self.logger.info(
                "ELK недоступен (нужен отдельный jar) — раскладка через smetana"
            )

        return self._elk_available

    def _load_manifest(self, yaml_path: Path) -> Dict:
        """
        Собрать манифест репозитория.

        Правки в самом YAML должны попадать в превью, поэтому манифест
        пересобирается, когда файл меняется.

        Args:
            yaml_path: Редактируемый файл контекста

        Returns:
            Манифест репозитория
        """
        root = self._find_repo_root(yaml_path)

        if root is None:
            # Репозитория рядом нет — работаем по одному файлу.
            # Чужие компоненты в этом режиме не разрешатся.
            return yaml.safe_load(yaml_path.read_text(encoding='utf-8')) or {}

        try:
            key = (root, yaml_path.stat().st_mtime)
        except OSError:
            key = (root, None)

        if key != self._manifest_key:
            self._manifest = ManifestLoader().load(root)
            self._manifest_key = key

        # Схему показывают до того, как она подключена к дереву репозитория:
        # в манифесте её контекста ещё нет, и правая панель отвечала бы
        # «контекст не найден» весь этап показа и правок по замечаниям
        own = yaml.safe_load(yaml_path.read_text(encoding='utf-8')) or {}
        merged = dict(self._manifest or {})
        for section, values in own.items():
            current = merged.get(section)
            if isinstance(values, dict) and isinstance(current, dict):
                merged[section] = {**current, **values}
            else:
                merged[section] = values

        return merged

    def _find_repo_root(self, yaml_path: Path) -> Optional[Path]:
        """
        Найти корневой dochub.yaml арх.репозитория.

        Args:
            yaml_path: Файл контекста

        Returns:
            Путь к architecture/dochub.yaml или None
        """
        if self.repo_root:
            candidate = Path(self.repo_root)
            return candidate if candidate.is_file() else candidate / 'dochub.yaml'

        for parent in yaml_path.resolve().parents:
            candidate = parent / 'dochub.yaml'
            if candidate.exists():
                return candidate

        return None

    def render_plantuml(self, source: str, base_dir: Optional[Path] = None) -> str:
        """
        Отрендерить PlantUML в SVG.

        Если схема подключает файлы с недоступного сервера, рендер повторяется
        без них: внешние !include отвечают за оформление и иконки, а структура
        диаграммы лежит в самом файле. Лучше показать схему без фирменных
        стилей, чем не показать ничего.

        Args:
            source: Код PlantUML
            base_dir: Каталог для поиска !include — схемы часто подключают
                      соседние .iuml относительным путём

        Returns:
            SVG-разметка

        Raises:
            PreviewError: если plantuml.jar недоступен или рендеринг упал
        """
        return self.render_plantuml_verbose(source, base_dir).svg

    def render_plantuml_verbose(
        self,
        source: str,
        base_dir: Optional[Path] = None
    ) -> 'RenderedDiagram':
        """
        Отрендерить PlantUML, сообщив об упрощениях.

        Args:
            source: Код PlantUML
            base_dir: Каталог для поиска !include

        Returns:
            SVG и список предупреждений

        Raises:
            PreviewError: если не удалось отрендерить даже без внешних файлов
        """
        if not self.plantuml_jar.exists():
            raise PreviewError(f"Не найден plantuml.jar: {self.plantuml_jar}")

        error = self._try_render(source, base_dir)

        if isinstance(error, RenderedDiagram):
            return error

        # Полный рендер не удался — пробуем без внешних подключений
        offline, stubbed = _stub_external_includes(source, base_dir)

        # Подключения могли не глушиться, а замениться локальными файлами —
        # тогда stubbed нулевой, но попытка всё равно имеет смысл
        if offline == source:
            raise PreviewError(self._explain_plantuml_error(error, source))

        retry = self._try_render(offline, base_dir)

        if not isinstance(retry, RenderedDiagram):
            # Подставленный локальный фрагмент может сам тянуть наружу —
            # последняя попытка без подключений вообще
            bare, bare_stubbed = _stub_external_includes(source, base_dir=None)
            retry = self._try_render(bare, base_dir)
            stubbed = max(stubbed, bare_stubbed)

        if not isinstance(retry, RenderedDiagram):
            raise PreviewError(self._explain_plantuml_error(error, source))

        hosts = sorted({
            re.sub(r'^(https?://[^/]+).*', r'\1', url)
            for url in re.findall(r'https?://[^\s"\']+', source)
        })
        retry.warnings.append(
            f"Схема отрисована без внешних подключений ({stubbed} шт.): "
            f"{', '.join(hosts[:3]) or 'внешний сервер'} недоступен. "
            "Структура верна, но фирменные стили и иконки не применены."
        )
        return retry

    def _try_render(
        self,
        source: str,
        base_dir: Optional[Path]
    ):
        """
        Попытаться отрендерить.

        Args:
            source: Код PlantUML
            base_dir: Каталог поиска !include

        Returns:
            RenderedDiagram при успехе, иначе текст ошибки
        """
        try:
            result = subprocess.run(
                self._java_command(base_dir),
                input=source.encode('utf-8'),
                capture_output=True,
                timeout=120
            )
        except FileNotFoundError:
            raise PreviewError("Не найдена Java — она нужна для рендеринга PlantUML")
        except subprocess.TimeoutExpired:
            return "PlantUML не ответил за 120 секунд"

        if result.returncode != 0:
            return result.stderr.decode('utf-8', errors='replace').strip()

        svg = result.stdout.decode('utf-8', errors='replace')

        # PlantUML умеет вернуть код 0 и картинку с текстом ошибки
        if 'An error has occured' in svg or 'cannot include' in svg:
            return svg[:400]

        return RenderedDiagram(svg=svg, warnings=[])

    @staticmethod
    def _explain_plantuml_error(error: str, source: str) -> str:
        """
        Превратить сообщение PlantUML в понятное объяснение.

        Args:
            error: Текст из stderr
            source: Исходный код схемы

        Returns:
            Объяснение с указанием, что делать
        """
        # setup_assets.py качает последний PlantUML, а он требует Java 11+.
        # На машине со старой Java ошибка приходит от JVM и о PlantUML в ней
        # не сказано ни слова
        if 'UnsupportedClassVersionError' in error:
            return (
                "PlantUML собран под более новую Java, чем установлена.\n\n"
                "Последние выпуски PlantUML требуют Java 11 или новее. "
                "Поставьте её (java -version покажет текущую) либо положите "
                "в lib/plantuml.jar выпуск под свою Java."
            )

        # Схемы компаний часто подключают настройки со своего сервера —
        # без доступа к нему PlantUML падает по таймауту
        remote = re.findall(r'!include[a-z]*\s+.*?(https?://[^\s"\']+)', source)
        looks_like_network = (
            'java.util.concurrent' in error
            or 'UnknownHost' in error
            or 'Connection' in error
            or 'issue ' in error
        )

        if remote and looks_like_network:
            hosts = sorted({re.sub(r'^(https?://[^/]+).*', r'\1', u) for u in remote})
            return (
                "Схема подключает файлы с внешнего сервера, который сейчас "
                f"недоступен: {', '.join(hosts)}.\n\n"
                "Так бывает, когда схема лежит вне корпоративной сети. "
                "Варианты: открыть доступ к серверу, либо положить "
                "подключаемые .iuml рядом со схемой и заменить в !include "
                "адрес на относительный путь."
            )

        return f"PlantUML вернул ошибку: {error or 'без описания'}"

    def describe_source(self, path: Path) -> Dict:
        """
        Определить, как показывать исходную схему.

        Args:
            path: Путь к исходной схеме

        Returns:
            Словарь с полями kind ('drawio' | 'svg') и data
        """
        suffix = path.suffix.lower()
        source = path.read_text(encoding='utf-8')

        if suffix in DRAWIO_SUFFIXES:
            # Отдаём XML как есть — рисует встроенный движок drawio,
            # поэтому пользователь видит оригинал, а не реконструкцию
            return {
                'kind': 'drawio',
                'data': source,
                'pages': self.list_pages(path)
            }

        if suffix in PLANTUML_SUFFIXES:
            rendered = self.render_plantuml_verbose(source, path.parent)
            return {
                'kind': 'svg',
                'data': rendered.svg,
                'warnings': rendered.warnings
            }

        raise PreviewError(f"Неподдерживаемый формат исходной схемы: {suffix}")

    @staticmethod
    def list_pages(path: Path) -> List[str]:
        """
        Перечислить страницы файла DrawIO.

        В одном файле часто лежит несколько независимых диаграмм, и без
        выбора страницы видна только первая.

        Args:
            path: Путь к схеме

        Returns:
            Названия страниц по порядку
        """
        import xml.etree.ElementTree as ET

        try:
            root = ET.parse(path).getroot()
        except (ET.ParseError, OSError):
            return []

        return [
            diagram.attrib.get('name') or f'Страница {index + 1}'
            for index, diagram in enumerate(root.iter('diagram'))
        ]

    def list_contexts(self, yaml_path: Path) -> List[Dict[str, str]]:
        """
        Перечислить контексты DocHub YAML.

        Args:
            yaml_path: Путь к DocHub YAML

        Returns:
            Список словарей с id и title
        """
        # Показываем только контексты редактируемого файла, а не весь
        # репозиторий — иначе в списке окажутся сотни чужих
        own = yaml.safe_load(yaml_path.read_text(encoding='utf-8')) or {}
        return [
            {'id': ctx_id, 'title': (ctx or {}).get('title', ctx_id)}
            for ctx_id, ctx in (own.get('contexts') or {}).items()
        ]

    def context_plantuml(
        self,
        yaml_path: Path,
        context_id: Optional[str] = None
    ) -> str:
        """
        Собрать исходник PlantUML для контекста.

        Нужен, чтобы выгрузить диаграмму в текстовом виде: такой файл можно
        положить рядом со схемой или отрендерить чем-то другим.

        Args:
            yaml_path: Путь к DocHub YAML
            context_id: Идентификатор контекста

        Returns:
            Код PlantUML
        """
        manifest = self._load_manifest(yaml_path)

        if context_id is None:
            contexts = self.list_contexts(yaml_path)
            if not contexts:
                raise PreviewError("В файле нет ни одного контекста")
            context_id = contexts[0]['id']

        try:
            return self.dochub_renderer.render_context(
                manifest,
                context_id,
                render_core=None if self.elk_available() else 'smetana'
            )
        except DocHubRenderError as e:
            raise PreviewError(str(e))

    def render_context(self, yaml_path: Path, context_id: Optional[str] = None) -> str:
        """
        Отрендерить контекст DocHub YAML в SVG.

        Args:
            yaml_path: Путь к DocHub YAML
            context_id: Идентификатор контекста (по умолчанию — первый)

        Returns:
            SVG-разметка
        """
        manifest = self._load_manifest(yaml_path)

        if context_id is None:
            contexts = self.list_contexts(yaml_path)
            if not contexts:
                raise PreviewError("В файле нет ни одного контекста")
            context_id = contexts[0]['id']

        try:
            plantuml_code = self.dochub_renderer.render_context(
                manifest,
                context_id,
                render_core=None if self.elk_available() else 'smetana'
            )
        except DocHubRenderError as e:
            raise PreviewError(str(e))

        return self.render_plantuml(plantuml_code)

"""
Рендеринг контекстов родным конвейером DocHub.

Метамодель извлечена из плагина DocHub (vendor/dochub) и исполняется как есть,
поэтому диаграмма получается такой же, как в самом DocHub: вложенные рамки
областей, аспекты списком внутри блоков, ссылки на компоненты.

Конвейер повторяет presentations.plantuml.source из plantuml.yaml:
    fetchComponents -> fetchAreas -> makePumlComponentDiagram
                    -> fetchLinks -> makePumlComponentsLinks
                    -> template.puml
"""

import re
from pathlib import Path
from typing import Dict, List, Optional

import jsonata
import yaml

from app.core.dochub.manifest import merge_deep
from app.utils.logger import get_logger
from app.utils.paths import DOCHUB_METAMODEL_DIR

# Движки раскладки, как в plantuml.yaml
RENDER_CORES = {
    'elk': '!pragma layout elk',
    'smetana': '!pragma layout smetana'
}


class DocHubRenderError(Exception):
    """Ошибка рендеринга контекста."""


class DocHubNativeRenderer:
    """Генерирует PlantUML контекста конвейером самого DocHub."""

    def __init__(self, vendor_dir: Optional[Path] = None):
        """
        Args:
            vendor_dir: Каталог с метамоделью DocHub
        """
        self.logger = get_logger()
        self.vendor_dir = Path(vendor_dir or DOCHUB_METAMODEL_DIR)

        base = self._load_metamodel('base.yaml')
        plantuml = self._load_metamodel('plantuml.yaml')

        contexts = base.get('entities', {}).get('contexts', {})
        merge_deep(contexts, plantuml.get('entities', {}).get('contexts', {}))

        self.api: Dict[str, str] = contexts.get('api', {})
        self.config: Dict = contexts.get('config', {})
        self.template = (self.vendor_dir / 'template.puml').read_text(encoding='utf-8')

        missing = {'fetchComponents', 'fetchAreas', 'fetchLinks',
                   'makePumlComponentDiagram', 'makePumlComponentsLinks'} - set(self.api)
        if missing:
            raise DocHubRenderError(f"В метамодели нет функций: {sorted(missing)}")

    def _load_metamodel(self, name: str) -> Dict:
        """Прочитать файл метамодели."""
        path = self.vendor_dir / name
        if not path.exists():
            raise DocHubRenderError(
                f"Не найдена метамодель DocHub: {path}. "
                "Она извлекается из плагина DocHub в vendor/dochub."
            )
        return yaml.safe_load(path.read_text(encoding='utf-8')) or {}

    def _evaluate(self, expression: str, data: Dict):
        """
        Исполнить JSONata-выражение метамодели.

        Args:
            expression: Текст выражения
            data: Входные данные

        Returns:
            Результат вычисления
        """
        expr = jsonata.Jsonata(expression)
        _register_custom_functions(expr)
        return expr.evaluate(data)

    def render_context(
        self,
        manifest: Dict,
        context_id: str,
        focus_id: Optional[str] = None,
        render_core: Optional[str] = None
    ) -> str:
        """
        Построить PlantUML для контекста.

        Args:
            manifest: Полный манифест репозитория
            context_id: Идентификатор контекста
            focus_id: Компонент, который нужно подсветить
            render_core: Принудительный движок раскладки (elk / smetana),
                         нужен когда jar с ELK недоступен

        Returns:
            Код PlantUML
        """
        contexts = manifest.get('contexts') or {}
        context = contexts.get(context_id)

        if context is None:
            raise DocHubRenderError(f"Контекст не найден: {context_id}")

        # Контекст может ссылаться на готовый puml-файл — тогда генерировать нечего
        uml = context.get('uml')
        if isinstance(uml, str) and uml.endswith('.puml'):
            raise DocHubRenderError(
                f"Контекст {context_id} ссылается на внешний файл {uml}"
            )

        extra_links = str(context.get('extra-links', True)).lower() != 'false'

        components = self._evaluate(self.api['fetchComponents'], {
            'manifest': manifest,
            'contextId': context_id,
            'extra-links': extra_links,
            'componentId': None
        }) or {}

        areas = self._evaluate(self.api['fetchAreas'], {
            'components': components
        }) or []

        elements = self._evaluate(self.api['makePumlComponentDiagram'], {
            'manifest': manifest,
            'areas': areas,
            'components': components,
            'focusId': focus_id
        }) or ''

        links = self._evaluate(self.api['fetchLinks'], {
            'components': components
        }) or []

        links_code = self._evaluate(self.api['makePumlComponentsLinks'], {
            'links': links
        }) or ''

        code = self._make_header(context, context_id) + elements + links_code

        # Блок uml из контекста DocHub подставляет вокруг сгенерированного кода
        if isinstance(uml, dict):
            before = str(uml.get('$before') or '').rstrip()
            after = str(uml.get('$after') or '').rstrip()
            code = '\n'.join(part for part in (before, code, after) if part)

        return self._fill_template(context, code, render_core)

    @staticmethod
    def _make_header(context: Dict, context_id: str) -> str:
        """Сформировать вызов $Header, как в метамодели."""
        uml = context.get('uml') if isinstance(context.get('uml'), dict) else {}
        title = context.get('title') or context_id
        return (
            f'$Header("{title}", "{uml.get("$autor", "")}", '
            f'"{uml.get("$version", "")}", "{uml.get("$moment", "")}")\n'
        )

    def _fill_template(
        self,
        context: Dict,
        code: str,
        render_core: Optional[str] = None
    ) -> str:
        """
        Подставить сгенерированный код в шаблон DocHub.

        Args:
            context: Контекст (может переопределять config.renderCore)
            code: Сгенерированный код диаграммы
            render_core: Принудительный движок раскладки

        Returns:
            Готовый PlantUML
        """
        config = dict(self.config)
        merge_deep(config, context.get('config') or {})

        core = render_core or config.get('renderCore')

        substitutions = {
            'renderCore': RENDER_CORES.get(core, ''),
            'presentation': config.get('defaultPresentation', 'plantuml'),
            '&code': code,
            'code': code
        }

        def replace(match: re.Match) -> str:
            return substitutions.get(match.group(1).strip(), '')

        return re.sub(r'\{\{([^}]+)\}\}', replace, self.template)

    def list_contexts(self, manifest: Dict) -> List[Dict[str, str]]:
        """
        Перечислить контексты манифеста.

        Args:
            manifest: Манифест репозитория

        Returns:
            Список словарей с id и title
        """
        return [
            {'id': ctx_id, 'title': (ctx or {}).get('title', ctx_id)}
            for ctx_id, ctx in (manifest.get('contexts') or {}).items()
        ]


def _register_custom_functions(expr: 'jsonata.Jsonata') -> None:
    """
    Зарегистрировать функции, которые DocHub добавляет к JSONata.

    Args:
        expr: Скомпилированное выражение
    """
    expr.register_lambda('wcard', _wcard)
    expr.register_lambda('mergedeep', _mergedeep)


def _wcard(value: Optional[str], mask: Optional[str]) -> bool:
    """
    Сопоставить идентификатор с маской.

    В контексте компоненты перечисляются как точными id, так и масками
    вида "dotnet.myService.*".

    Args:
        value: Проверяемый идентификатор
        mask: Маска

    Returns:
        True если идентификатор подходит под маску
    """
    if value is None or mask is None:
        return False
    if '*' not in mask and '?' not in mask:
        return value == mask

    pattern = '^' + ''.join(
        '.*' if ch == '*' else '.' if ch == '?' else re.escape(ch)
        for ch in mask
    ) + '$'
    return re.match(pattern, value) is not None


def _mergedeep(items: Optional[List]) -> Dict:
    """
    Слить список словарей вглубь.

    Args:
        items: Список словарей

    Returns:
        Результат слияния
    """
    result: Dict = {}
    for item in items or []:
        if isinstance(item, dict):
            merge_deep(result, item)
    return result

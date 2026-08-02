"""
Трансформер для преобразования данных схем в DocHub YAML формат.

Использует правила трансформации для генерации aspects, components, contexts.
"""

import re
from collections import Counter
from typing import Callable, Dict, List, Optional
from pathlib import Path

from app.core.parsers.base_parser import Component, Relation, ParseResult
from app.utils.logger import get_logger


def slugify(name: Optional[str]) -> str:
    """
    Привести название с диаграммы к camelCase-идентификатору DocHub.

    "OrderHandlerService"      -> "orderHandlerService"
    "Partner API Endpoints"    -> "partnerApiEndpoints"
    "dbo.Partner_Edit_Get"     -> "dboPartnerEditGet"

    Args:
        name: Название узла (c4Name)

    Returns:
        Идентификатор или пустая строка
    """
    if not name:
        return ''

    words = [w for w in re.split(r'[^0-9A-Za-z]+', name) if w]
    if not words:
        return ''

    # Аббревиатуры приводим к обычному виду: в репозитории принято
    # "catalogApi", а не "catalogAPI"
    words = [w.capitalize() if w.isupper() and len(w) > 1 else w for w in words]

    head, *tail = words

    # Идентификатор всегда начинается со строчной: PascalCase в id запрещён
    head = head[:1].lower() + head[1:]

    return head + ''.join(w[:1].upper() + w[1:] for w in tail)


class Transformer:
    """Трансформер данных схем."""

    def __init__(self, domain: str, context: str, subdomain: str):
        """
        Инициализация трансформера.

        Args:
            domain: Домен (например, "sales")
            context: Ограниченный контекст (например, "orders")
            subdomain: Поддомен (например, "orderFlow")
        """
        self.domain = domain
        self.context = context
        self.subdomain = subdomain
        self.logger = get_logger()

        # Полный путь домена
        self.full_domain_path = f"{domain}.{context}.{subdomain}"

        # Заполняется в transform(): id схемы -> id DocHub
        self.id_map: Dict[str, str] = {}

    def transform(
        self,
        parse_result: ParseResult,
        reused_components: Optional[List[Dict]] = None,
        owner_lookup: Optional[Callable[[str], Optional[str]]] = None,
        own_file: Optional[str] = None,
        own_root: Optional[str] = None
    ) -> Dict:
        """
        Трансформировать результат парсинга в DocHub формат.

        Args:
            parse_result: Результат парсинга схемы
            reused_components: Список переиспользуемых компонентов
            owner_lookup: Функция id -> файл владельца. Компоненты чужих
                          поддоменов не попадают в наш файл: описывать их
                          нужно у владельца, а у себя только ссылаться
            own_file: Наш файл поддомена относительно корня репозитория
            own_root: Идентификатор нашего сервиса, например
                      "dotnet.orderService". Схема описывает один
                      сервис, поэтому всё, что лежит в других рамках верхнего
                      уровня, принадлежит чужим поддоменам

        Returns:
            Словарь с aspects, components, contexts и foreign — списком
            компонентов, которые надо завести в файлах владельцев
        """
        if not parse_result.is_c4:
            self.logger.error("Cannot transform non-C4 schema")
            return {}

        if reused_components is None:
            reused_components = []

        # Генерируем структуру.
        # foreign — компоненты чужих поддоменов: в наш файл они не пишутся,
        # их заводят у владельца, а мы только ссылаемся из контекста
        result = {
            'aspects': {},
            'components': {},
            'contexts': {},
            'foreign': [],
            # Кандидаты на исключение: решение принимает пользователь,
            # движок только показывает основание
            'questionable': []
        }

        # Идентификаторы строим по c4Name с учётом вложенности рамок
        self.id_map = self.build_id_map(parse_result.components)

        # Обрабатываем компоненты
        new_component_ids = []

        for component in parse_result.components:
            # Рамки сами по себе на диаграмму не идут — они дают L2
            if component.is_boundary:
                continue

            # Проверяем что компонент не переиспользуется
            if self._is_reused(component.id, reused_components):
                # Добавляем только в контекст
                reused_id = self._get_reused_id(component.id, reused_components)
                new_component_ids.append(reused_id)
                self.id_map[component.id] = reused_id
                self.logger.debug(f"Reusing component: {reused_id}")
                continue

            component_id = self.id_map.get(component.id)
            if not component_id:
                continue

            # Компонент чужого поддомена описывается у владельца.
            # У себя оставляем только ссылку в контексте.
            owner_file = owner_lookup(component_id) if owner_lookup else None
            root = '.'.join(component_id.split('.')[:2])

            # Схема описывает один сервис: всё вне его рамки — чужое, даже
            # если id не совпал с репозиторием (имена на схеме и в репозитории
            # обычно различаются, поэтому одного поиска по id мало)
            outside_own_root = bool(own_root) and root != own_root
            known_foreign = bool(owner_file) and owner_file != own_file

            if outside_own_root or known_foreign:
                result['foreign'].append({
                    'component_id': component_id,
                    'owner_file': owner_file,
                    'owner_known': bool(owner_file),
                    'title': component.title,
                    'entity': self._map_entity_type(component.type, component),
                    'description': component.description
                })
                new_component_ids.append(component_id)
                continue

            # Создаем новый компонент
            comp_data = self._transform_component(component, component_id)
            result['aspects'].update(comp_data['aspects'])
            result['components'].update(comp_data['components'])
            new_component_ids.append(comp_data['component_id'])

        # Родительские L2 обязательны: без них L3 висят в воздухе
        # Родителей достраиваем только для своих компонентов: L2 чужого
        # сервиса уже есть у владельца, дублировать его нельзя
        result['components'].update(
            self._build_parent_components(list(result['components']))
        )

        # Спрашиваем только про свои компоненты: чужие уходят к владельцу,
        # и вопрос «включать или нет» к ним не относится
        result['questionable'] = self._find_questionable(
            parse_result, set(result['components'])
        )

        # Добавляем переиспользуемые компоненты в контекст
        for reused in reused_components:
            if reused['id'] not in new_component_ids:
                new_component_ids.append(reused['id'])

        # Создаем контекст
        result['contexts'][self.full_domain_path] = self._create_context(
            new_component_ids,
            parse_result.relations
        )

        self.logger.info(
            f"Transformed: {len(result['components'])} new components, "
            f"{len(reused_components)} reused"
        )

        return result

    def build_id_map(self, components: List[Component]) -> Dict[str, str]:
        """
        Сопоставить внутренние идентификаторы схемы с идентификаторами DocHub.

        DrawIO хранит id вида "XsR4LPghTYNSFqmyrRTn-2" — на диаграмме такие
        бесполезны. Осмысленный id строится из c4Name с учётом вложенности:
        рамка сервиса даёт L2, элемент внутри неё — L3.

        Args:
            components: Компоненты схемы (с заполненным parent)

        Returns:
            Словарь {id из схемы: id для DocHub}
        """
        by_id = {comp.id: comp for comp in components}
        owners = {
            comp.id: self._find_owner(comp, by_id)
            for comp in components if not comp.is_boundary
        }

        # Технологию определяем один раз на рамку: иначе дети с разной
        # технологией растащат один сервис на несколько L2
        votes: Dict[str, Counter] = {}
        for component in components:
            owner = owners.get(component.id)
            if owner is None:
                continue
            technology = self._extract_technology(component.technology)
            if technology != 'component':
                votes.setdefault(owner.id, Counter())[technology] += 1

        owner_tech: Dict[str, str] = {}
        for owner in components:
            if not owner.is_boundary:
                continue

            # Имя рамки — сигнал надёжнее: в шаблонах DrawIO попадаются
            # элементы с технологией-заглушкой вроде "e.g. Spring Service"
            by_title = self._extract_technology(owner.title)
            if by_title != 'component':
                owner_tech[owner.id] = by_title
            elif votes.get(owner.id):
                owner_tech[owner.id] = votes[owner.id].most_common(1)[0][0]

        id_map: Dict[str, str] = {}

        for component in components:
            if component.is_boundary:
                continue

            own_slug = slugify(component.title)
            if not own_slug:
                continue

            owner = owners.get(component.id)

            if owner is None:
                technology = self._extract_technology(component.technology)
                id_map[component.id] = f"{technology}.{own_slug}"
                continue

            technology = owner_tech.get(
                owner.id,
                self._extract_technology(owner.title)
            )
            id_map[component.id] = f"{technology}.{slugify(owner.title)}.{own_slug}"

        return id_map

    @staticmethod
    def _find_owner(component: Component, by_id: Dict[str, Component]) -> Optional[Component]:
        """
        Найти рамку-владельца компонента (будущий L2).

        Поднимаемся по вложенности до самой внешней рамки: промежуточные
        (Infrastructure, Services, Controllers) — это группировка внутри
        сервиса, а не отдельные компоненты.

        Args:
            component: Компонент
            by_id: Индекс компонентов по id

        Returns:
            Внешняя рамка или None
        """
        owner = None
        current = component
        seen = set()

        while current.parent and current.parent in by_id:
            if current.parent in seen:
                break
            seen.add(current.parent)

            current = by_id[current.parent]
            if current.is_boundary:
                owner = current

        return owner

    def _transform_component(
        self,
        component: Component,
        component_id: Optional[str] = None
    ) -> Dict:
        """
        Трансформировать компонент.

        Args:
            component: Компонент из схемы
            component_id: Готовый идентификатор DocHub

        Returns:
            Словарь с aspects и components
        """
        if component_id is None:
            technology = self._extract_technology(component.technology)
            component_id = f"{technology}.{slugify(component.title) or component.id}"

        # Аспект именуем по компоненту, а не по внутреннему id схемы
        aspect_slug = slugify(component.title) or component.id
        aspect_id = f"{self.full_domain_path}.{aspect_slug}"

        # Создаем аспект
        aspect = {
            aspect_id: {
                'title': component.title,
                'location': f"{self.domain}/{self.context}/{self.subdomain}/{aspect_slug}"
            }
        }

        if component.description:
            aspect[aspect_id]['description'] = component.description

        # Создаем компонент
        comp = {
            component_id: {
                'title': component.title,
                'entity': self._map_entity_type(component.type, component),
                'aspects': [aspect_id]
            }
        }

        if component.technology:
            comp[component_id]['technology'] = component.technology

        return {
            'aspects': aspect,
            'components': comp,
            'component_id': component_id
        }

    # Названия, за которыми обычно стоит группировка кода, а не компонент
    # архитектуры. Это подсказка, а не приговор: в одном проекте DTO —
    # мусор на схеме, в другом осмысленный слой
    STRUCTURAL_NAMES = {
        'dto', 'dtos', 'entities', 'entity', 'enums', 'enum', 'interfaces',
        'models', 'model', 'configuration', 'config', 'constants', 'helpers',
        'extensions', 'utils', 'common', 'shared', 'infrastructure',
        'dependencyinjection', 'contracts',
    }

    def _find_questionable(
        self,
        parse_result: ParseResult,
        own_ids: set
    ) -> List[Dict]:
        """
        Отметить элементы, о которых стоит спросить пользователя.

        Движок ничего не выбрасывает сам: что выносить на схему, зависит от
        конкретного случая. Здесь только основания для вопроса.

        Args:
            parse_result: Результат разбора схемы
            own_ids: Идентификаторы компонентов, попавших в наш файл

        Returns:
            Список словарей с id, названием и причиной сомнения
        """
        linked = {r.from_id for r in parse_result.relations}
        linked |= {r.to_id for r in parse_result.relations}

        found = []

        for component in parse_result.components:
            if component.is_boundary:
                continue

            component_id = self.id_map.get(component.id)
            if not component_id or component_id not in own_ids:
                continue

            reasons = []

            if component.id not in linked:
                reasons.append(
                    'нет ни одной связи на схеме — обычно это признак того, '
                    'что элемент нарисован для полноты картины'
                )

            title = (component.title or '').strip().lower()
            if title in self.STRUCTURAL_NAMES:
                reasons.append(
                    'название похоже на группировку кода, а не на компонент '
                    'архитектуры'
                )

            if not component.description:
                reasons.append('нет описания, назначение по схеме не понять')

            if reasons:
                found.append({
                    'component_id': component_id,
                    'title': component.title,
                    'reasons': reasons,
                })

        return found

    @staticmethod
    def _build_parent_components(component_ids: List[str]) -> Dict:
        """
        Создать недостающие родительские компоненты.

        Для L3 вида "dotnet.myService.feature" нужен L2 "dotnet.myService"
        со ссылкой на детей — иначе DocHub не построит вложенные области.

        Args:
            component_ids: Итоговые идентификаторы компонентов

        Returns:
            Словарь родительских компонентов
        """
        children: Dict[str, List[str]] = {}

        for component_id in component_ids:
            parts = component_id.split('.')
            if len(parts) < 3:
                continue
            parent_id = '.'.join(parts[:-1])
            children.setdefault(parent_id, []).append(component_id)

        return {
            parent_id: {
                'title': parent_id.split('.')[-1],
                'entity': 'component',
                'components': sorted(items)
            }
            for parent_id, items in children.items()
            if parent_id not in component_ids
        }

    def _create_context(
        self,
        component_ids: List[str],
        relations: List[Relation]
    ) -> Dict:
        """
        Создать контекст.

        Args:
            component_ids: Список ID компонентов
            relations: Список связей

        Returns:
            Словарь контекста
        """
        context = {
            'title': self.subdomain,
            'location': f"Domain/{self.domain}/{self.context}/{self.subdomain}",
            # Иначе DocHub дорисует всё окружение перечисленных компонентов
            # и диаграмма станет нечитаемой
            'extra-links': False,
            'components': component_ids
        }

        # Связи DocHub берёт из блока uml, а не из links
        after = self._transform_relations(relations, component_ids)
        if after:
            context['uml'] = {'$after': after}

        return context

    def _transform_relations(
        self,
        relations: List[Relation],
        component_ids: List[str]
    ) -> str:
        """
        Собрать связи в текст PlantUML для блока uml.$after.

        Args:
            relations: Связи из схемы
            component_ids: Идентификаторы компонентов контекста

        Returns:
            Строки вида "a --> b", по одной на связь
        """
        known = set(component_ids)
        lines = []
        seen = set()

        for relation in relations:
            source = self.id_map.get(relation.from_id)
            target = self.id_map.get(relation.to_id)

            # Связи на рамки и на отброшенные элементы не рисуем
            if not source or not target or source == target:
                continue
            if source not in known or target not in known:
                continue

            direction = relation.direction or '-->'
            if direction == '<--':
                source, target, direction = target, source, '-->'

            line = f"{source} {direction} {target}"
            if relation.label:
                line += f" : {relation.label}"

            if line not in seen:
                seen.add(line)
                lines.append(line)

        return '\n'.join(lines)

    def _extract_technology(self, technology: Optional[str]) -> str:
        """
        Извлечь технологию для ID компонента.

        Args:
            technology: Строка технологии

        Returns:
            Короткое название технологии
        """
        if not technology:
            return 'component'

        tech_lower = technology.lower()

        # Маппинг технологий
        tech_map = {
            'c#': 'dotnet',
            '.net': 'dotnet',
            'asp.net': 'dotnet',
            'java': 'java',
            'spring': 'java',
            'python': 'python',
            'node': 'nodejs',
            'nodejs': 'nodejs',
            'react': 'react',
            'angular': 'angular',
            'vue': 'vue',
            'postgresql': 'postgres',
            'postgres': 'postgres',
            'mysql': 'mysql',
            'mssql': 'mssql',
            'sql server': 'mssql',
            'mongodb': 'mongodb',
            'redis': 'redis',
            'rabbitmq': 'rabbitmq',
            'kafka': 'kafka'
        }

        # Ищем совпадение
        for key, value in tech_map.items():
            if key in tech_lower:
                return value

        # По умолчанию
        return 'component'

    # Слова в названии или технологии, выдающие очередь шины событий
    QUEUE_HINTS = ('queue', 'exchange', 'topic', 'очеред', 'обменник', 'rabbitmq', 'kafka')

    # Слова, выдающие хранилище данных
    DATABASE_HINTS = ('database', 'схема', 'бд ', 'db', 'postgres', 'mssql')

    def _map_entity_type(
        self,
        component_type: str,
        component: Optional[Component] = None
    ) -> str:
        """
        Маппинг типа компонента в entity DocHub.

        Одного c4Type мало: очереди и базы рисуются обычными контейнерами,
        а различаются по технологии и названию. В репозитории приняты
        component, database, queue, person, file, folder — типа system нет.

        Args:
            component_type: Тип из C4 (Person, Container, System, Component)
            component: Компонент целиком, если нужен разбор названия

        Returns:
            Entity тип для DocHub
        """
        type_map = {
            'Person': 'person',
            'Person_Ext': 'person',
            # В репозитории нет entity: system — верхнеуровневые блоки
            # описываются обычными компонентами
            'System': 'component',
            'System_Ext': 'component',
            'Container': 'component',
            'ContainerDb': 'database',
            'Component': 'component'
        }

        entity = type_map.get(component_type, 'component')

        if component is not None and entity == 'component':
            haystack = f"{component.title or ''} {component.technology or ''}".lower()

            if any(hint in haystack for hint in self.QUEUE_HINTS):
                return 'queue'
            if any(hint in haystack for hint in self.DATABASE_HINTS):
                return 'database'

        return entity

    def _is_reused(self, component_id: str, reused_components: List[Dict]) -> bool:
        """
        Проверить что компонент переиспользуется.

        Args:
            component_id: ID компонента из схемы
            reused_components: Список переиспользуемых компонентов

        Returns:
            True если переиспользуется
        """
        for reused in reused_components:
            if reused.get('schema_id') == component_id:
                return True
        return False

    def _get_reused_id(self, component_id: str, reused_components: List[Dict]) -> str:
        """
        Получить ID переиспользуемого компонента.

        Args:
            component_id: ID компонента из схемы
            reused_components: Список переиспользуемых компонентов

        Returns:
            ID компонента из репозитория
        """
        for reused in reused_components:
            if reused.get('schema_id') == component_id:
                return reused['id']
        return component_id

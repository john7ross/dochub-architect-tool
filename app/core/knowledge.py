"""
Накопленные знания о переносе схем.

Каждая схема чему-то учит: как в этой компании называют компоненты, чей
поддомен владеет очередями, что принято выносить на диаграмму, а что нет.
Без записи эти находки живут только внутри одного диалога, и следующую схему
приходится объяснять заново.

Уроки записываются, когда пользователь согласился загрузить схему: значит,
она сделана так, как задумано, и принятые по дороге решения можно считать
правильными. Скилл читает накопленное перед началом следующей работы.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from app.utils.logger import get_logger
from app.utils.paths import PROJECT_ROOT

DEFAULT_STORE = PROJECT_ROOT / 'knowledge' / 'lessons.yaml'

# Категории уроков. Ограниченный список удерживает файл читаемым:
# без него всё сваливается в одну кучу и перестаёт помогать.
CATEGORIES = {
    'naming': 'Именование компонентов, аспектов и контекстов',
    'ownership': 'Чей поддомен владеет компонентом',
    'inclusion': 'Что выносить на схему, а что опускать',
    'structure': 'Разбиение на контексты, уровни L2 и L3',
    'integration': 'Особенности интеграций: очереди, API, хранимые процедуры',
    'process': 'Порядок работы и согласования',
}

# Насколько похожими должны быть формулировки, чтобы считать их одним уроком
SIMILARITY_THRESHOLD = 0.85


@dataclass
class Lesson:
    """Один накопленный урок."""
    rule: str
    category: str
    rationale: str = ''
    source: str = ''
    added: str = ''
    seen: int = 1

    def to_dict(self) -> Dict:
        """Представление для файла."""
        data = {
            'rule': self.rule,
            'category': self.category,
            'seen': self.seen,
        }
        if self.rationale:
            data['rationale'] = self.rationale
        if self.source:
            data['source'] = self.source
        if self.added:
            data['added'] = self.added
        return data


@dataclass
class RecordResult:
    """Итог записи пачки уроков."""
    added: List[str] = field(default_factory=list)
    merged: List[str] = field(default_factory=list)
    rejected: List[str] = field(default_factory=list)


class KnowledgeStore:
    """Хранилище накопленных уроков."""

    def __init__(self, path: Optional[Path] = None):
        """
        Args:
            path: Файл хранилища. По умолчанию knowledge/lessons.yaml
                  в каталоге приложения; команда может указать общий
        """
        self.path = Path(path or DEFAULT_STORE)
        self.logger = get_logger()

    def load(self) -> List[Lesson]:
        """
        Прочитать уроки.

        Returns:
            Список уроков; пустой, если хранилища ещё нет
        """
        if not self.path.exists():
            return []

        try:
            data = yaml.safe_load(self.path.read_text(encoding='utf-8')) or {}
        except (yaml.YAMLError, OSError) as e:
            self.logger.warning(f"Не удалось прочитать знания: {e}")
            return []

        return [
            Lesson(
                rule=item.get('rule', ''),
                category=item.get('category', 'process'),
                rationale=item.get('rationale', ''),
                source=item.get('source', ''),
                added=item.get('added', ''),
                seen=int(item.get('seen', 1)),
            )
            for item in (data.get('lessons') or [])
            if item.get('rule')
        ]

    def save(self, lessons: List[Lesson]) -> None:
        """
        Записать уроки.

        Args:
            lessons: Полный список уроков
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)

        # Сначала то, что подтверждалось чаще: такие правила важнее
        ordered = sorted(
            lessons,
            key=lambda item: (item.category, -item.seen, item.rule.lower())
        )

        payload = {
            'version': 1,
            'lessons': [item.to_dict() for item in ordered],
        }

        self.path.write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False,
                           default_flow_style=False, width=100),
            encoding='utf-8'
        )

    def record(
        self,
        lessons: List[Dict],
        source: str = '',
        today: Optional[str] = None
    ) -> RecordResult:
        """
        Добавить пачку уроков, объединяя повторы.

        Повторное наблюдение того же правила не создаёт дубль, а увеличивает
        счётчик: так видно, какие правила подтвердились на нескольких схемах.

        Args:
            lessons: Список словарей с ключами rule, category, rationale
            source: Откуда уроки, например имя сервиса
            today: Дата в виде строки; по умолчанию сегодняшняя

        Returns:
            Что добавлено, что объединено, что отклонено
        """
        existing = self.load()
        result = RecordResult()
        stamp = today or date.today().isoformat()

        for item in lessons:
            rule = (item.get('rule') or '').strip()
            category = (item.get('category') or 'process').strip()

            if not rule:
                result.rejected.append('пустое правило')
                continue

            if category not in CATEGORIES:
                result.rejected.append(
                    f"{rule[:50]}: неизвестная категория {category!r}"
                )
                continue

            twin = self._find_similar(rule, existing)
            if twin is not None:
                twin.seen += 1
                if source and source not in twin.source:
                    twin.source = f"{twin.source}, {source}".strip(', ')
                result.merged.append(rule)
                continue

            existing.append(Lesson(
                rule=rule,
                category=category,
                rationale=(item.get('rationale') or '').strip(),
                source=source,
                added=stamp,
            ))
            result.added.append(rule)

        self.save(existing)
        self.logger.info(
            f"Знания: добавлено {len(result.added)}, "
            f"объединено {len(result.merged)}, отклонено {len(result.rejected)}"
        )
        return result

    def search(
        self,
        query: str = '',
        category: Optional[str] = None
    ) -> List[Lesson]:
        """
        Найти уроки.

        Args:
            query: Подстрока в правиле или обосновании
            category: Фильтр по категории

        Returns:
            Подходящие уроки
        """
        needle = query.lower().strip()

        return [
            lesson for lesson in self.load()
            if (not category or lesson.category == category)
            and (not needle
                 or needle in lesson.rule.lower()
                 or needle in lesson.rationale.lower())
        ]

    def as_markdown(self, lessons: Optional[List[Lesson]] = None) -> str:
        """
        Собрать уроки в читаемый вид.

        Args:
            lessons: Что показывать; по умолчанию всё

        Returns:
            Текст в Markdown
        """
        items = self.load() if lessons is None else lessons

        if not items:
            return (
                "Накопленных уроков пока нет. Они появятся после первой "
                "согласованной схемы."
            )

        by_category: Dict[str, List[Lesson]] = {}
        for lesson in items:
            by_category.setdefault(lesson.category, []).append(lesson)

        lines = ['# Накопленные правила', '']

        for category, title in CATEGORIES.items():
            group = by_category.get(category)
            if not group:
                continue

            lines.append(f'## {title}')
            lines.append('')

            for lesson in sorted(group, key=lambda x: -x.seen):
                mark = f' _(подтверждено {lesson.seen}×)_' if lesson.seen > 1 else ''
                lines.append(f'- **{lesson.rule}**{mark}')
                if lesson.rationale:
                    lines.append(f'  - почему: {lesson.rationale}')
                if lesson.source:
                    lines.append(f'  - откуда: {lesson.source}')
            lines.append('')

        return '\n'.join(lines).rstrip() + '\n'

    @staticmethod
    def _normalise(text: str) -> str:
        """Привести формулировку к виду, пригодному для сравнения."""
        return re.sub(r'[^\w\s]', '', text.lower()).strip()

    def _find_similar(self, rule: str, lessons: List[Lesson]) -> Optional[Lesson]:
        """
        Найти уже записанный урок с тем же смыслом.

        Args:
            rule: Новая формулировка
            lessons: Уже записанные уроки

        Returns:
            Похожий урок или None
        """
        candidate = self._normalise(rule)

        for lesson in lessons:
            ratio = SequenceMatcher(
                None, candidate, self._normalise(lesson.rule)
            ).ratio()
            if ratio >= SIMILARITY_THRESHOLD:
                return lesson

        return None

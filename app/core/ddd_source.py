"""
Доменная схема по ссылке.

Схема живёт у компании и меняется, а следить за свежестью копии руками — не
работа пользователя. Поэтому `DOCHUB_DDD_PATH` принимает и ссылку.

Скачивание происходит **один раз, при подготовке к работе** — в
`dochub_repo_sync`, рядом с обновлением репозитория. Дальше, начиная с брифа,
инструмент работает по скачанной копии и в сеть не ходит: в закрытом контуре
её может не быть вовсе, и обрывать этим сборку схемы нельзя.
"""

import hashlib
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

#: Ссылка на просмотр в Google Drive не отдаёт файл — нужен адрес выгрузки
_DRIVE_FILE = re.compile(r'drive\.google\.com/file/d/([\w-]+)')
_DRIVE_ID = re.compile(r'[?&]id=([\w-]+)')

USER_AGENT = 'dochub-architect-tool'


def is_url(value: str) -> bool:
    """
    Ссылка это или путь к файлу.

    Args:
        value: Значение настройки

    Returns:
        True для http и https
    """
    return str(value).lower().startswith(('http://', 'https://'))


def download_url(value: str) -> str:
    """
    Привести ссылку к виду, по которому отдаётся сам файл.

    Args:
        value: Ссылка как её дал пользователь

    Returns:
        Ссылка на выгрузку
    """
    match = _DRIVE_FILE.search(value) or _DRIVE_ID.search(value)
    if match and 'drive.google.com' in value:
        return f'https://drive.google.com/uc?export=download&id={match.group(1)}'
    return value


def cache_path(url: str) -> Path:
    """
    Где лежит скачанная копия.

    Имя привязано к ссылке: две разные схемы не должны затирать друг друга.

    Args:
        url: Ссылка на схему

    Returns:
        Путь к файлу кэша
    """
    root = os.environ.get('DOCHUB_CACHE') or os.environ.get('LOCALAPPDATA')
    base = Path(root) if root else Path.home() / '.cache'
    digest = hashlib.sha256(url.encode('utf-8')).hexdigest()[:12]
    return base / 'dochub-architect-tool' / f'ddd-{digest}.drawio'


def local_path(value: str) -> Tuple[Optional[Path], Optional[str]]:
    """
    Найти локальный файл схемы, не обращаясь в сеть.

    Так схему читают все шаги после подготовки: бриф, дерево доменов, поиск
    размещения. Сеть здесь недопустима — в закрытом контуре её нет.

    Args:
        value: Путь к файлу или ссылка

    Returns:
        Пара (путь или None, объяснение или None)
    """
    if not is_url(value):
        return Path(value), None

    cache = cache_path(value)
    if cache.exists():
        return cache, None

    return None, (
        'доменная схема задана ссылкой, но ещё не скачана. Обновите её '
        'до начала работы: dochub_repo_sync(update=true) — он забирает и '
        'изменения репозитория, и свежую доменную схему'
    )


def refresh(value: str, timeout: int = 30) -> Tuple[Optional[Path], Optional[str]]:
    """
    Скачать схему заново — шаг подготовки, а не работы.

    Args:
        value: Путь к файлу или ссылка
        timeout: Сколько ждать сеть

    Returns:
        Пара (путь или None, предупреждение или None). Предупреждение
        означает, что взята прежняя копия или схемы нет вовсе
    """
    if not is_url(value):
        path = Path(value)
        if path.exists():
            return path, None
        return None, f'доменная схема не найдена: {value}'

    cache = cache_path(value)

    try:
        request = urllib.request.Request(
            download_url(value), headers={'User-Agent': USER_AGENT})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        if cache.exists():
            return cache, (
                f'доменную схему не удалось обновить ({e}); работаем по ранее '
                'скачанной копии'
            )
        return None, (
            f'доменная схема не скачалась: {e}. Проверьте доступ к ссылке из '
            'этой сети или укажите в DOCHUB_DDD_PATH путь к локальному файлу'
        )

    # Диск отдаёт HTML-заглушку, когда доступ по ссылке закрыт
    if b'<html' in body[:200].lower() or body.lstrip()[:9].lower() == b'<!doctype':
        if cache.exists():
            return cache, (
                'по ссылке пришла HTML-страница, а не схема — похоже, закрыт '
                'доступ; работаем по ранее скачанной копии'
            )
        return None, (
            'по ссылке пришла HTML-страница, а не файл схемы. Обычно это '
            'значит, что доступ по ссылке закрыт'
        )

    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(body)
    return cache, None

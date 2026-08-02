#!/usr/bin/env python3
"""
Загрузить крупные файлы, которые не хранятся в репозитории.

PlantUML и просмотрщик drawio весят десятки мегабайт, поэтому в git они не
кладутся. Скрипт скачивает их один раз; дальше всё работает офлайн.

Запуск:
    python setup_assets.py
    python setup_assets.py --check    # только проверить, что на месте
"""

import argparse
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Раздача drawio отвечает 403 на обращение без обычного User-Agent
USER_AGENT = 'Mozilla/5.0 (compatible; dochub-architect-tool/setup_assets)'


def _context() -> ssl.SSLContext:
    """
    Контекст TLS для загрузки.

    Хранилище сертификатов Windows на части машин содержит просроченный
    корневой сертификат, и проверка цепочки падает ещё до запроса. Набор
    certifi приходит вместе с httpx, поэтому берём его, если он есть.
    """
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()

    return ssl.create_default_context(cafile=certifi.where())

ASSETS = [
    {
        'path': ROOT / 'lib' / 'plantuml.jar',
        'url': 'https://github.com/plantuml/plantuml/releases/latest/download/plantuml.jar',
        'what': 'PlantUML — рендеринг диаграмм',
        'without': 'превью и рендер контекстов не работают',
    },
    {
        'path': ROOT / 'vendor' / 'viewer-static.min.js',
        'url': 'https://viewer.diagrams.net/js/viewer-static.min.js',
        'what': 'просмотрщик drawio — отрисовка оригинала схемы',
        'without': 'левая панель превью останется пустой',
    },
]

MAVEN = 'https://repo1.maven.org/maven2'

# Раскладка ELK, которую DocHub просит по умолчанию. В самом plantuml.jar её
# нет: нужны Eclipse Layout Kernel, EMF и Guava. Все они лежат на Maven Central,
# поэтому качаются напрямую — плагин DocHub для IDE не требуется.
ELK_JARS = [
    ('org/eclipse/elk', 'org.eclipse.elk.core', '0.8.1'),
    ('org/eclipse/elk', 'org.eclipse.elk.graph', '0.8.1'),
    ('org/eclipse/elk', 'org.eclipse.elk.alg.common', '0.8.1'),
    ('org/eclipse/elk', 'org.eclipse.elk.alg.layered', '0.8.1'),
    ('org/eclipse/emf', 'org.eclipse.emf.common', '2.12.0'),
    ('org/eclipse/emf', 'org.eclipse.emf.ecore', '2.12.0'),
    ('org/eclipse/emf', 'org.eclipse.emf.ecore.xmi', '2.12.0'),
    ('com/google/guava', 'guava', '33.4.8-jre'),
    ('com/google/guava', 'failureaccess', '1.0.3'),
    ('com/google/guava', 'listenablefuture',
     '9999.0-empty-to-avoid-conflict-with-guava'),
    ('com/google/j2objc', 'j2objc-annotations', '3.0.0'),
    ('org/jspecify', 'jspecify', '1.0.0'),
    ('com/google/errorprone', 'error_prone_annotations', '2.36.0'),
]

for _group, _artifact, _version in ELK_JARS:
    ASSETS.append({
        'path': ROOT / 'lib' / 'elk' / f'{_artifact}-{_version}.jar',
        'url': f'{MAVEN}/{_group}/{_artifact}/{_version}/{_artifact}-{_version}.jar',
        'what': f'ELK: {_artifact}',
        'without': 'раскладка через smetana — схема шире, содержимое то же',
        'optional': True,
    })


def check() -> bool:
    """
    Проверить наличие файлов.

    Returns:
        True если всё на месте
    """
    complete = True
    elk_present = 0

    for asset in ASSETS:
        path = asset['path']

        # Про каждый jar раскладки отчитываться незачем — их тринадцать
        if asset.get('optional'):
            elk_present += path.exists()
            continue

        if path.exists():
            size = path.stat().st_size / 1048576
            print(f'  есть  {path.relative_to(ROOT).as_posix()}  ({size:.1f} МБ)')
        else:
            complete = False
            print(f'  НЕТ   {path.relative_to(ROOT).as_posix()}  — {asset["without"]}')

    total_elk = sum(1 for a in ASSETS if a.get('optional'))
    if elk_present == total_elk:
        print(f'  есть  lib/elk/  (раскладка ELK, {total_elk} файлов)')
    else:
        print(f'  НЕТ   lib/elk/  — раскладка через smetana '
              f'({elk_present} из {total_elk})')

    return complete


def fetch(asset: dict) -> bool:
    """
    Скачать один файл.

    Args:
        asset: Описание файла

    Returns:
        True при успехе
    """
    path = asset['path']
    path.parent.mkdir(parents=True, exist_ok=True)

    print(f'{asset["what"]}')
    print(f'  {asset["url"]}')

    request = urllib.request.Request(asset['url'], headers={'User-Agent': USER_AGENT})

    try:
        with urllib.request.urlopen(request, timeout=120,
                                    context=_context()) as response:
            data = response.read()
    except (urllib.error.URLError, TimeoutError) as e:
        print(f'  не удалось: {e}')
        return False

    path.write_bytes(data)
    print(f'  сохранено: {len(data) / 1048576:.1f} МБ')
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true',
                        help='только проверить наличие')
    args = parser.parse_args()

    if args.check:
        print('Проверка:')
        return 0 if check() else 1

    required = [a for a in ASSETS if not a.get('optional')]
    optional = [a for a in ASSETS if a.get('optional')]

    ok = True
    for asset in required:
        if asset['path'].exists():
            print(f'{asset["what"]} — уже на месте, пропускаю')
            continue
        ok &= fetch(asset)

    missing = [a for a in optional if not a['path'].exists()]
    if missing:
        print(f'\nРаскладка ELK: {len(missing)} файлов с Maven Central')
        for asset in missing:
            if not fetch(asset):
                print('  без ELK рендеринг пойдёт через smetana — это не ошибка')
                break

    print()
    print('Итог:')
    check()

    if not ok:
        print('\nЧасть файлов не скачалась. Их можно положить вручную по путям выше.')

    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())

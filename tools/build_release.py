#!/usr/bin/env python3
"""
Собрать архив для раздачи коллегам.

Внутри архива — всё, что нужно для работы, включая сам Python: у сотрудника
может не быть ни интерпретатора, ни доступа к PyPI, ни прав на установку.
Распаковал, запустил .bat — работает.

Запуск:
    python tools/build_release.py
    python tools/build_release.py --runtime <python-3.11.x-embed-amd64.zip>

Архив собирается в каталог рядом с проектом.
"""

import argparse
import hashlib
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
NAME = 'dochub-architect-tool'

# Windows x64: внутри лежат embeddable-питон, JRE и git именно под неё,
# и по имени файла это должно быть видно
PLATFORM = 'win64'


def version() -> str:
    """
    Версия проекта — из app/__init__.py, второй копии здесь нет.

    Returns:
        Строка версии
    """
    import re

    source = (ROOT / 'app' / '__init__.py').read_text(encoding='utf-8')
    return re.search(r"__version__\s*=\s*'([^']+)'", source).group(1)

# Тот же выпуск, на котором проект разрабатывается и тестируется
PYTHON_VERSION = '3.11.9'
RUNTIME_URL = (f'https://www.python.org/ftp/python/{PYTHON_VERSION}/'
               f'python-{PYTHON_VERSION}-embed-amd64.zip')

# Java нужна PlantUML. Temurin — сборка OpenJDK под GPLv2 с Classpath
# Exception: её можно раздавать вместе с приложением. Берём JRE, а не JDK:
# компилятор и инструменты разработчика получателю ни к чему
JRE_API = ('https://api.adoptium.net/v3/assets/latest/17/hotspot'
           '?architecture=x64&image_type=jre&os=windows&vendor=eclipse')

# git нужен для работы с арх.репозиторием. MinGit — официальная минимальная
# сборка Git for Windows: распаковывается в каталог и не требует установки
GIT_API = 'https://api.github.com/repos/git-for-windows/git/releases/latest'

# Что кладём в архив
TREES = ('app', 'docs', 'skills', 'tests', 'vendor', 'workflows', 'lib')
FILES = ('README.md', 'README.ru.md', 'ARCHITECTURE.md', 'ARCHITECTURE.ru.md',
         'ROADMAP.md', 'ROADMAP.ru.md', 'CHANGELOG.md', 'SECURITY.md',
         'THIRD-PARTY-NOTICES.md', 'LICENSE', 'requirements.txt', 'pytest.ini',
         'setup_assets.py', '.env.example', 'donate-qr.png')

# Доменная схема и схемы сервисов у каждой компании свои: в архив они не
# кладутся, пути к ним пользователь указывает в .env
EXTRA = ()

SKIP_DIRS = {'__pycache__', '.pytest_cache', '.git', '.venv'}


def log(message: str) -> None:
    print(message, flush=True)


def fetch_runtime(explicit: str = '') -> Path:
    """
    Взять embeddable-питон: он не требует установки и прав.

    Args:
        explicit: Готовый архив, если он уже скачан

    Returns:
        Путь к zip с рантаймом
    """
    if explicit:
        return Path(explicit)

    cache = ROOT.parent / f'python-{PYTHON_VERSION}-embed-amd64.zip'
    if cache.exists():
        log(f'рантайм уже скачан: {cache.name}')
        return cache

    log(f'качаю {RUNTIME_URL}')
    request = urllib.request.Request(
        RUNTIME_URL, headers={'User-Agent': 'dochub-architect-tool/build'})
    with urllib.request.urlopen(request, timeout=300) as response:
        cache.write_bytes(response.read())

    log(f'скачано: {cache.stat().st_size // 1048576} МБ')
    return cache


def prepare_runtime(runtime_zip: Path, target: Path) -> None:
    """
    Развернуть рантайм и положить в него зависимости.

    Args:
        runtime_zip: Архив embeddable-питона
        target: Куда разворачивать
    """
    with zipfile.ZipFile(runtime_zip) as z:
        z.extractall(target)

    # Файл ._pth задаёт sys.path целиком и отключает добавление рабочего
    # каталога. Поэтому объявляем и site-packages, и сам проект: без второго
    # "python -m app.mcp_server" не найдёт пакет app, хотя запущен из корня
    # проекта. Пути отсчитываются от каталога с python.exe
    for pth in target.glob('python*._pth'):
        lines = pth.read_text(encoding='utf-8').splitlines()
        lines = [line.replace('#import site', 'import site') for line in lines]

        anchor = lines.index('.') + 1 if '.' in lines else 0
        for extra in ('Lib\\site-packages', '..'):
            if extra not in lines:
                lines.insert(anchor, extra)
                anchor += 1

        pth.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        log(f'{pth.name}: подключены site-packages и корень проекта')

    packages = target / 'Lib' / 'site-packages'
    packages.mkdir(parents=True, exist_ok=True)

    # Колёса берутся под рантайм из архива, а не под интерпретатор сборщика.
    # На машине сборки стоит свой выпуск Python, и без этих флагов pip кладёт
    # в рантайм 3.11 бинарники под чужой ABI: pydantic и cffi молча
    # не импортируются, и узнаёт об этом уже получатель
    major, minor, _ = PYTHON_VERSION.split('.')
    log(f'ставлю зависимости в рантайм (колёса под cp{major}{minor} win_amd64)')
    subprocess.run(
        [sys.executable, '-m', 'pip', 'install', '--quiet', '--target',
         str(packages), '-r', str(ROOT / 'requirements.txt'),
         '--only-binary=:all:',
         '--python-version', f'{major}.{minor}',
         '--implementation', 'cp',
         '--abi', f'cp{major}{minor}',
         '--platform', 'win_amd64',
         # Свой кэш байт-кода pip пишет интерпретатором сборщика, а тот
         # помечает .pyc чужим тегом: рантайму 3.11 они бесполезны, а вес
         # и путь машины сборки в архив уносят
         '--no-compile'],
        check=True, stdin=subprocess.DEVNULL
    )

    # Обёртки консольных скриптов: pip кладёт в bin/ по .exe на каждый
    # entry_points, и в каждую зашит shebang с путём интерпретатора машины
    # сборки — с именем её пользователя, если Python стоит в профиле. У
    # получателя такого пути нет, так что обёртки нерабочие с самого начала,
    # а всё в проекте и так зовётся через runtime/python.exe -m
    scripts = packages / 'bin'
    if scripts.is_dir():
        count = len(list(scripts.iterdir()))
        shutil.rmtree(scripts)
        log(f'убраны обёртки pip из bin/: {count}')

    # Кэш байт-кода: первый запуск сервера у пользователя не должен уходить
    # на компиляцию всего pydantic. Компилирует сам рантайм из архива, иначе
    # тег .pyc не совпадёт и получатель скомпилирует всё заново.
    # -s срезает путь машины сборки: без него имя её пользователя уезжает
    # в каждый .pyc, а в трассировках у получателя стоят чужие каталоги
    subprocess.run(
        [str(target / 'python.exe'), '-m', 'compileall', '-q',
         '-s', str(target.parent), str(packages)],
        check=False, stdin=subprocess.DEVNULL
    )

    # Часть кэша рождается раньше compileall и мимо -s: pywin32 кладёт в
    # site-packages свой .pth, и интерпретатор импортирует его ещё на старте.
    # Такой .pyc уносит в архив путь машины сборки вместе с именем её
    # пользователя, поэтому выбрасываем: получатель скомпилирует эти модули
    # сам при первом импорте
    marker = str(ROOT).encode()
    dropped = [p for p in packages.rglob('*.pyc') if marker in p.read_bytes()]
    for path in dropped:
        path.unlink()
    if dropped:
        log(f'выброшено .pyc с путём машины сборки: {len(dropped)}')


def fetch_jre(target: Path) -> str:
    """
    Положить рядом Java: без неё не рисуются схемы, а ставить её получателю
    не должно быть нужно.

    Args:
        target: Каталог runtime, внутри появится jre/

    Returns:
        Название выпуска
    """
    import json

    cache_dir = ROOT.parent
    request = urllib.request.Request(
        JRE_API, headers={'User-Agent': 'dochub-architect-tool/build'})
    with urllib.request.urlopen(request, timeout=120) as response:
        asset = json.load(response)[0]

    release = asset['release_name']
    package = asset['binary']['package']
    cache = cache_dir / package['name']

    if not cache.exists():
        log(f'качаю Java {release} ({package["size"] // 1048576} МБ)')
        request = urllib.request.Request(
            package['link'], headers={'User-Agent': 'dochub-architect-tool/build'})
        with urllib.request.urlopen(request, timeout=600) as response:
            cache.write_bytes(response.read())

        digest = hashlib.sha256(cache.read_bytes()).hexdigest()
        if digest != package['checksum']:
            cache.unlink()
            raise SystemExit(f'контрольная сумма Java не сошлась: {digest}')
        log('контрольная сумма сошлась')
    else:
        log(f'Java уже скачана: {cache.name}')

    unpacked = target / '_jre_tmp'
    with zipfile.ZipFile(cache) as z:
        z.extractall(unpacked)

    # В архиве всё лежит внутри каталога вида jdk-17.0.20+8-jre
    inner = next(p for p in unpacked.iterdir() if p.is_dir())
    shutil.move(str(inner), str(target / 'jre'))
    shutil.rmtree(unpacked)

    return release


def fetch_git(target: Path) -> str:
    """
    Положить рядом git: без него не работает ни синхронизация с
    репозиторием, ни публикация, а ставить его получателю не должно быть нужно.

    Args:
        target: Каталог runtime, внутри появится git/

    Returns:
        Название выпуска
    """
    import json

    def cached() -> Optional[Path]:
        """Ранее скачанный MinGit: сборка не должна зависеть от api.github."""
        found = sorted(
            p for p in ROOT.parent.glob('MinGit-*-64-bit.zip')
            if 'busybox' not in p.name
        )
        return found[-1] if found else None

    try:
        request = urllib.request.Request(
            GIT_API, headers={'User-Agent': 'dochub-architect-tool/build'})
        with urllib.request.urlopen(request, timeout=120) as response:
            release = json.load(response)
    except OSError as e:
        # API отвечает не всегда, а нужный архив обычно уже лежит рядом:
        # без этого сборка падает там, где качать нечего
        local = cached()
        if not local:
            raise
        log(f'api.github недоступен ({e}), беру скачанный git: {local.name}')
        with zipfile.ZipFile(local) as z:
            z.extractall(target / 'git')
        return local.stem.replace('MinGit-', 'v').replace('-64-bit', '')

    asset = next(a for a in release['assets']
                 if a['name'].startswith('MinGit') and '64-bit' in a['name']
                 and 'busybox' not in a['name'])

    cache = ROOT.parent / asset['name']
    if not cache.exists():
        log(f'качаю git {release["tag_name"]} ({asset["size"] // 1048576} МБ)')
        request = urllib.request.Request(
            asset['browser_download_url'],
            headers={'User-Agent': 'dochub-architect-tool/build'})
        with urllib.request.urlopen(request, timeout=600) as response:
            cache.write_bytes(response.read())
    else:
        log(f'git уже скачан: {cache.name}')

    with zipfile.ZipFile(cache) as z:
        z.extractall(target / 'git')

    return release['tag_name']


LAUNCHERS = {
    'connect-to-claude.bat': r'''@echo off
chcp 866 >nul
title DocHub Architect - podklyuchenie k Claude Code
setlocal
set "HERE=%~dp0"

echo.
echo   Propisyvayu MCP-server dochub v nastroyki Claude Code.
echo   Suschestvuyuschie servery ne zatirayutsya.
echo.

"%HERE%runtime\python.exe" "%HERE%skills\dochub-architect\tools\install_mcp.py" --agent claude --scope user
if errorlevel 1 goto :fail

echo.
echo   Gotovo. Perezapustite Claude Code, chtoby on uvidel server.
echo   Pered rabotoy zapolnite .env - obrazec lezhit v .env.example
echo.
pause
exit /b 0

:fail
echo.
echo   Ne poluchilos. Smotrite soobschenie vyshe.
echo.
pause
exit /b 1
''',
    'check.bat': r'''@echo off
chcp 866 >nul
title DocHub Architect - proverka ustanovki
setlocal
set "HERE=%~dp0"

echo.
echo   1. Zavisimosti i rantaym
"%HERE%runtime\python.exe" -c "import mcp, pydantic, yaml, ruamel.yaml, jsonata, httpx, certifi, sys; print('      Python', sys.version.split()[0], '- vse biblioteki na meste')"
if errorlevel 1 goto :fail

echo.
echo   2. Java iz komplekta
"%HERE%runtime/jre/bin/java.exe" -version 2>&1 | findstr /i "version"
if errorlevel 1 goto :fail

echo.
echo   3. Git iz komplekta
"%HERE%runtime/git/cmd/git.exe" --version
if errorlevel 1 goto :fail

echo.
echo   4. Vlozhennye fayly
cd /d "%HERE%"
"%HERE%runtime\python.exe" setup_assets.py --check

echo.
echo   5. Server
"%HERE%runtime\python.exe" -c "import app.mcp_server as m; print('      instrumentov:', len([n for n in dir(m) if n.startswith('dochub_')]))"
if errorlevel 1 goto :fail

echo.
echo   Vsyo v poryadke.
echo.
pause
exit /b 0

:fail
echo.
echo   Est problema - smotrite soobschenie vyshe.
echo.
pause
exit /b 1
''',
    'tests.bat': r'''@echo off
chcp 866 >nul
title DocHub Architect - testy
setlocal
set "HERE=%~dp0"
cd /d "%HERE%"
"%HERE%runtime\python.exe" -m pytest
echo.
pause
''',
}

READ_ME_EN = """DocHub Architect Tool — transferring C4 diagrams into a DocHub repository
=========================================================================

Nothing to install: Python, Java, git and every library sit inside this
archive, in the runtime folder. No internet access is needed to set it up —
only to reach your GitLab afterwards.

Steps:

1. Unpack the archive into a SHORT path: C:\\dochub-architect-tool or
   D:\\dochub-architect-tool. This is a requirement, not a suggestion.

   Windows cannot open a file whose full path is longer than 260 characters,
   and this archive contains deeply nested ones. Unpacked from Downloads or
   the Desktop it extracts halfway and silently: some files simply never
   appear, and it breaks later, somewhere that looks unrelated. For the same
   reason the path must contain no spaces and no non-latin characters.

2. Clone your architecture repository if you have not already. The tool works
   on a local copy and never creates one itself. git ships inside the archive:

     runtime\\git\\cmd\\git.exe clone <your repository URL> C:\\repos\\architectural-repository

3. Copy .env.example to .env and fill in:
     DOCHUB_REPO_ROOT   — where the architecture repository is cloned
     DOCHUB_DDD_PATH    — your company domain schema (.drawio)
     GITLAB_TOKEN       — a token with the api scope. Optional: everything
                          works without it, you just open the merge request
                          yourself, through the link the tool prints after
                          pushing the branch.

4. Run "check.bat" — it reports whether everything is in place.

5. Run "connect-to-claude.bat" and restart Claude Code.

6. Tell the agent: "here is a service diagram, transfer it into DocHub" and
   give it the path to a .drawio or .puml file. It asks for what is missing.

Documentation: README.md, docs/USAGE.md, skills/dochub-architect/SKILL.md
Security and tokens: SECURITY.md
"""


READ_ME = '''DocHub Architect Tool — перенос схем в архитектурный репозиторий
================================================================

Устанавливать ничего не нужно: Python, Java, git и все библиотеки лежат
внутри архива, в каталоге runtime. Доступ в интернет для установки не нужен —
он понадобится только самой работе, чтобы дотянуться до GitLab.

Порядок:

1. Распакуйте архив в КОРОТКИЙ путь: C:\\dochub-architect-tool или
   D:\\dochub-architect-tool. Это обязательное условие, а не пожелание.

   Windows не открывает файлы, полный путь к которым длиннее 260 символов,
   а внутри архива есть файлы с глубокой вложенностью. Из «Загрузок» или
   с рабочего стола архив распакуется наполовину и молча: часть файлов
   просто не появится, а сломается это потом и в неочевидном месте.
   По той же причине в пути не должно быть пробелов и кириллицы.

2. Склонируйте свой архитектурный репозиторий, если ещё не склонировали.
   Инструмент работает с локальной копией и сам её не создаёт. git лежит
   внутри архива:

     runtime\\git\\cmd\\git.exe clone <адрес репозитория> C:\\repos\\architectural-repository

3. Скопируйте .env.example в .env и впишите:
     DOCHUB_REPO_ROOT   — куда клонирован архитектурный репозиторий
     DOCHUB_DDD_PATH    — доменная схема вашей компании (.drawio)
     GITLAB_TOKEN       — токен со scope api. Необязателен: без него всё
                          работает, merge request просто открываете сами
                          по ссылке, которую инструмент печатает после
                          отправки ветки.

4. Запустите «check.bat» — он скажет, всё ли на месте.

5. Запустите «connect-to-claude.bat» и перезапустите Claude Code.

6. Скажите агенту: «вот схема сервиса, перенеси её в DocHub» и дайте путь
   к .drawio или .puml. Дальше он спросит недостающее сам.

Документация: README.ru.md, docs/USAGE.ru.md, skills/dochub-architect/SKILL.md
Безопасность и токены: SECURITY.md
'''


def collect(build: Path) -> int:
    """
    Скопировать файлы проекта в каталог сборки.

    Args:
        build: Каталог сборки

    Returns:
        Сколько файлов скопировано
    """
    count = 0

    for tree in TREES:
        source = ROOT / tree
        if not source.is_dir():
            log(f'нет каталога {tree}, пропускаю')
            continue
        for path in source.rglob('*'):
            if not path.is_file() or set(path.relative_to(ROOT).parts) & SKIP_DIRS:
                continue
            target = build / path.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            count += 1

    for name in FILES + EXTRA:
        source = ROOT / name
        if not source.exists():
            log(f'нет файла {name}, пропускаю')
            continue
        target = build / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        count += 1

    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', default='',
                        help='готовый zip embeddable-питона')
    args = parser.parse_args()

    # Каталог сборки — внутри проекта, а не рядом с ним: у частной и
    # публичной версий имя проекта одно, и общий каталог они затирали друг
    # у друга. Убирается сразу после упаковки: 340 МБ мусора никому не нужны
    build = ROOT / '.build'
    if build.exists():
        shutil.rmtree(build)
    build.mkdir(parents=True)

    log('собираю файлы проекта')
    count = collect(build)
    log(f'файлов проекта: {count}')

    prepare_runtime(fetch_runtime(args.runtime), build / 'runtime')
    log(f'Java внутри: {fetch_jre(build / "runtime")}')
    log(f'git внутри: {fetch_git(build / "runtime")}')

    for name, body in LAUNCHERS.items():
        (build / name).write_bytes(body.replace('\n', '\r\n').encode('cp866'))
    # Памятка на двух языках, как и остальная документация проекта
    (build / 'READ ME FIRST.txt').write_bytes(
        READ_ME_EN.replace('\n', '\r\n').encode('utf-8-sig'))
    (build / 'ЧИТАЙ МЕНЯ.txt').write_bytes(
        READ_ME.replace('\n', '\r\n').encode('utf-8-sig'))

    archive = ROOT / 'dist' / f'{NAME}-{version()}-portable-{PLATFORM}.zip'
    archive.parent.mkdir(exist_ok=True)
    if archive.exists():
        archive.unlink()

    log('пакую')
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for path in sorted(build.rglob('*')):
            if path.is_file():
                z.write(path, Path(NAME) / path.relative_to(build))

    digest = hashlib.sha256(archive.read_bytes()).hexdigest()

    # Архив собран — распакованная копия больше не нужна
    shutil.rmtree(build, ignore_errors=True)

    log('')
    log(f'готово: {archive}')
    log(f'размер: {archive.stat().st_size / 1048576:.1f} МБ')
    log(f'sha256: {digest}')
    return 0


if __name__ == '__main__':
    sys.exit(main())

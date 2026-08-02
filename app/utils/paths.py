"""
Пути к ресурсам приложения.

Считаются от расположения кода, а не от текущей рабочей директории:
MCP-сервер запускается из произвольного каталога, и относительные пути
вида "vendor/dochub" в этом случае не разрешаются.
"""

from pathlib import Path
from typing import Optional

# app/utils/paths.py -> корень проекта
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

VENDOR_DIR = PROJECT_ROOT / 'vendor'
DOCHUB_METAMODEL_DIR = VENDOR_DIR / 'dochub'
DRAWIO_VIEWER = VENDOR_DIR / 'viewer-static.min.js'
PLANTUML_JAR = PROJECT_ROOT / 'lib' / 'plantuml.jar'
STATIC_DIR = PROJECT_ROOT / 'app' / 'preview' / 'static'

# Внешние программы, без которых часть работы не делается: Java рисует схемы,
# git ходит в репозиторий. В портативной раздаче обе лежат рядом, и ставить их
# получателю не нужно; при установке из исходников берутся системные
BUNDLED_JRE = PROJECT_ROOT / 'runtime' / 'jre'
BUNDLED_GIT = PROJECT_ROOT / 'runtime' / 'git'


def _bundled(root: Path, *relative: str) -> Optional[str]:
    """
    Найти вложенную программу.

    Args:
        root: Корень вложенного пакета
        relative: Пути к исполняемому файлу внутри него

    Returns:
        Путь строкой или None, если вложенной программы нет
    """
    for name in relative:
        candidate = root / name
        if candidate.exists():
            return str(candidate)

    return None


def java_command() -> str:
    """
    Чем запускать PlantUML.

    Returns:
        Путь к вложенной Java, если она есть, иначе просто "java" из PATH
    """
    return _bundled(BUNDLED_JRE, 'bin/java.exe', 'bin/java') or 'java'


def git_command() -> str:
    """
    Чем работать с репозиторием.

    Returns:
        Путь к вложенному git, если он есть, иначе просто "git" из PATH
    """
    return _bundled(BUNDLED_GIT, 'cmd/git.exe', 'bin/git.exe', 'bin/git') or 'git'

# Раскладка ELK, которую DocHub использует по умолчанию (renderCore: elk).
# В самом plantuml.jar её нет. Jar-файлы качаются с Maven Central скриптом
# setup_assets.py; те же самые лежат в плагине DocHub для IDE, поэтому
# проверяются оба места.
ELK_DIR = PROJECT_ROOT / 'lib' / 'elk'
IDEA_PLUGIN_LIB = PROJECT_ROOT / 'plugins' / 'IDEAPlugin' / 'lib'

ELK_JAR_PREFIXES = (
    'org.eclipse.elk.',
    'org.eclipse.emf.',
    'guava-',
    'failureaccess-',
    'listenablefuture-',
    'j2objc-annotations-',
    'jspecify-',
    'error_prone_annotations-'
)


def elk_jars() -> list:
    """
    Собрать jar'ы, нужные для раскладки ELK.

    Ищет в каталоге, куда их кладёт setup_assets.py, и в плагине DocHub для
    IDE, если он рядом.

    Returns:
        Список путей; пустой, если ни там, ни там ничего нет
    """
    found = {}

    for directory in (ELK_DIR, IDEA_PLUGIN_LIB):
        if not directory.is_dir():
            continue
        for path in directory.glob('*.jar'):
            if path.name.startswith(ELK_JAR_PREFIXES):
                found.setdefault(path.name, path)

    return sorted(found.values())

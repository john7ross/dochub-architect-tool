"""
Публикация схемы в архитектурный репозиторий.

Ветка, коммит, push и merge request. Работает через git CLI и HTTP API
GitLab — без GitPython и python-gitlab, чтобы приложение оставалось
переносимым и разворачивалось распаковкой архива.

Токен читается только из переменной окружения: он не должен попадать
ни в параметры инструментов, ни в логи, ни в историю диалога.
"""

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import quote, urlparse

import httpx

from app.utils.logger import get_logger
from app.utils.paths import git_command

TOKEN_ENV_VARS = ('GITLAB_TOKEN', 'CI_JOB_TOKEN', 'GITLAB_PRIVATE_TOKEN')

# Сколько ждём операцию с сетью: за корпоративным прокси недоступный
# репозиторий отвечает не отказом, а молчанием
NETWORK_TIMEOUT = 60


class PublishError(Exception):
    """Ошибка публикации."""


@dataclass
class PublishResult:
    """Итог публикации."""
    branch: str
    committed: bool
    pushed: bool
    commit_sha: Optional[str] = None
    merge_request_url: Optional[str] = None
    files: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass
class SyncReport:
    """Состояние репозитория относительно удалённого."""
    repo_root: str
    remote_url: str
    protocol: str
    reachable: bool
    current_branch: str
    target_branch: str
    unreachable_reason: Optional[str] = None
    fetched: bool = False
    behind: Optional[int] = None
    ahead: Optional[int] = None
    dirty_files: List[str] = field(default_factory=list)
    updated: bool = False
    branch_created: Optional[str] = None
    branch_switched: Optional[str] = None
    warnings: List[str] = field(default_factory=list)


class Publisher:
    """Публикует изменения архитектурного репозитория."""

    def __init__(self, repo_path: Path):
        """
        Args:
            repo_path: Корень git-репозитория
        """
        self.repo_path = Path(repo_path).resolve()
        self.logger = get_logger()

        if not (self.repo_path / '.git').exists():
            raise PublishError(f"Не git-репозиторий: {self.repo_path}")

    def _git(self, *args: str, check: bool = True, timeout: int = 180) -> str:
        """
        Выполнить команду git в репозитории.

        Args:
            args: Аргументы git
            check: Падать при ненулевом коде возврата
            timeout: Сколько ждать команду

        Returns:
            stdout команды
        """
        # git не должен спрашивать логин и пароль: сервер запущен без
        # терминала, и запрос учётных данных превратится в зависание
        env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GCM_INTERACTIVE': 'never'}

        try:
            result = subprocess.run(
                [git_command(), *args],
                cwd=self.repo_path,
                capture_output=True,
                # Без DEVNULL дочерний git наследует stdin сервера, а это труба
                # протокола MCP: она не закрывается, и git не завершается вовсе
                stdin=subprocess.DEVNULL,
                env=env,
                timeout=timeout
            )
        except FileNotFoundError:
            # Без этого пользователь видит "WinError 2: не найден указанный
            # файл" и гадает, какой именно
            raise PublishError(
                "git не найден. В портативной раздаче он лежит рядом с "
                "проектом; при установке из исходников поставьте Git for "
                "Windows или добавьте его в PATH"
            )

        stdout = result.stdout.decode('utf-8', errors='replace').strip()
        stderr = result.stderr.decode('utf-8', errors='replace').strip()

        if check and result.returncode != 0:
            raise PublishError(f"git {' '.join(args)}: {stderr or stdout}")

        return stdout

    def current_branch(self) -> str:
        """Текущая ветка."""
        return self._git('rev-parse', '--abbrev-ref', 'HEAD')

    def pending_files(self) -> List[str]:
        """
        Изменённые и новые файлы в рабочем каталоге.

        Returns:
            Пути относительно корня репозитория
        """
        output = self._git('status', '--porcelain')
        files = []

        for line in output.splitlines():
            # Формат: два символа кода состояния, затем путь
            if len(line) <= 2:
                continue
            # Переименования выглядят как "old -> new"
            path = line[2:].split(' -> ')[-1].strip().strip('"')
            if path:
                files.append(path)

        return files

    def protocol(self) -> str:
        """Как настроен origin: https или ssh."""
        url = self.remote_url()
        return 'ssh' if url.startswith('git@') or url.startswith('ssh://') else 'https'

    def sync(
        self,
        target_branch: str = 'main',
        branch: Optional[str] = None,
        update: bool = False
    ) -> SyncReport:
        """
        Посмотреть на репозиторий перед работой и, если просят, подготовить его.

        Схему нельзя строить на том, что лежит на диске: за время между
        задачами репозиторий уезжает вперёд, и расхождение обнаружится только
        при push, когда работа уже сделана.

        Args:
            target_branch: Ветка, относительно которой считаем отставание
            branch: Рабочая ветка; будет создана или выбрана
            update: Подтянуть изменения (только fast-forward)

        Returns:
            SyncReport

        Raises:
            PublishError: если репозиторий не готов к работе
        """
        try:
            remote_url = self.remote_url()
        except PublishError as e:
            # Причину терять нельзя: «нет origin» и «git не найден» лечатся
            # совершенно по-разному, а выглядели одинаково
            if 'git не найден' in str(e):
                raise
            raise PublishError(
                f"У репозитория {self.repo_path} нет удалённого origin. "
                "Схему можно собрать и локально, но опубликовать — нет"
            )

        report = SyncReport(
            repo_root=str(self.repo_path),
            remote_url=remote_url,
            protocol=self.protocol(),
            reachable=False,
            current_branch=self.current_branch(),
            target_branch=target_branch,
            dirty_files=self.pending_files()
        )

        try:
            # Именно --heads, а не HEAD: в пустом репозитории HEAD ещё не
            # существует, и проверка доступности провалилась бы на живом origin
            self._git('ls-remote', '--heads', 'origin', timeout=NETWORK_TIMEOUT)
            report.reachable = True
        except PublishError as e:
            reason = str(e).strip()
            report.unreachable_reason = reason or (
                'origin недоступен, git не объяснил причину. Обычно это права '
                'или сеть: проверьте доступ к GitLab'
            )
        except subprocess.TimeoutExpired:
            report.unreachable_reason = (
                f'origin не ответил за {NETWORK_TIMEOUT} с. Обычно это VPN или '
                'прокси: проверьте доступ к GitLab из этой сети'
            )

        if report.reachable:
            try:
                self._git('fetch', 'origin', timeout=NETWORK_TIMEOUT)
                report.fetched = True
            except (PublishError, subprocess.TimeoutExpired) as e:
                report.warnings.append(f'fetch не выполнен: {e}')

        remote_ref = f'origin/{target_branch}'
        counts = self._git('rev-list', '--left-right', '--count',
                           f'{remote_ref}...HEAD', check=False)
        parts = counts.split()
        if len(parts) == 2:
            report.behind, report.ahead = int(parts[0]), int(parts[1])
        else:
            report.warnings.append(
                f'не удалось сравнить с {remote_ref}: такой ветки нет ни '
                'локально, ни на origin'
            )

        if update:
            report.updated = self._update(report, target_branch)

        if branch:
            self._ensure_branch(report, branch, target_branch)

        return report

    def _update(self, report: SyncReport, target_branch: str) -> bool:
        """
        Подтянуть изменения без слияний.

        Args:
            report: Отчёт, куда пишутся предупреждения
            target_branch: Ветка назначения

        Returns:
            True, если обновление выполнено
        """
        if report.dirty_files:
            report.warnings.append(
                'в рабочем каталоге есть несохранённые изменения — '
                'обновление пропущено, чтобы их не потерять'
            )
            return False

        if not report.fetched:
            report.warnings.append('обновлять нечем: fetch не прошёл')
            return False

        if report.current_branch != target_branch:
            report.warnings.append(
                f'обновление пропущено: сейчас ветка {report.current_branch}, '
                f'а не {target_branch}'
            )
            return False

        try:
            self._git('merge', '--ff-only', f'origin/{target_branch}')
            return True
        except PublishError as e:
            report.warnings.append(
                f'fast-forward не прошёл ({e}); ветка разошлась с origin, '
                'решать это должен человек'
            )
            return False

    def _ensure_branch(
        self,
        report: SyncReport,
        branch: str,
        target_branch: str
    ) -> None:
        """
        Создать рабочую ветку или переключиться на неё.

        Args:
            report: Отчёт, куда пишется результат
            branch: Имя рабочей ветки
            target_branch: От чего ответвляться
        """
        if not re.fullmatch(r'[A-Za-z0-9._/-]{1,200}', branch):
            raise PublishError(
                f"Недопустимое имя ветки: {branch!r}. "
                "Разрешены латиница, цифры, точка, дефис, подчёркивание, слэш"
            )

        if self._git('branch', '--list', branch).strip():
            self._git('checkout', branch)
            report.branch_switched = branch
            # Отчёт читают как «где мы сейчас» и передают дальше в публикацию:
            # оставить здесь ветку до переключения — значит закоммитить в неё
            report.current_branch = branch
            return

        base = f'origin/{target_branch}' if report.fetched else 'HEAD'
        try:
            self._git('checkout', '-b', branch, base)
        except PublishError:
            # Ветки target может не быть на origin — тогда ответвляемся от того,
            # что есть сейчас
            self._git('checkout', '-b', branch)
            report.warnings.append(
                f'ветка {branch} создана от текущего состояния: '
                f'{base} недоступна'
            )

        report.branch_created = branch
        report.current_branch = branch

    def publish(
        self,
        branch: str,
        message: str,
        files: Optional[List[str]] = None,
        push: bool = True,
        merge_request_title: Optional[str] = None,
        merge_request_description: Optional[str] = None,
        target_branch: str = 'main',
        assignee: Optional[str] = None,
        reviewers: Optional[List[str]] = None,
        squash: bool = False,
        remove_source_branch: bool = True
    ) -> PublishResult:
        """
        Создать ветку, закоммитить, запушить и открыть merge request.

        Args:
            branch: Имя новой ветки
            message: Текст коммита
            files: Файлы для коммита; None — все изменённые
            push: Отправлять ли в удалённый репозиторий
            merge_request_title: Заголовок MR; None — MR не создаётся
            merge_request_description: Описание MR
            target_branch: Ветка назначения MR
            assignee: Кто отвечает за MR (username в GitLab)
            reviewers: Кто ревьюит (username в GitLab)
            squash: Схлопывать ли коммиты при мерже
            remove_source_branch: Удалять ли ветку после мержа

        Returns:
            PublishResult

        Raises:
            PublishError: если коммитить нечего или git вернул ошибку
        """
        if not re.fullmatch(r'[A-Za-z0-9._/-]{1,200}', branch):
            raise PublishError(
                f"Недопустимое имя ветки: {branch!r}. "
                "Разрешены латиница, цифры, точка, дефис, подчёркивание, слэш"
            )

        # Схема попадает в репозиторий через merge request, а не прямым
        # коммитом в ветку назначения: она обычно защищена, и push отвергнут
        # уже после того, как работа сделана
        if branch == target_branch:
            raise PublishError(
                f"Ветка публикации совпадает с веткой назначения ({branch}). "
                "Схема вносится через merge request из рабочей ветки — "
                "заведите её dochub_repo_sync(branch=...) и передайте сюда "
                "current_branch из его ответа"
            )

        result = PublishResult(branch=branch, committed=False, pushed=False)

        staged = files if files is not None else self.pending_files()
        if not staged:
            raise PublishError("Нет изменений для коммита")

        # Ветку создаём только если её ещё нет — повторный вызов не должен падать
        existing = self._git('branch', '--list', branch)
        if existing.strip():
            self._git('checkout', branch)
            result.warnings.append(f"ветка {branch} уже существовала")
        else:
            self._git('checkout', '-b', branch)

        for path in staged:
            self._git('add', '--', path)

        result.files = staged

        # Пустой коммит означал бы, что изменения уже внесены ранее
        if not self._git('diff', '--cached', '--name-only').strip():
            raise PublishError(
                "После добавления файлов индекс пуст — изменения уже закоммичены"
            )

        self._git('commit', '-m', message)
        result.committed = True
        result.commit_sha = self._git('rev-parse', 'HEAD')

        if not push:
            return result

        self._git('push', '-u', 'origin', branch)
        result.pushed = True

        if merge_request_title:
            try:
                result.merge_request_url = self._create_merge_request(
                    branch,
                    merge_request_title,
                    merge_request_description or '',
                    target_branch,
                    assignee=assignee,
                    reviewers=reviewers or [],
                    squash=squash,
                    remove_source_branch=remove_source_branch,
                    warnings=result.warnings
                )
            except PublishError as e:
                # Коммит и push уже состоялись — MR можно создать руками
                result.warnings.append(str(e))

        return result

    def remote_url(self) -> str:
        """URL удалённого репозитория."""
        return self._git('remote', 'get-url', 'origin')

    def _project_path(self) -> str:
        """
        Определить путь проекта в GitLab из URL remote.

        Returns:
            Путь вида "group/subgroup/project"
        """
        url = self.remote_url()

        if url.startswith('git@'):
            # git@host:group/project.git
            path = url.split(':', 1)[-1]
        else:
            path = urlparse(url).path

        return path.strip('/').removesuffix('.git')

    def _api_base(self) -> str:
        """
        Базовый адрес API GitLab.

        Raises:
            PublishError: если origin не ведёт в GitLab
        """
        url = self.remote_url()

        if url.startswith('git@'):
            host = url.split('@', 1)[-1].split(':', 1)[0]
            return f"https://{host}/api/v4"

        parsed = urlparse(url)

        # Локальная папка или диск вместо адреса: из неё получался бы
        # бессмысленный "c:///api/v4", и запрос падал бы непонятной ошибкой
        if parsed.scheme not in ('http', 'https') or not parsed.netloc:
            raise PublishError(
                f"origin ведёт не в GitLab: {url}. Ветка и коммит на месте, "
                "но merge request создавать негде"
            )

        return f"{parsed.scheme}://{parsed.netloc}/api/v4"

    def _user_id(self, token: str, username: str) -> Optional[int]:
        """
        Найти пользователя GitLab по username.

        Args:
            token: Токен API
            username: Имя пользователя

        Returns:
            Идентификатор или None, если такого нет
        """
        try:
            response = httpx.get(
                f"{self._api_base()}/users",
                headers={'PRIVATE-TOKEN': token},
                params={'username': username},
                timeout=30.0
            )
        except httpx.HTTPError:
            return None

        if response.status_code != 200:
            return None

        found = response.json()
        return found[0].get('id') if found else None

    def _create_merge_request(
        self,
        branch: str,
        title: str,
        description: str,
        target_branch: str,
        assignee: Optional[str] = None,
        reviewers: Optional[List[str]] = None,
        squash: bool = False,
        remove_source_branch: bool = True,
        warnings: Optional[List[str]] = None
    ) -> str:
        """
        Создать merge request через API GitLab.

        Args:
            branch: Исходная ветка
            title: Заголовок
            description: Описание
            target_branch: Ветка назначения
            assignee: Ответственный (username)
            reviewers: Ревьюверы (username)
            squash: Схлопывать коммиты при мерже
            remove_source_branch: Удалять ветку после мержа
            warnings: Список, куда писать некритичные замечания

        Returns:
            Ссылка на merge request

        Raises:
            PublishError: если нет токена или API вернул ошибку
        """
        warnings = warnings if warnings is not None else []
        token = next(
            (os.environ[name] for name in TOKEN_ENV_VARS if os.environ.get(name)),
            None
        )

        if not token:
            raise PublishError(
                "Ветка отправлена, но merge request не создан: нет токена. "
                f"Задайте переменную окружения {TOKEN_ENV_VARS[0]}. "
                "MR можно создать вручную по ссылке, которую вернул push."
            )

        project = quote(self._project_path(), safe='')
        url = f"{self._api_base()}/projects/{project}/merge_requests"

        payload: Dict[str, object] = {
            'source_branch': branch,
            'target_branch': target_branch,
            'title': title,
            'description': description,
            'remove_source_branch': remove_source_branch,
            'squash': squash
        }

        # Ассайни и ревьюверы задаются идентификаторами, а человек знает
        # только username. Не нашли — MR всё равно создаём: без ответственного
        # он живой, а без MR работа никуда не движется
        if assignee:
            found = self._user_id(token, assignee)
            if found:
                payload['assignee_id'] = found
            else:
                warnings.append(f'ответственный {assignee} в GitLab не найден')

        reviewer_ids = []
        for name in reviewers or []:
            found = self._user_id(token, name)
            if found:
                reviewer_ids.append(found)
            else:
                warnings.append(f'ревьювер {name} в GitLab не найден')

        if reviewer_ids:
            payload['reviewer_ids'] = reviewer_ids

        try:
            response = httpx.post(
                url,
                headers={'PRIVATE-TOKEN': token},
                json=payload,
                timeout=30.0
            )
        except httpx.HTTPError as e:
            raise PublishError(f"GitLab API недоступен: {e}")

        if response.status_code == 409:
            raise PublishError("Merge request для этой ветки уже существует")
        if response.status_code in (401, 403):
            raise PublishError(
                "GitLab отклонил токен: проверьте права (нужен scope api)"
            )
        if response.status_code >= 400:
            raise PublishError(
                f"GitLab вернул {response.status_code}: {response.text[:200]}"
            )

        return response.json().get('web_url', '')


def changed_paths(repo_path: Path, base: str, head: str = 'HEAD') -> List[str]:
    """
    Список файлов, изменённых между двумя ревизиями.

    Args:
        repo_path: Корень репозитория
        base: Базовая ревизия (ветка, тег, коммит)
        head: Сравниваемая ревизия

    Returns:
        Пути относительно корня репозитория

    Raises:
        PublishError: если git вернул ошибку
    """
    result = subprocess.run(
        [git_command(), 'diff', '--name-only', f'{base}...{head}'],
        cwd=Path(repo_path),
        capture_output=True,
        # См. Publisher._git: наследованный stdin MCP-сервера вешает git
        stdin=subprocess.DEVNULL,
        timeout=120
    )

    if result.returncode != 0:
        stderr = result.stderr.decode('utf-8', errors='replace').strip()
        raise PublishError(f"git diff {base}...{head}: {stderr}")

    return [
        line.strip()
        for line in result.stdout.decode('utf-8', errors='replace').splitlines()
        if line.strip()
    ]

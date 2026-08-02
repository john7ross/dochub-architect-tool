# Security Policy

**English** · [Русский](#политика-безопасности)

## Supported versions

Security fixes go into the latest released version. There are no long-term
support branches.

| Version | Supported |
|---|---|
| 0.2.x | ✅ |
| older | ❌ |

## Reporting a vulnerability

**Please do not open a public issue for a security problem.** A public report
tells everyone about the hole before there is a fix, and this server runs git
and Java on the operator's machine and can hold a GitLab token.

Two private channels, in order of preference:

1. **GitHub private advisory** — the *Security* tab of this repository →
   *Report a vulnerability*. It creates a discussion visible only to you and
   the maintainer, and it can turn into a published advisory once the fix
   ships.
2. **Email** — the address on the maintainer's GitHub profile
   ([@john7ross](https://github.com/john7ross)).

A regular GitHub issue is the right place for ordinary bugs, questions and
feature requests — just not for vulnerabilities.

### What to include

- what an attacker can do, and what they need to have first;
- steps to reproduce, ideally the smallest possible case;
- affected version or commit;
- your assessment of the impact, if you have one.

**Never attach a real `GITLAB_TOKEN`, an internal repository URL, or a company
architecture file.** A token with the `api` scope can read and write every
project its owner can reach. Redact hosts as `https://<gitlab>/<group>/<repo>`.

### What to expect

- acknowledgement within a few days;
- an assessment and a plan, or an explanation of why it is not a vulnerability;
- credit in the release notes when the fix ships, unless you prefer otherwise.

This is a personal open-source project, not a commercial product with an SLA.
There is no bug bounty.

## Threat model in one paragraph

The server does deterministic work on local files: it reads the diagrams and
the architecture repository whose paths the agent passes in, writes YAML back,
runs `git` in that repository and `java` to render PlantUML. Three things are
worth reporting. **The token**: `GITLAB_TOKEN` is read from the environment
only, is sent solely as a `PRIVATE-TOKEN` header to the GitLab API, and must
never appear in tool output, a log line or an error message. **The paths**: a
tool call carries file system paths, so anything that makes the server read or
write outside the repository the operator named is a finding. **The renderer**:
PlantUML executes the diagram it is given, and `!include` can pull in local
files and remote URLs — rendering a diagram from an untrusted source is
equivalent to running someone else's file with your privileges.

## Operating this server safely

- Keep `GITLAB_TOKEN` in the environment, never in a config file inside the
  project and never in a tool argument.
- Issue the token with the **minimum scope** the task needs (`api` is required
  only to create a merge request; without it the branch is still pushed).
- Publication asks for explicit confirmation on purpose: `dochub_publish`
  refuses to commit until it is called with `confirmed=true`. Do not automate
  that confirmation away.
- The preview server binds `127.0.0.1` and serves the schema and the generated
  YAML without authentication. It is meant for one workstation — do not expose
  the port to a network.
- Accumulated rules (`knowledge/lessons.yaml`), architecture repositories and
  service diagrams hold internal company data. They are gitignored, and they
  should not be copied around or attached to bug reports either.
- Rotate the token if it has ever appeared in a log, a screenshot, a chat
  message or a terminal recording.

---

# Политика безопасности

## Поддерживаемые версии

Исправления безопасности выходят в последней версии. Веток с долгосрочной
поддержкой нет.

| Версия | Поддерживается |
|---|---|
| 0.2.x | ✅ |
| старее | ❌ |

## Как сообщить об уязвимости

**Не открывайте публичный issue по проблеме безопасности.** Публичное
сообщение рассказывает о дыре всем раньше, чем появится исправление, а этот
сервер запускает git и Java на машине оператора и может держать токен GitLab.

Два приватных канала, по убыванию предпочтительности:

1. **Приватный advisory на GitHub** — вкладка *Security* этого репозитория →
   *Report a vulnerability*. Обсуждение видите только вы и сопровождающий, а
   после выхода исправления его можно опубликовать.
2. **Почта** — адрес указан в профиле сопровождающего на GitHub
   ([@john7ross](https://github.com/john7ross)).

Обычный issue — правильное место для рядовых багов, вопросов и предложений.
Но не для уязвимостей.

### Что приложить

- что может сделать атакующий и что ему для этого нужно;
- шаги воспроизведения, желательно минимальный случай;
- версия или коммит;
- ваша оценка последствий, если она есть.

**Никогда не прикладывайте настоящий `GITLAB_TOKEN`, адрес внутреннего
репозитория или файл архитектуры компании.** Токен со scope `api` читает и
пишет всё, что доступно его владельцу. Замажьте адреса как
`https://<gitlab>/<группа>/<репозиторий>`.

### Чего ждать в ответ

- подтверждение получения в течение нескольких дней;
- оценку и план либо объяснение, почему это не уязвимость;
- упоминание в описании релиза при выходе исправления, если вы не против.

Это личный open-source проект, а не коммерческий продукт с SLA. Вознаграждения
за найденные уязвимости нет.

## Модель угроз в одном абзаце

Сервер делает детерминированную работу с локальными файлами: читает схемы и
архитектурный репозиторий по путям, которые передал агент, пишет YAML, зовёт
`git` в этом репозитории и `java` для отрисовки PlantUML. Сообщать стоит о трёх
вещах. **Токен**: `GITLAB_TOKEN` берётся только из переменной окружения,
уходит единственным заголовком `PRIVATE-TOKEN` в API GitLab и не должен
появляться ни в выводе инструмента, ни в логе, ни в тексте ошибки. **Пути**: в
вызове инструмента приходят пути файловой системы, поэтому всё, что заставляет
сервер читать или писать вне названного оператором репозитория, — находка.
**Рендер**: PlantUML исполняет то, что ему дали, а `!include` умеет подтянуть и
локальный файл, и внешний адрес; отрисовать схему из недоверенного источника —
всё равно что запустить чужой файл со своими правами.

## Как эксплуатировать сервер безопасно

- Держите `GITLAB_TOKEN` в переменной окружения — не в конфиге внутри проекта
  и не в параметрах инструмента.
- Выдавайте токен с **минимально необходимым scope** (`api` нужен только для
  создания merge request; без него ветка всё равно уходит).
- Публикация требует явного подтверждения намеренно: `dochub_publish` не
  сделает коммит, пока его не вызовут с `confirmed=true`. Не автоматизируйте
  это подтверждение.
- Превью слушает `127.0.0.1` и отдаёт схему и сгенерированный YAML без
  авторизации. Это инструмент одной рабочей станции — не выставляйте порт в
  сеть.
- Накопленные правила (`knowledge/lessons.yaml`), архитектурные репозитории и
  схемы сервисов содержат внутренние данные компании. Они в `.gitignore`, и их
  не стоит ни копировать по машинам, ни прикладывать к сообщениям об ошибках.
- Перевыпустите токен, если он хоть раз попал в лог, скриншот, переписку или
  запись экрана.

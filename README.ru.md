# DocHub Architect Tool

[English](README.md) · **Русский**

MCP-сервер для переноса C4-схем из DrawIO и PlantUML в архитектурный
репозиторий DocHub.

Детерминированную работу делает код: разбор схем, сборка манифеста
репозитория, поиск компонентов для переиспользования, проверка ссылочной
целостности, рендеринг, запись YAML, публикация. Смысл — какую бизнес-функцию
отражает элемент, чей поддомен им владеет, как сценарии делятся на контексты —
даёт агент вместе с человеком, потому что из схемы это не выводится.

## Что он даёт

- **Разбор, который работает на настоящих файлах.** C4-атрибуты лежат на
  обёртках `<object>`, вложенность задана геометрией, а не атрибутом `parent`,
  страницы нередко хранятся сжатыми. Всё это учтено.
- **Отказ на логических диаграммах.** Sequence и activity описывают поведение,
  а не состав, и сервер говорит об этом прямо, а не достраивает по догадкам.
- **Рендеринг родной метамоделью DocHub**, взятой из его плагина, — контекст
  выглядит ровно так, как нарисует сам DocHub.
- **Превью в два окна**: слева оригинал схемы, справа построенная, и правая
  панель обновляется по мере правок YAML.
- **Правило владельца.** Очередь, метод чужого API или хранимая процедура
  описываются в файле того поддомена, которому принадлежат; свой файл только
  ссылается на них.
- **Исследование до вопросов.** Бриф читает схему, доменную модель,
  репозиторий и накопленные правила, и спрашивается только то, чего нет ни в
  одном из них, — со ссылкой на первоисточник.
- **Репозиторий проверяется до работы, а не после.** Доступен, не отстал,
  рабочая ветка заведена — вместо расхождения, которое обнаружится при push.
- **Накопление знаний.** Решения, подтверждённые пользователем, сохраняются и
  переиспользуются, чтобы не объяснять одни и те же соглашения каждый раз.

## Что нужно

| Что | Зачем | Если нет |
|-----|-------|----------|
| Python 3.10+ | сам сервер | обязателен |
| Java 11+ | рендеринг PlantUML | превью и рендер контекстов не работают |
| `lib/plantuml.jar` | рендеринг | скачивается setup_assets.py |
| `vendor/dochub/` | метамодель DocHub | идёт в комплекте |
| `vendor/viewer-static.min.js` | отрисовка оригинала DrawIO | левая панель превью останется пустой |
| `lib/elk/` | раскладка ELK | откат на smetana, схемы шире |
| `GITLAB_TOKEN` | создание merge request | ветка уйдёт, MR создаётся вручную |

## Установка

Если на машине ничего не установлено — соберите портативный архив: `python
tools/build_release.py` кладёт внутрь свой Python, Java и git, и получателю
ставить нечего. Только Windows x64.

Из исходников:

```bash
pip install -r requirements.txt
python setup_assets.py
copy .env.example .env
```

Вторая команда скачивает PlantUML и просмотрщик drawio. Они весят десятки
мегабайт и в git не хранятся; после этого всё работает офлайн.
`python setup_assets.py --check` показывает, что уже на месте.

## Настройка

Вся настройка — в `.env`, образец с пояснениями лежит в `.env.example`.
Заполнить нужно пути: репозиторий и доменную схему. У остального есть рабочие
умолчания:

```ini
DOCHUB_REPO_ROOT=C:/repos/architectural-repository
DOCHUB_DDD_PATH=C:/repos/architectural-repository/ddd.drawio   # или ссылка
GITLAB_TOKEN=                       # нужен только для merge request
DOCHUB_REMOTE_PROTOCOL=https        # или ssh — чего ждёт ваш origin
DOCHUB_TARGET_BRANCH=main
DOCHUB_COMMIT_TEMPLATE=Добавление схемы {schema}
DOCHUB_MR_ASSIGNEE=                 # кто отвечает за merge request
DOCHUB_AUTOMODE=false               # агент сам доводит до MR
```

Одноимённая переменная окружения перебивает файл — клиент может переопределить
одно значение, ничего не редактируя. `dochub_workspace` показывает итог и то,
откуда взято каждое значение. Полная таблица — в
[docs/USAGE.ru.md](docs/USAGE.ru.md).

**automode** — режим для тех, кто не работает с архитектурными репозиториями:
агент закрывает бриф умолчаниями и накопленными правилами, строит схему,
показывает превью и доводит работу до merge request, не спрашивая
подтверждения. Мерж автоматическим не бывает никогда — это решение человека.

## Подключение к MCP-клиенту

Сервер общается по MCP через stdio. Клиенты обычно держат серверы в объекте
`mcpServers`:

```json
{
  "mcpServers": {
    "dochub": {
      "command": "python",
      "args": ["-m", "app.mcp_server"],
      "cwd": "<путь к проекту>"
    }
  }
}
```

Вспомогательный скрипт покажет или пропишет этот фрагмент, не трогая уже
подключённые серверы:

```bash
python skills/dochub-architect/tools/install_mcp.py --print
```

## Быстрая проверка

```bash
python -m app.mcp_server
```

Сервер пишет строку запуска в stderr и ждёт ввода — в stdout идёт протокол, и
ничего постороннего туда попадать не должно. Прервать: Ctrl+C.

```bash
python -m pytest
```

## Документация

- [ARCHITECTURE.ru.md](ARCHITECTURE.ru.md) — компоненты, технологии, диаграммы
- [ROADMAP.ru.md](ROADMAP.ru.md) — состояние и границы
- [docs/USAGE.ru.md](docs/USAGE.ru.md) — инструменты и как они складываются
- [skills/dochub-architect/](skills/dochub-architect/) — скилл, задающий агенту
  порядок работы; идёт вместе с проектом
- [workflows/](workflows/README.ru.md) — фаза исследования как параллельный
  прогон для Claude Code, необязательно
- [SECURITY.md](SECURITY.md#политика-безопасности) — модель угроз и как
  сообщить об уязвимости

## Поддержать автора

<p align="center">
  <img src="donate-qr.png" alt="Donate QR" width="200"/>
</p>

BTC: bc1q3frrup5neh7nhfg944etu2agd4j9u0vg3jyee6

ETH(Arbitrum): 0x43B349d8Cea83215D707EBa3bc35e9917f746b0a

TRX: THSzvy49KNeqRjXsGkurh2A5G4avV4RgN4

XRP: rLWZjS3DMupC4ZdXCX3BVYn4dEtC3iNhgy

SOL: 3xwfybxJ6Tz5t6pjBBkL5yYQCZo6wfbv932UNA4ThdP8

ADA: addr1q926ys75jp5wn2pv32a3t8r8pdhr7w02v0t9j4a8pmg0ruww5rlkctu4lnz2hfcwa5qfn3zhsd0s23r22uqwzx9gu6cq5c4e76

TON: UQC4qlAOD9Nly4K_66GJ_yCsSM3x2sB0vZ2GrBQbc--gZUui

DOGE: DTjNYmbtymzcjUiV4MsZY8MP4dM7MJ6qLC

XMR: 44qRqM6YtnxXUhkgCFqDDrKMPjWriu69FLBoop8Kwp7e1VQsBUJoVQ8JYQjfMV5C6uidTUgSSyoJ65mq8aYG2esZ1rrqfwt

## Лицензия

MIT, см. [LICENSE](LICENSE). Что проект распространяет вместе с собой и под
какими лицензиями: [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).

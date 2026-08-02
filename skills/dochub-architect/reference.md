# DocHub — справочник: валидатор, рендер, entity

Дополнение к [SKILL.md](SKILL.md). Полная версия — `dochub-schema-guide.md`
в корне арх.репозитория.

Большинство перечисленных ниже ошибок ловится до открытия DocHub:

```
dochub_validate_context(yaml_path, context_id, manifest_path)
```

---

## Валидатор DocHub

### Аспект верхнего уровня не определён

**Причина:** id `a.b.c`, но нет аспекта `a` или `a.b`.

**Решение:** объявить родителя в `aspects:` того же или корневого yaml.

```yaml
aspects:
  skywalking:
    title: SkyWalking
    location: SkyWalking
  skywalking.network:
    title: Сетевые связи
    location: SkyWalking/Network
```

---

### Компонент верхнего уровня не определён

**Причина:** id `python.voiceinsight`, но нет компонента `python`.

**Решение:** в `architecture/common/general/root.yaml`:

```yaml
components:
  python:
    title: Python
    entity: component
```

И в context `platforms` → `components: [ python, ... ]`.

**Корни уже в репо:** `mssql`, `dotnet`, `postgresql` — `general/root.yaml`; `externalServices` — `common/external/root.yaml`.

---

### Компоненты вне контекста

**Причина:** компонент объявлен, но ни в одном `contexts.components`.

**Решение:**
1. Контекст сценария, где участвует компонент.
2. Каталожный контекст владельца (напр. `collectionAgencyL3.yaml` для `mssql.catalogSchema` + `mssql.catalogSchema.*`).

Wildcard `.*` **не всегда** покрывает родителя-sхему (`entity: database`) — указывай явно.

---

### Несуществующие компоненты в контексте

- Опечатка в id
- Файл не в `architecture/dochub.yaml` → `imports:`

---

### YAML syntax (блокирует рендер)

```yaml
# ПЛОХО — Nested mappings are not allowed
title: GET : /api/foo

# ХОРОШО
title: /api/foo
summary: GET — описание
```

---

## Рендер диаграмм

| Симптом | Причина | Fix |
|---------|---------|-----|
| Все методы API | L2-контроллер в `components` | L3-метод |
| Неверное имя сервиса | `title` L2 устарел | Исправить `dotnet.*.title` |
| Неверная ХП | Угадали по имени | Код + appsettings |
| Syntax Error | `rectangle` в `$after` | Только `-->` между id |
| Лишние блоки | `extra-links: true` | `extra-links: false` |

`extra-links: false` скрывает **чужие** компоненты контекста, но **не** дочерние аспекты L2.

---

## entity и поля компонентов

| entity | Когда | Поля заголовка |
|--------|-------|----------------|
| `component` | Сервис, класс, API, ХП | dotnet: `server`, `container` |
| `database` | Схема БД, инстанс PG | `server`, `base`, `schema` |
| `person` / `actor` | Роли | — |
| `folder` | FileStorage | — |

Сводка (dotnet/mssql/postgresql): `team`, `prodact`, `summary`.

---

## API-каталог

Структура:
```
dotnet.catalogApi              # L2, title: Catalog.Api
  dotnet.catalogApi.methodGet    # L3, один аспект
```

L3 для нового метода (в чужом yaml с согласования):
```yaml
dotnet.someApi.controller.methodName:
  title: api/path/{id}/action
  summary: POST — описание
  aspects:
    - domain.context.api.controller.methodName
```

---

## MSSQL / PostgreSQL

**Перед созданием обязательно ищи существующий компонент:**
```
dochub_search_components(manifest_path, query="mssql.MySchema")
```

Компонент схемы:
```yaml
mssql.mySchema:
  title: Схема MySchema
  entity: database
  schema: MySchema
  aspects:
    - domain.context.mySchema
```

ХП:
```yaml
mssql.mySchema.myProc:
  title: MySchema.MyProc
  entity: component
  aspects:
    - domain.context.mySchema.myProc
```

---

## externalServices

Определяются в `architecture/common/external/root.yaml`. В поддомене — только ссылка в `contexts.components`:

```yaml
- externalServices.partnerName
```

Новый партнёр → добавить в `common/external/root.yaml` + context `externalServices`.

---

## Принадлежность компонентов

Компонент и его аспекты описываются **в файле владельца**, а не у того, кто
их использует. В своём поддомене — только ссылка в `contexts.components`
и в `uml.$after`.

```yaml
# У ВЛАДЕЛЬЦА (architecture/domain/IT/domainConnect/eventBus.yaml)
components:
  dotnet.eventBus.applicationRefusedForOrderService:
    title: order-placed.order-service
    entity: queue

# У СЕБЯ (orderFlow.yaml) — только используем
contexts:
  sales.orders.orderFlow.orderEventReceive:
    components:
      - dotnet.eventBus.applicationRefusedForOrderService
    uml:
      $after: |
        dotnet.eventBus.applicationRefusedForOrderService --> dotnet.orderService.applicationRefusedEventReceive
```

Правило действует, даже если сущность заведена специально под наш сервис.

Найти владельца: `dochub_locate_owner(repo_root, component_ids)`.

---

## Что обычно НЕ включать в схему

- Healthcheck, swagger, каждый DTO
- Телеметрию/контейнеризацию — если пользователь не просил
- Дубли pipeline на L3 API-каталоге

**Включать:** бизнес-pipeline, интеграции, БД, операционные дашборды (Grafana), если это реальная настройка.

---

## MR description (шаблон)

```markdown
## Summary
- Схема <ServiceName>: аспекты, L2/L3, контексты
- Интеграции: ...
- Валидатор DocHub: 0 отклонений

## Test plan
- [ ] Валидатор без отклонений
- [ ] Контексты открываются и рендерятся
- [ ] На диаграммах только нужные API/ХП
```

---

## Структура репозитория (entry points)

```
dochub.yaml → root.yaml → architecture/dochub.yaml
architecture/plantuml.yaml   # renderCore: elk | smetana
architecture/entity.yaml     # метамодель
README.md                    # официальное именование
.cursor/rules/dochub-*.mdc   # правила Cursor в репо
```

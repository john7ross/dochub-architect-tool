export const meta = {
  name: 'dochub-mr-review',
  description: 'Ревью чужого merge request в архитектурном репозитории: веером по изменённым сущностям, каждая находка проверяется состязательно, в отчёт попадает только подтверждённое.',
  whenToUse: 'Когда пришёл MR в арх.репозиторий и нужно понять, что в нём не так: ссылочная целостность, регистрация, владение компонентами, именование. Требует подключённого MCP-сервера dochub.',
  phases: [
    { title: 'Что изменилось', detail: 'сущности и файлы ветки' },
    { title: 'Разбор', detail: 'агент на файл: проверки и правила' },
    { title: 'Проверка', detail: 'состязательная: находка либо доказана, либо снята' },
    { title: 'Отчёт', detail: 'только подтверждённое, со ссылкой на файл и правило' },
  ],
}

// КАК ПЕРЕДАТЬ ДАННЫЕ:
//   args = { base: "origin/main", head: "feature/orders" }
//   args = { repo: "C:/repos/architectural-repository", base: "main", head: "HEAD", mr: "<ссылка на MR>" }
//
// repo можно не передавать: он в .env сервера.
const input = typeof args === 'string' ? { head: args } : (args || {})
const repo = input.repo || ''
const base = input.base || 'origin/main'
const head = input.head || 'HEAD'
const mr = input.mr || ''

const TOOLS = `Инструменты MCP-сервера dochub загрузи через ToolSearch по запросу "dochub" (max_results 25).
Если ToolSearch ничего не вернул — сервер не подключён к сессии: скажи об этом и не выдумывай ответ.`

const CHANGES = {
  type: 'object',
  properties: {
    ok: { type: 'boolean' },
    files: {
      type: 'array',
      description: 'изменённые YAML-файлы репозитория',
      items: { type: 'string' },
    },
    added: {
      type: 'array',
      description: 'добавленные сущности: компоненты, аспекты, контексты',
      items: { type: 'string' },
    },
    changed: {
      type: 'array',
      description: 'изменённые сущности',
      items: { type: 'string' },
    },
    problem: { type: 'string' },
  },
  required: ['ok', 'files'],
}

const FINDINGS = {
  type: 'object',
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          title: { type: 'string', description: 'суть замечания одной строкой' },
          where: { type: 'string', description: 'файл и сущность' },
          kind: { type: 'string', description: 'целостность / регистрация / владение / именование / рендер' },
          detail: { type: 'string', description: 'что именно не так и чем это подтверждается' },
          fix: { type: 'string', description: 'что сделать автору' },
        },
        required: ['title', 'where', 'kind'],
      },
    },
  },
  required: ['findings'],
}

const VERDICT = {
  type: 'object',
  properties: {
    real: { type: 'boolean', description: 'замечание подтверждается инструментами' },
    reason: { type: 'string', description: 'чем подтверждается или почему снято' },
  },
  required: ['real', 'reason'],
}

phase('Что изменилось')

const changes = await agent(
  `# Роль
Ты смотришь, что меняет ветка в архитектурном репозитории.

${TOOLS}

# Задача
Вызови dochub_changed_entities: base="${base}", head="${head}"${repo ? `, repo_path="${repo}"` : ' (repo_path возьмётся из настроек)'}.

Верни изменённые YAML-файлы и списки добавленных и изменённых сущностей —
компонентов, аспектов, контекстов. Идентификаторы бери ровно те, что вернул
инструмент.`,
  { label: 'diff', phase: 'Что изменилось', schema: CHANGES }
)

if (!changes || !changes.ok || !changes.files.length) {
  const problem = (changes && changes.problem) || 'ветка не меняет YAML-файлов репозитория'
  log(`Разбирать нечего: ${problem}`)
  return { base, head, files: 0, problem }
}

log(`Изменено файлов: ${changes.files.length}, сущностей: ${changes.added.length + changes.changed.length}`)

phase('Разбор')

let files = changes.files
const MAX_FILES = 8
if (files.length > MAX_FILES) {
  log(`Файлов ${files.length}: разбираю первые ${MAX_FILES}, остальные остаются на человеке.`)
  files = files.slice(0, MAX_FILES)
}

// Разбор и состязательная проверка идут конвейером: файл, разобранный первым,
// сразу уходит на проверку и не ждёт остальных
const reviewed = await pipeline(
  files,
  (file) => agent(
    `# Роль
Ты ревьюишь изменения архитектурного репозитория DocHub. Ищешь то, что сломает
репозиторий или разойдётся с принятыми правилами, а не «мне бы иначе».

${TOOLS}

# Файл
${file}
${mr ? `Merge request: ${mr}` : ''}

# Что проверить
1. dochub_validate_context по файлу — ссылочная целостность: неизвестные
   компоненты, недостающие аспекты, связи наружу контекста.
2. dochub_check_registration — подключён ли файл к дереву, объявлены ли
   корневые платформы, родительские аспекты и внешние сервисы.
3. dochub_render_context — каждый контекст должен отрисовываться.
4. dochub_locate_owner по добавленным компонентам — описан ли компонент у
   владельца. Очередь шины, метод чужого API или хранимая процедура,
   заведённые в своём файле вместо файла владельца, — это находка.
5. dochub_search_components — нет ли дубля того, что уже описано под другим
   именем.
6. dochub_lessons — накопленные правила: именование, что выносится на
   диаграмму. Расхождение с правилом, подтверждённым не раз, — находка.

# Чего не делать
Не придирайся к бизнес-названиям и к тому, как автор разбил на контексты: это
его решение. Замечание без подтверждения инструментом не находка, а мнение —
такие не возвращай.`,
    { label: `разбор ${file.split('/').pop()}`, phase: 'Разбор', schema: FINDINGS }
  ),
  (review, file) => parallel((review && review.findings ? review.findings : []).map((f) => () =>
    agent(
      `# Роль
Ты снимаешь ложные замечания. По умолчанию считай замечание неподтверждённым,
пока сам не увидел подтверждение инструментом.

${TOOLS}

# Замечание
Файл: ${file}
Где: ${f.where}
Тип: ${f.kind}
Суть: ${f.title}
Обоснование автора замечания: ${f.detail || 'не приведено'}

# Задача
Проверь сам, теми же инструментами. Подтверждай, только если инструмент
действительно жалуется или правило действительно нарушено. Если замечание —
вкусовщина, дубль другого замечания или следствие того, что файл ещё не
подключён к дереву (и это отмечено отдельно), сними его.`,
      { label: `проверка: ${f.title.slice(0, 40)}`, phase: 'Проверка', schema: VERDICT }
    ).then((v) => ({ ...f, file, verdict: v }))
  ))
)

const confirmed = reviewed
  .flat()
  .filter(Boolean)
  .filter((f) => f.verdict && f.verdict.real)

log(`Подтверждённых замечаний: ${confirmed.length}`)

phase('Отчёт')

const report = await agent(
  `# Роль
Ты пишешь ревью merge request в архитектурный репозиторий — автору, а не в стол.

# Данные

Ветка: ${head}, база: ${base}${mr ? `\nMerge request: ${mr}` : ''}
Файлов разобрано: ${files.length} из ${changes.files.length}
Добавлено сущностей: ${changes.added.length}, изменено: ${changes.changed.length}

## Подтверждённые замечания (${confirmed.length})
${confirmed.map((f) => `- [${f.kind}] ${f.title}
  где: ${f.where} (${f.file})
  чем подтверждается: ${f.verdict.reason}
  что сделать: ${f.fix || 'не предложено'}`).join('\n') || 'нет'}

# Задача
Собери ревью на русском в markdown:
1. одной фразой — можно ли мержить как есть;
2. блокирующее: то, что сломает репозиторий или валидатор DocHub;
3. несрочное: именование и расхождения с накопленными правилами;
4. если разобраны не все файлы — прямо скажи, какие остались непроверенными.

Формулируй как коллеге: что не так, чем подтверждается, что сделать. Хвалить
не нужно, придумывать замечания сверх списка — тем более.`,
  { label: 'ревью', phase: 'Отчёт' }
)

return {
  base,
  head,
  files_changed: changes.files.length,
  files_reviewed: files.length,
  findings: confirmed.length,
  report,
}

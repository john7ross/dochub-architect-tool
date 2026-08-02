export const meta = {
  name: 'dochub-batch',
  description: 'Пакетный перенос схем: по агенту на схему — бриф, черновик DocHub YAML, проверка; запись в репозиторий и merge request — по одной схеме за раз.',
  whenToUse: 'Когда на руках несколько схем сервисов и нужно довести их до черновиков с отчётом. Требует подключённого MCP-сервера dochub.',
  phases: [
    { title: 'Черновики', detail: 'агент на схему: бриф, черновик, проверка' },
    { title: 'Публикация', detail: 'последовательно: репозиторий один на всех' },
    { title: 'Отчёт', detail: 'что готово, что требует человека' },
  ],
}

// КАК ПЕРЕДАТЬ ДАННЫЕ:
//   args = { schemas: ["C:/schemas/a.drawio", "C:/schemas/b.drawio"], out: "C:/drafts" }
//
//   Чтобы дошло до merge request, у схемы должно быть известно место в
//   репозитории — тогда вместо строки передаётся объект:
//   args = { schemas: [
//     { schema: "C:/schemas/a.drawio", yaml: "<repo>/architecture/domain/sales/orders/orderFlow.yaml",
//       domain: "sales", context: "orders", subdomain: "orderFlow", own_root: "dotnet.orderService" }
//   ], publish: true }
//
//   publish=true работает только при включённом automode в настройках сервера:
//   без него dochub_publish откажется публиковать без подтверждения человека.
const input = Array.isArray(args) ? { schemas: args } : (args || {})
const out = input.out || ''
const publish = input.publish === true
let items = (input.schemas || []).map((s) => (typeof s === 'string' ? { schema: s } : s))

if (!items.length) {
  log('Передай схемы: args = { schemas: ["C:/schemas/a.drawio", "C:/schemas/b.drawio"] }')
  return { error: 'нет схем' }
}

const MAX = 10
if (items.length > MAX) {
  log(`Схем ${items.length}: беру первые ${MAX}, остальные запусти вторым прогоном.`)
  items = items.slice(0, MAX)
}

const TOOLS = `Инструменты MCP-сервера dochub загрузи через ToolSearch по запросу "dochub" (max_results 25).
Если ToolSearch ничего не вернул — сервер не подключён к сессии: верни ok=false и скажи об этом, ничего не выдумывая.`

const DRAFT = {
  type: 'object',
  properties: {
    ok: { type: 'boolean', description: 'черновик собран и проверен' },
    schema: { type: 'string' },
    physical: { type: 'boolean', description: 'схема физическая и в нотации C4' },
    yaml_path: { type: 'string', description: 'куда записан черновик' },
    placement: { type: 'string', description: 'домен.контекст.поддомен, если определилось' },
    own_root: { type: 'string', description: 'какой сервис описывает схема' },
    elements: { type: 'number' },
    reused: {
      type: 'array',
      description: 'что уже есть в репозитории и переиспользуется',
      items: { type: 'string' },
    },
    problems: {
      type: 'array',
      description: 'что нашла проверка: ссылочная целостность, регистрация',
      items: { type: 'string' },
    },
    questions: {
      type: 'array',
      description: 'что осталось за человеком',
      items: { type: 'string' },
    },
  },
  required: ['ok', 'schema'],
}

const PUBLISHED = {
  type: 'object',
  properties: {
    ok: { type: 'boolean' },
    schema: { type: 'string' },
    branch: { type: 'string' },
    commit: { type: 'string', description: 'сообщение коммита' },
    merge_request: { type: 'string', description: 'ссылка на MR, если создан' },
    skipped: { type: 'string', description: 'почему пропущено, если пропущено' },
    own_subdomains: {
      type: 'array',
      description: 'свои файлы поддоменов, в которые вносятся правки',
      items: { type: 'string' },
    },
    foreign_subdomains: {
      type: 'array',
      description: 'чужие поддомены, затронутые схемой: требуют согласования',
      items: { type: 'string' },
    },
  },
  required: ['ok', 'schema'],
}

phase('Черновики')

// Чтение и сборка черновиков независимы: репозиторий здесь только читается
const drafts = await parallel(items.map((item, index) => () => agent(
  `# Роль
Ты переносишь схему сервиса в архитектурный репозиторий DocHub. Твоя часть —
структура: разобрать схему, собрать черновик YAML и проверить его. Смысловые
решения (бизнес-названия аспектов, разбиение на сценарии) не твои: что не
выводится из первоисточников — уходит в вопросы.

${TOOLS}

# Схема
${item.schema}

# Порядок
1. dochub_brief со schema_path — факты, место в доменной модели, открытые вопросы.
   Если в ответе physical=false — верни ok=false, physical=false и problem, дальше не работай.
2. dochub_draft_from_schema — черновик. Параметры передаются плоско, не в params:
   schema_path, domain, context, subdomain${item.own_root ? ', own_root' : ''}.
${item.domain
    ? `   Место известно: domain="${item.domain}", context="${item.context}", subdomain="${item.subdomain}".`
    : `   Место возьми из placement_candidates брифа. Если кандидатов не один —
   верни ok=false и вопрос человеку: угадывать поддомен нельзя.`}
${item.own_root ? `   own_root="${item.own_root}".` : `   own_root — рамка верхнего уровня, описывающая сам сервис; если рамок несколько и
   выбор неочевиден, это вопрос человеку.`}
3. Запиши черновик в ${item.yaml ? `файл ${item.yaml}` : (out ? `каталог ${out}` : 'файл рядом со схемой, с расширением .dochub.yaml')}.
   Инструмент возвращает YAML текстом и сам ничего не пишет.
4. dochub_validate_context и dochub_check_registration по записанному файлу.
   Всё, на что они пожаловались, собери в problems — своими словами, коротко.
5. Верни, что из схемы уже есть в репозитории (reused из брифа): это
   переиспользуется, а не создаётся заново.

Ничего не додумывай: чего нет ни на схеме, ни в репозитории, ни в правилах —
это вопрос, а не догадка.`,
  { label: `схема ${index + 1}/${items.length}`, phase: 'Черновики', schema: DRAFT }
)))

const ready = drafts.filter(Boolean).filter((d) => d.ok)
const failed = drafts.filter(Boolean).filter((d) => !d.ok)
log(`Черновиков собрано: ${ready.length} из ${items.length}`)

phase('Публикация')

const published = []

if (!publish) {
  log('publish не запрошен: черновики собраны, запись в репозиторий за человеком.')
} else {
  // Репозиторий один на всех: две ветки одновременно завести нельзя,
  // поэтому здесь никакого parallel — строго по одной схеме
  for (let i = 0; i < ready.length; i += 1) {
    const draft = ready[i]
    const item = items.find((it) => it.schema === draft.schema) || {}

    if (!item.yaml) {
      published.push({
        ok: false, schema: draft.schema,
        skipped: 'не передан yaml — путь файла поддомена в репозитории; без него неизвестно, куда класть схему',
      })
      continue
    }

    const result = await agent(
      `# Роль
Ты публикуешь готовую схему в архитектурный репозиторий.

${TOOLS}

# Схема
${draft.schema} -> ${item.yaml}
Название для сообщения коммита: ${item.name || draft.own_root || 'схема сервиса'}

# Порядок
1. dochub_repo_sync с update=true и branch — имя ветки собери из префикса в
   настройках и названия схемы, латиницей.
2. dochub_register_schema с apply=false — план. В ответе есть own_subdomains и
   foreign_subdomains: это и есть ответ на вопрос, в какие поддомены вносятся
   правки и на какие распространяется схема.
   Если foreign_subdomains не пуст — apply не делай: верни ok=false, а в
   skipped перечисли эти поддомены поимённо. Правка чужого файла требует
   согласования с владельцем, фоновый агент такое решение не принимает.
3. Если чужого нет — dochub_register_schema с apply=true.
4. dochub_publish: repo_path, branch, schema_name (сообщение соберётся по
   шаблону из настроек), merge_request_title. confirmed не передавай — в
   automode инструмент опубликует сам, а без automode откажется, и это
   правильный отказ: верни его текст как skipped.

Ничего не сливай и не мержи: merge request — конечная точка.`,
      { label: `публикация ${i + 1}/${ready.length}`, phase: 'Публикация', schema: PUBLISHED }
    )

    published.push(result || { ok: false, schema: draft.schema, skipped: 'агент не ответил' })
  }
}

phase('Отчёт')

const report = await agent(
  `# Роль
Ты собираешь отчёт по пакетному переносу схем для архитектора.

# Данные

## Черновики готовы (${ready.length})
${ready.map((d) => `- ${d.schema}
  место: ${d.placement || 'не определено'}, сервис: ${d.own_root || 'не определён'}, элементов: ${d.elements || '?'}
  файл: ${d.yaml_path || 'не записан'}
  переиспользовано: ${(d.reused || []).join(', ') || 'ничего'}
  замечания проверок: ${(d.problems || []).join('; ') || 'нет'}
  вопросы: ${(d.questions || []).join('; ') || 'нет'}`).join('\n')}

## Не собрано (${failed.length})
${failed.map((d) => `- ${d.schema}: ${(d.problems || []).join('; ') || 'причина в журнале прогона'}`).join('\n') || 'нет'}

## Публикация
${publish ? published.map((p) => `- ${p.schema}: ${p.ok ? `ветка ${p.branch}, ${p.merge_request || 'MR не создан'}` : `пропущено — ${p.skipped}`}
  свои поддомены: ${(p.own_subdomains || []).join(', ') || 'не определены'}
  чужие поддомены: ${(p.foreign_subdomains || []).join(', ') || 'нет'}`).join('\n') : 'не запрашивалась'}

# Задача
Собери отчёт на русском в markdown, в таком порядке:
1. что готово и куда положено;
2. **какие поддомены затронуты** — отдельным списком: свои и чужие. Чужие
   назови поимённо и скажи, что они требуют согласования с владельцами;
3. что требует решения человека — вопросы и замечания проверок, сгруппированные
   по смыслу, а не по схемам: одинаковые вопросы к разным схемам объединяй;
4. что не получилось и почему.

Не придумывай выводов, которых нет в данных, и не предлагай бизнес-названия
аспектов — это работа человека.`,
  { label: 'отчёт', phase: 'Отчёт' }
)

return {
  total: items.length,
  drafted: ready.length,
  failed: failed.map((d) => d.schema),
  published: publish ? published : null,
  report,
}

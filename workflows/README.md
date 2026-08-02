# Workflows

**English** · [Русский](README.ru.md)

Scripts for Claude Code's `Workflow` tool. They are optional, and there are
deliberately few of them: a workflow earns its place only where the work is
**judgement or a whole pipeline**, not where it is a lookup.

Parallelising lookups with agents is the wrong fix. Searching the repository
for every element of a diagram used to take about a minute one call at a time;
the answer was not a fan-out but `dochub_search_components` accepting a list of
queries — one manifest build for all of them, a second instead of a minute,
deterministic, and available in every MCP client rather than in Claude Code
alone.

## dochub-batch

Several schemas at once: one agent per schema — brief, draft, validation,
registration check — and a report grouped by decision rather than by schema, so
the same question asked of five schemas is answered once.

```
Workflow(name: "dochub-batch", args: {
  schemas: ["C:/schemas/a.drawio", "C:/schemas/b.drawio"],
  out: "C:/drafts"
})
```

To go all the way to a merge request, a schema needs a known place in the
repository — pass an object instead of a string:

```
Workflow(name: "dochub-batch", args: {
  schemas: [{
    schema: "C:/schemas/a.drawio",
    yaml:   "<repo>/architecture/domain/sales/orders/orderFlow.yaml",
    domain: "sales", context: "orders", subdomain: "orderFlow",
    own_root: "dotnet.orderService"
  }],
  publish: true
})
```

`publish` only works with `automode` on: without it `dochub_publish` refuses to
act without a human confirmation, and that refusal is reported as the reason
the schema was skipped.

**Reading fans out, writing does not.** Drafts are built in parallel, but the
repository is one for all of them: two branches cannot be created at the same
time, so registration and publication run one schema after another.

The report lists **which subdomains are touched**, own and foreign, separately.
A plan that reaches into a foreign subdomain stops that schema and names it:
editing someone else's file is their responsibility and their review, and that
decision belongs to a person, not to a background agent.

## When to run them

Ask, or wait for the agent to offer: the skill tells it to mention the ready
scripts when the task looks like one of them and to **ask permission first** —
a workflow spins up several agents, which costs real time and money. A single
schema with an agreement loop needs no workflow at all.

## dochub-mr-review

Reviews someone else's merge request in the architecture repository: what the
branch changes, then one agent per file — referential integrity, registration,
ownership, naming against the accumulated rules — and every finding is checked
by a second, sceptical agent. Only what survives reaches the report.

```
Workflow(name: "dochub-mr-review", args: {
  base: "origin/main",
  head: "feature/orders",
  mr:   "<link to the merge request>"     # optional, for the report
})
```

Taste is out of scope: business names and how the author split the diagram into
contexts are their decisions, and a finding no tool confirms is an opinion, not
a finding.

## Install

The Workflow tool reads scripts from `~/.claude/workflows/`:

```bash
cp workflows/*.js ~/.claude/workflows/
```

The MCP server must be connected in the session: subagents look its tools up
through `ToolSearch`. If it is not connected, the run stops and says so instead
of inventing an answer.

## What is deliberately not a workflow

The agreement loop — preview, comments, corrections — stays in the main
conversation. A workflow runs in the background and cannot ask a question
halfway through, and the whole point of that loop is the dialogue.

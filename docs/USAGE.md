# Usage

**English** · [Русский](USAGE.ru.md)

The server exposes 20 tools. Two of them change state — the repository sync
and the publication; everything else is read-only.

## Order of work

The skill in `skills/dochub-architect/` describes the order an agent should
follow. The short version:

1. read the settings and the accumulated rules, ask what is missing;
2. sync the repository: is it reachable, is it current, create the work branch;
3. collect the brief — research first, so only what is genuinely unknown gets
   asked;
4. check that the diagram is a physical C4 one;
5. if sources are given — reconcile the diagram with the code;
6. build a draft, look for components to reuse;
7. show the preview and iterate on comments;
8. wire the file into the tree and into the owners' files;
9. publish after an explicit confirmation;
10. record what was learned.

## Tools

### Settings and knowledge

**`dochub_workspace`** — everything this installation is configured with: the
paths that do not change between tasks, the GitLab defaults (target branch,
commit template, merge request parameters) and whether `automode` is on. It
also reports where each value came from — `.env`, an environment variable or
the saved paths — and what is still missing.

**`dochub_brief`** — the research, done before the questions. The diagram, the
domain model, the repository and the accumulated rules are read, and what comes
back is the list of questions nothing answers, each carrying the source it came
from. With `automode` on, every question carries the default that will be
applied instead.

**Two sources of truth.** Without `project_root` the schema is built strictly
from what is drawn. With it the brief reads the service repository and returns
a `code` section: how many files were scanned, what matched, and three kinds
of discrepancy — different spellings, a class only in the code, an element
only on the diagram. Each discrepancy carries a file and a line. The path is a
brief parameter, not a setting: every diagram has its own repository.

**`dochub_lessons`** — rules confirmed on earlier schemas. Call first, before
asking questions: what is already recorded should not be asked again.

**`dochub_record_lessons`** — store decisions. Only after the user agreed to
publish; `confirmed=true` is required.

### Diagrams

**`dochub_parse_schema`** — elements, links and nesting. Without `page` it also
returns the composition of every page: a DrawIO file often holds several
independent diagrams and they must not be merged. Logical diagrams are rejected
with an explanation.

**`dochub_render_schema`** — renders the source diagram. PlantUML becomes SVG;
DrawIO is drawn by the preview engine, and `save_to` writes an image. With
`all_pages` every page becomes its own file.

**`dochub_ddd_tree`** — the company domain schema: domains, bounded contexts,
subdomains, responsible people. Used to propose where the schema belongs.

### Repository

**`dochub_scan_repository`** — the manifest: how many components, contexts and
aspects exist, and which root prefixes are in use.

**`dochub_search_components`** — search before describing anything foreign.
Fuzzy matching is on by default, so a duplicate written differently is still
found. `queries` takes a whole list at once: the manifest is built once for all
of them, which turns a minute of one-by-one lookups over a real repository into
about a second.

**`dochub_locate_owner`** — whose component this is and which file it belongs
in. Ownership starts at the service level: a bare platform root owns nothing.

**`dochub_reconcile_with_code`** — compares the diagram with the sources and
lists discrepancies with file and line. It decides nothing: what to include is
the user's call.

### Building

**`dochub_draft_from_schema`** — a structural draft. For a multi-page DrawIO
file `page` is required: pages are separate diagrams, and without a choice the
tool returns their list instead of a draft. `own_root` is required:
without knowing which service the diagram describes, foreign components would
land in your file. Everything outside that frame is listed separately. The
value is an identifier root (`dotnet.orderServiceApi`), not a frame caption:
anything else is refused with the list of roots found in the diagram — the
same list `dochub_brief` offers.

### Checking

**`dochub_validate_context`** — referential integrity: unknown components,
missing aspects, links pointing outside the context.

**`dochub_check_registration`** — whether the file is wired into the tree and
whether root platforms, parent aspects and external services are declared.

**`dochub_render_context`** — builds the PlantUML of a context through the
DocHub metamodel. Useful to confirm a context renders at all.

**`dochub_preview`** — the split screen. The page picks up YAML changes by
itself; the left pane keeps the original, the right one the result. Both sides
can be downloaded — the original as SVG, the result as YAML, PlantUML or SVG.

### Publishing

**`dochub_repo_sync`** — the state of the architecture repository before the
work starts: is the remote reachable at all, how far behind the local copy is,
what is uncommitted, and the work branch. Updating is fast-forward only and is
skipped when the working tree is dirty, so nobody's work is lost. Call it
first, not last: a schema built on a stale copy diverges from other people's
changes, and that only shows up at push time.

**`dochub_register_schema`** — writes into shared and foreign files. Without
`apply` it only returns a plan; changes in foreign subdomains are marked so the
agent can warn the user.

**`dochub_publish`** — branch, commit, push, merge request. `confirmed=true` is
required and must follow an explicit agreement from the user; the one exception
is `automode`, which publishes on its own and says so in the answer. The commit
message is built from the template in the settings — pass `schema_name` instead
of composing it. Target branch, assignee, reviewers, squash and branch removal
come from the settings unless overridden. Merging is never done by the tool.

**`dochub_changed_entities`** — what a branch changes, for both repositories.
For a service repository it lists changed source files, so a schema can be
limited to one task; for the architecture repository it lists added and changed
components, aspects and contexts, which suits reviewing someone else's merge
request.

`DOCHUB_DDD_PATH` also accepts a link (http/https), not only a path. The
server then downloads the domain schema itself — **during preparation**, in
`dochub_repo_sync`, together with updating the repository. Nobody has to keep
a local copy current.

After that nothing reaches for the network again: the brief, the domain tree
and everything else read the downloaded copy. If the refresh failed, work goes
on with the previous copy and `dochub_repo_sync` says so in `warnings`. If
there is no copy at all, the tool names the step that fetches it instead of
reporting a missing file. A Google Drive view link is recognised and turned
into a download address; so is a sign-in page arriving instead of a file.

## Examples

Where things live:

```
dochub_scan_repository(manifest_path="<repo>/architecture/dochub.yaml")
```

Is there already a component for a foreign API:

```
dochub_search_components(manifest_path="...", query="catalogApi")
```

Where a new queue should be described:

```
dochub_locate_owner(repo_root="...", component_ids=["dotnet.eventBus.myQueue"])
```

Compare the diagram with the sources:

```
dochub_reconcile_with_code(schema_path="service.drawio", project_root="<sources>")
```

Show the result and wait for comments:

```
dochub_preview(schema_path="service.drawio", yaml_path="<repo>/.../subdomain.yaml")
```

## Trying the whole path without GitLab

A local folder stands in for the repository perfectly as far as git is
concerned — everything up to and including `push` is the same code path:

```bash
git clone --bare <your-clone-of-the-architecture-repo> origin.git
git clone origin.git work
```

Point `DOCHUB_REPO_ROOT` at `work`, and the branch, the commit and the push all
run for real, against real data, with no access to the corporate network.

The one thing a folder cannot stand in for is the **merge request**: it is an
HTTP call to the GitLab API, and a file path has no host. `dochub_publish` says
so plainly — the branch is pushed, the merge request is not created — instead of
building a nonsensical address out of the path.

## Settings

Everything lives in `.env` next to the project; `.env.example` is the annotated
template. An environment variable of the same name wins over the file, so a
client can override a single value without editing anything.

| Key | What it decides |
|---|---|
| `DOCHUB_REPO_ROOT` | the architecture repository |
| `DOCHUB_MANIFEST` | the root `dochub.yaml`; derived from the repository if unset |
| `DOCHUB_DDD_PATH` | the company domain schema |
| `DOCHUB_PROJECTS_ROOT` | where service repositories live |
| `GITLAB_TOKEN` | a token with the `api` scope — only to create a merge request |
| `DOCHUB_REMOTE_PROTOCOL` | `https` or `ssh`: what the origin expects |
| `DOCHUB_TARGET_BRANCH` | the branch a merge request goes into |
| `DOCHUB_BRANCH_PREFIX` | the prefix of the work branch |
| `DOCHUB_COMMIT_TEMPLATE` | commit message; `{schema}` is substituted |
| `DOCHUB_MR_ASSIGNEE`, `DOCHUB_MR_REVIEWERS` | who owns and who reviews the MR |
| `DOCHUB_MR_SQUASH`, `DOCHUB_MR_REMOVE_SOURCE_BRANCH` | how the branch is merged |
| `DOCHUB_AUTOMODE` | the agent goes all the way to the merge request on its own |
| `DRAWIO_PATH` | Draw.io Desktop, if exporting to PNG or PDF is needed |

`DOCHUB_ENV` points at a different `.env`; `DOCHUB_WORKSPACE` at the file where
`dochub_workspace` saves paths. `DOCHUB_TEST_REPO` is for the tests that need a
real repository.

The token is never a tool parameter and never appears in a log or an answer.

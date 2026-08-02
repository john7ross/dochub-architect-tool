# DocHub Architect Tool

**English** · [Русский](README.ru.md)

An MCP server that helps transfer C4 diagrams from DrawIO and PlantUML into a
DocHub architecture repository.

Code does the deterministic work: parsing diagrams, assembling the repository
manifest, finding components to reuse, checking referential integrity,
rendering, writing YAML, publishing. The meaning — which business function an
element represents, which subdomain owns it, how scenarios split into contexts
— comes from the agent working together with a person, because a diagram alone
does not carry it.

## What it gives you

- **Parsing that works on real files.** C4 attributes live on `<object>`
  wrappers, nesting is expressed by geometry rather than a `parent` attribute,
  and pages are often stored compressed. All of that is handled.
- **Rejection of logical diagrams.** A sequence or activity diagram describes
  behaviour, not composition; the server says so instead of guessing.
- **Rendering by DocHub's own metamodel**, taken from its plugin, so a context
  looks exactly as DocHub will draw it.
- **A split-screen preview**: the original diagram on the left, the generated
  one on the right, refreshed as the YAML changes.
- **Ownership rules.** A queue, a foreign API method or a stored procedure
  belongs in the file of the subdomain that owns it; your own file only
  references it.
- **Research before questions.** The brief reads the diagram, the domain model,
  the repository and the accumulated rules, and only what none of them answers
  is asked — with the source quoted.
- **The repository is checked before the work, not after.** Reachable, current,
  a work branch created — instead of discovering a divergence at push time.
- **Accumulated knowledge.** Decisions confirmed by the user are stored and
  reused, so the same conventions need not be explained again.

## Requirements

| What | Why | Without it |
|------|-----|------------|
| Python 3.10+ | the server itself | required |
| Java 11+ | rendering PlantUML | preview and context rendering unavailable |
| `lib/plantuml.jar` | rendering | fetched by setup_assets.py |
| `vendor/dochub/` | DocHub metamodel | ships with the project |
| `vendor/viewer-static.min.js` | drawing the original DrawIO | left preview pane stays empty |
| `lib/elk/` | ELK layout | falls back to smetana, wider diagrams |
| `GITLAB_TOKEN` | creating a merge request | the branch is pushed, the MR is made by hand |

## Install

Nothing installed on the machine? Build a portable archive — `python
tools/build_release.py` packs a self-contained Windows x64 build with its own
Python, Java and git, so the recipient installs nothing.

From source:

```bash
pip install -r requirements.txt
python setup_assets.py
cp .env.example .env
```

The second command downloads PlantUML and the drawio viewer. They are tens of
megabytes and are not kept in git; after this everything runs offline.
`python setup_assets.py --check` reports what is in place.

## Configure

`.env` is the whole configuration; `.env.example` is the annotated template.
The paths are the part you must fill in — the repository, the domain schema —
the rest has working defaults:

```ini
DOCHUB_REPO_ROOT=C:/repos/architectural-repository
DOCHUB_DDD_PATH=C:/repos/architectural-repository/ddd.drawio
GITLAB_TOKEN=                       # only to create a merge request
DOCHUB_REMOTE_PROTOCOL=https        # or ssh — what your origin expects
DOCHUB_TARGET_BRANCH=main
DOCHUB_COMMIT_TEMPLATE=Добавление схемы {schema}
DOCHUB_MR_ASSIGNEE=                 # who owns the merge request
DOCHUB_AUTOMODE=false               # the agent goes to the MR on its own
```

An environment variable of the same name wins over the file, so a client can
override one value without editing anything. `dochub_workspace` shows the
result and says where each value came from. Full table: [docs/USAGE.md](docs/USAGE.md).

**automode** is for people who do not work with architecture repositories: the
agent answers the brief from defaults and accumulated rules, builds the schema,
shows the preview and goes all the way to the merge request without asking for
confirmation. Merging is never automatic — that stays a human decision.

## Register in an MCP client

The server speaks MCP over stdio. Clients usually keep servers in an
`mcpServers` object:

```json
{
  "mcpServers": {
    "dochub": {
      "command": "python",
      "args": ["-m", "app.mcp_server"],
      "cwd": "<path to the project>"
    }
  }
}
```

A helper prints or writes this fragment without touching servers already
registered:

```bash
python skills/dochub-architect/tools/install_mcp.py --print
```

## Quick check

```bash
python -m app.mcp_server
```

The server writes a startup line to stderr and waits for input — stdout
carries the protocol, so nothing else may go there. Stop with Ctrl+C.

```bash
python -m pytest
```

## Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) — components, technologies, diagrams
- [ROADMAP.md](ROADMAP.md) — status and boundaries
- [docs/USAGE.md](docs/USAGE.md) — the tools and how they work together
- [skills/dochub-architect/](skills/dochub-architect/) — the skill that teaches
  an agent the working order; it ships with the project
- [workflows/](workflows/) — the research phase as a parallel run for Claude
  Code, optional
- [SECURITY.md](SECURITY.md) — threat model and how to report a vulnerability

## Licence

MIT, see [LICENSE](LICENSE). Everything this project redistributes and under
what licence: [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).

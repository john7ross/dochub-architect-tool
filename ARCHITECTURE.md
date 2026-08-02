# Architecture

**English** · [Русский](ARCHITECTURE.ru.md)

## Idea

Transferring a diagram into a DocHub repository splits into two kinds of work.

**Deterministic** — parsing files, assembling the manifest, searching for
existing components, checking referential integrity, rendering, writing YAML,
git. This is code: it is repeatable, testable and costs nothing to run.

**Meaningful** — deciding that `OrderHandlerService` implements "enriching and
sending orders to counterparties", that the queue belongs to the event bus
subdomain, that these five links form one scenario. A diagram does not carry
this; it comes from a person, and an agent helps to formulate it.

The tool is therefore an MCP server: it supplies the first kind of work as
tools, and the agent supplies the second while talking to the user.

## Technologies

| Area | Choice | Why |
|------|--------|-----|
| Protocol | MCP over stdio, FastMCP | the client starts the server as a subprocess; no ports or authentication |
| Validation | Pydantic v2 | schemas are derived from models, the client sees the parameters |
| DrawIO parsing | `xml.etree` from the standard library | the format is plain XML; an external parser adds nothing |
| DocHub rendering | JSONata over the vendored metamodel | executing DocHub's own rules instead of reimplementing them |
| Diagram rendering | PlantUML in a bundled jar | works offline; ELK layout is added as separate jars |
| Original DrawIO | vendored `viewer-static.min.js` | drawio's own engine, so the user sees the original rather than a reconstruction |
| Editing YAML | ruamel.yaml | preserves comments and key order in files people read |
| YAML reading | PyYAML | faster where formatting does not matter |
| git | the `git` CLI through `subprocess` | one less dependency when the project is handed over as an archive |
| GitLab | httpx | only a merge request is created over the API |

## Components

Diagram: [docs/diagrams/component.png](docs/diagrams/component.png), source
[component.puml](docs/diagrams/component.puml).

### Entry point

`app/mcp_server.py` — 20 tools. It only validates input, calls the core and
formats the answer; the logic lives in the modules below. One tool has no core
module of its own: `dochub_brief` is assembly — it calls the parsers, the domain
schema, the manifest and the knowledge store, and turns whatever none of them
answers into the questions for the user.

### Settings

- `settings.py` — the whole configuration of an installation: paths, the GitLab
  defaults and `automode`. Three sources in a fixed order — an environment
  variable, then `.env`, then the paths saved by `dochub_workspace` — and every
  value remembers where it came from, because "the wrong repository" is
  otherwise hard to diagnose. `.env` is parsed here: a library for twenty lines
  would be one more dependency for nothing.
- `workspace.py` — paths saved by the agent in earlier sessions. Kept as the
  lowest-priority source, so an installation configured through the tool keeps
  working. Found via `DOCHUB_WORKSPACE`, then `dochub-workspace.yaml` beside the
  project, then `~/.dochub/workspace.yaml`.
- `utils/paths.py` — locations of bundled assets, resolved from the code rather
  than the working directory: an MCP server starts from an arbitrary folder.
- `utils/logger.py` — logging to stderr; stdout carries the MCP protocol.

### Parsing

- `parsers/drawio_parser.py` — C4 attributes on `<object>`, geometric nesting,
  compressed pages, per-page attribution.
- `parsers/plantuml_parser.py` — C4 macros.
- `parsers/base_parser.py` — the shared model: a component, a link, a parse
  result with per-page slicing.
- `parsers/ddd_parser.py` — the company domain schema: domains, bounded
  contexts, subdomains, responsible people.
- `parsers/geometry.py` — containment, shared by both DrawIO parsers.
- `parsers/diagram_kind.py` — tells a physical diagram from a logical one.

### Model

- `transformer.py` — a draft: ids from `c4Name`, L2/L3 from nesting, links into
  `uml.$after`, and the split into own and foreign components.
- `reconcile.py` — compares the diagram with the service source code and
  reports discrepancies with file and line.

### Repository

- `dochub/manifest.py` — expands `imports` recursively; without it references
  to neighbouring files do not resolve.
- `dochub/native_renderer.py` — runs the DocHub metamodel from `vendor/dochub`.
- `dochub/registration.py` — checks that the file is wired into the tree.
- `dochub/registrar.py` — writes into shared and foreign files; an owner index
  answers "whose component is this".
- `publisher.py` — the repository over the git CLI: the state before the work
  (is the remote reachable, how far behind, what is uncommitted), a
  fast-forward update, the work branch — and then branch, commit, push and the
  merge request with its assignee, reviewers and merge options. Every git call
  closes the child's stdin: under MCP the server's own stdin is the protocol
  pipe, and a child that inherits it never exits.
- `knowledge.py` — rules confirmed on earlier schemas.

### Preview

- `preview/renderer.py` — renders PlantUML, with an offline fallback when
  external includes are unreachable.
- `preview/server.py` — a local HTTP server; the page reports the rendered
  original back, which is how an image is produced without extra software.
- `core/drawio_export.py` — writing a DrawIO diagram to PNG or PDF through
  Draw.io Desktop. Only needed when SVG is not enough: SVG comes from the
  vendored engine and requires nothing installed.

## Two views

**Logical.** The agent drives a sequence: read the accumulated rules, ask the
missing questions, check the diagram type, choose a place in the domain model,
reconcile with the code, build a draft, show the preview, iterate on comments,
register in the tree, publish, record what was learned. The sequence diagram
[docs/diagrams/flow.png](docs/diagrams/flow.png) shows it end to end.

**Physical.** The MCP client starts `python -m app.mcp_server` as a subprocess
and talks over stdin/stdout. The server calls `java -jar lib/plantuml.jar` for
rendering and `git` for publishing, and raises a local HTTP server for the
preview which the user opens in a browser. Everything except the GitLab API
runs on the machine; no network is required after installation.

## Decisions worth knowing

**Rendering by the vendored metamodel, not by our own rules.** A hand-written
generator produced a plausible but different picture. The metamodel from the
DocHub plugin gives nested regions and aspects inside blocks — exactly what
DocHub shows.

**The original DrawIO is never reconstructed.** A person compares the source
with the result, so the left pane must show the source. The vendored viewer
draws it; a reconstruction from parsed components would be a different picture
and would defeat the comparison.

**Unreachable external includes do not break rendering.** Corporate diagrams
pull styles and icons from internal servers. When they are unavailable the
render is repeated without them, and the answer says the structure is intact
while the styling is not.

**A component belongs to its owner.** Queues, foreign API methods and stored
procedures are described in the file of the owning subdomain even when created
for our service. Our file only references them by full id.

**Publication requires explicit confirmation.** It is the only tool that
changes state and reaches outside; without a confirmation flag it refuses and
tells the agent to show the preview first.

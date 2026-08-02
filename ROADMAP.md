# Roadmap

**English** · [Русский](ROADMAP.ru.md)

## Status

The feature queue is closed. The project does what it was built for: an agent
transfers a C4 diagram into a DocHub repository, a person checks the result on
a split screen and confirms publication.

- 20 MCP tools, no drift between declaration and implementation
- 220 tests, all green
- Verified on a real repository (1235 components, 228 contexts) and a real
  service diagram (86 elements, 67 links)
- Every tool driven through a real MCP client before this release, see
  [Release audit](#release-audit)

## What was built

**Parsing.** The DrawIO parser had never worked on real files: C4 attributes
were looked for on `<mxCell>` while they live on `<object>` wrappers, and links
were detected by a substring in `style`. Nesting turned out to be geometric
rather than expressed by `parent`. Compressed pages were not read at all. The
DDD parser was in the same state — it found domains but zero contexts, because
dashed boxes were taken for bounded contexts while they are annotations with
the names of responsible people.

**Rendering.** A hand-written generator was replaced by the DocHub metamodel
taken from the DocHub IDE plugin, so a context is drawn as DocHub draws it.
ELK layout jars come from Maven Central.

**Preview.** Split screen with correct zoom: the whole diagram scales through
the root `viewBox`, which is what broke in the IDE plugin. The original DrawIO
is drawn by drawio's own vendored engine.

**Ownership.** The engine refuses to build a draft until it is told which
service the diagram describes, and everything outside that frame goes into a
separate list to be registered in the owners' files.

**Learning.** Rules confirmed by the user are stored with a confirmation
counter and read before the next schema. The standing paths — repository and
domain schema — are set once and substituted automatically.

**Configuration.** One `.env` holds the paths, the GitLab defaults and the
mode. Every value reports where it came from, because "it works against the
wrong repository" is otherwise a long conversation.

**The repository before the work.** Reachability, how far behind, uncommitted
files, a fast-forward update and the work branch — before the schema is built
rather than at push time.

**The brief.** The diagram, the domain model, the repository and the rules are
read first, and only what none of them answers becomes a question — with the
source quoted. With `automode` the same questions carry defaults, and the agent
goes to the merge request on its own. Merging is never automatic.

## Release audit

Before publication every tool was driven through a real MCP client over stdio,
not called as a Python function: the diagram fixture, a DocHub example
repository, a synthetic DDD schema and a throwaway git repository. That is the
only way the defects below could show up at all — the test suite calls the same
code in process and stayed green throughout.

- **Every `git` call hung until its timeout.** A child process inherits the
  server's stdin, and under MCP that is the protocol pipe, which never closes.
  `dochub_changed_entities` and the whole publication path were unusable.
  Fixed by closing the child's stdin; a test now asserts that every
  `subprocess` call in the project does so.
- **`setup_assets.py` never fetched the drawio viewer.** The site answers 403
  to the default `urllib` User-Agent, and the Windows certificate store may
  carry an expired root, which fails verification before the request. On a
  fresh install the left preview pane stayed empty. Fixed by sending a normal
  User-Agent and using the `certifi` bundle.
- **An older Java produced an unreadable error.** `setup_assets.py` fetches the
  latest PlantUML, which needs Java 11+; below that the JVM raised
  `UnsupportedClassVersionError`, which names neither PlantUML nor the way out.
  Now explained, and the requirement is in the README.
- **`dochub_check_registration` returned an empty report** when the root
  manifest was not found — indistinguishable from "the file is simply not
  connected yet". Now it says what it looked for and where.

Everything else held: 18 tools answered, the split-screen preview rendered both
panes with real content, zoom worked on both, live reload picked up a YAML edit,
all eight preview routes had a caller and every caller had a route, and
publication refused to commit without explicit confirmation.

## Boundaries — deliberately out of scope

**Business names for aspects.** `PartnerRuleChecker` will not become "checking
an order against counterparty rules" by any rule. This comes from a person.

**Splitting into scenario contexts.** Which links form one end-to-end scenario
is a judgement call; the engine produces one context and the agent splits it.

**`team`, `prodact`, service numbers.** These are not in the diagram. Partly
recoverable from the DDD schema, otherwise asked from the user.

**Rendering DrawIO without a browser.** The engine is JavaScript. The preview
draws the diagram; exporting to PNG or PDF without an open preview needs
Draw.io Desktop, and the tool says so rather than substituting a reconstruction.

## Known limits

- A diagram whose macro call is split across two lines will not render — that
  is a defect of the diagram, PlantUML rejects it regardless.
- Automatic owner detection works when the ids on the diagram match the
  repository. When they differ, the frame of your own service is used as the
  signal, and the rest is confirmed with the user.
- The reconciler compares declarations of classes, interfaces and records. It
  does not analyse call graphs, so "a class exists but is not used" is not
  detected.

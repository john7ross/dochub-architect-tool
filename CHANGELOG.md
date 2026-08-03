# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Kept in English only, like the commit history; the prose docs are bilingual.

## [0.3.0] - 2026-08-02

The steps around the schema itself: configuring an installation, preparing the
repository before the work, and the brief that decides what is worth asking.

### Added

- the brief reads the service code when it is given one. `project_root` used
  to do nothing beyond silencing the question about sources — the code was
  only read by `dochub_reconcile_with_code`, if the agent thought to call it.
  Now the brief scans the repository itself and returns what matched and what
  did not: different spellings, classes present only in the code, elements
  present only on the diagram, each with a file and a line. What goes into the
  schema is still asked, never decided

- `DOCHUB_DDD_PATH` accepts a link, not only a path. The domain schema lives
  with the company and changes; keeping a local copy current is not the user's
  job. It is refreshed once, during preparation — `dochub_repo_sync` pulls the
  repository and the schema together — and from the brief onwards everything
  reads the downloaded copy without touching the network. A failed refresh
  keeps the previous copy and says so; a Google Drive view link is turned into
  a download address, and a sign-in page arriving instead of a file is
  recognised and explained

- Settings in `.env` (`.env.example` is the annotated template): paths, the
  GitLab protocol, the target branch, the branch prefix, the commit template,
  merge request parameters and `automode`. An environment variable of the same
  name wins over the file, and `dochub_workspace` reports where every value
  came from
- `dochub_repo_sync` — the state of the architecture repository before the
  work: is the remote reachable at all, how far behind the local copy is, what
  is uncommitted, plus a fast-forward update and the work branch. A schema
  built on a stale copy diverges from other people's changes, and that used to
  surface only at push time
- `dochub_brief` — research before questions: the diagram, the domain model,
  the repository and the accumulated rules are read, and what comes back is
  only what none of them answers, each question carrying its source
- `automode` — for people who do not work with architecture repositories: the
  brief is answered from defaults and the agent goes all the way to the merge
  request without asking for confirmation. Merging is never automatic
- A commit message template (`Добавление схемы {schema}` by default), so the
  format is the same across the repository
- Merge request parameters: assignee, reviewers, squash, removing the source
  branch, target branch — all from the settings, overridable per call
- `dochub_search_components` accepts a list of `queries`: the manifest is built
  once for all of them. Checking the elements of a diagram against a real
  repository (1172 components) took about a minute one call at a time and now
  takes about a second
- `workflows/` — two optional scripts for Claude Code's `Workflow` tool:
  `dochub-batch` (several schemas at once, reading in parallel, writing one at
  a time because the repository is one for all of them) and `dochub-mr-review`
  (one agent per changed file, every finding checked by a sceptic). The skill
  tells the agent to offer them and ask before spending a multi-agent run —
  a user has no way of knowing the scripts exist
- The registration plan names the subdomains instead of counting the changes:
  `own_subdomains`, `foreign_subdomains` and a ready sentence for the user.
  Editing someone else's file is their responsibility and their review, and
  "14 changes" hid that
- A local folder can stand in for the repository: everything up to and
  including `push` runs against it unchanged, which is how the whole path was
  verified on real data without corporate network access. Only the merge
  request cannot be faked that way, and the tool now says exactly that instead
  of building `c:///api/v4` out of a file path
- The DDD parser reads pages separately — coordinates are per page, so a block
  from the next page could otherwise land inside a frame on this one — and it
  reports domain frames that carry no name instead of dropping them silently:
  a domain nobody can see is a domain nobody can place a schema into
- git is looked up the same way as Java: the copy bundled beside the project
  wins, PATH is the fallback, and its absence is explained instead of
  surfacing as `WinError 2`
- The version lives in `app/__init__.py` and nowhere else; the build reads it
  and the archive name carries it, `dochub-architect-tool-0.3.0-portable-win64.zip`
- 220 tests

### Changed

- MCP SDK 2.x is supported: `FastMCP` became `MCPServer` there, and a fresh
  `pip install` gets 2.x — the server would not start at all for a new user.
  Both versions work, the import decides at runtime

### Fixed

- in `automode` the brief filled in every answer, including which page of a
  multi-page file to take. A schema goes into the repository as one specific
  diagram, so that answer belongs to the person, not to a heuristic: the page
  question now carries no default and is asked in `automode` too

- `dochub_draft_from_schema` merged every page of a multi-page DrawIO file
  into one context. Pages are separate diagrams — usually one task per page,
  and the next page is a different service — so the result was a context that
  exists on none of them. The tool now takes `page`, refuses until one is
  chosen and lists the pages with their element counts; `dochub_brief` returns
  the same list and asks which page is meant

- C4 macros as they are actually written were not parsed: a named argument
  after the required ones (`$link=""`, `$tags=`) or an empty description
  (`""`) made the whole file come back with zero elements, and the draft then
  asked which service the schema describes while offering an empty list

- the split-screen preview built the repository manifest and never mixed in
  the file being edited, so a schema that is not yet imported into the tree —
  every new one, at exactly the moment it is shown to the person who ordered
  it — answered `контекст не найден` in the right pane. The manifest now
  carries the edited file on top, so own contexts render and foreign
  components still resolve
- a DrawIO caption is HTML: markup and line breaks went straight into titles
  and identifiers. Identifiers like `fontStyleFontSize11px...` appeared, and a
  line break inside a title split the draft's comment block so the saved file
  stopped being YAML
- a component named in Russian produced an empty identifier and was dropped
  from the draft without a word — on a corpus of real diagrams that silently
  lost 17% of all elements. Names are transliterated now, and every
  transliterated identifier is listed for the user to confirm or rename
- an identifier could contain an empty segment (`mssql..orderItem`) when a
  frame had no usable caption

- `dochub_repo_sync` created or switched the work branch but kept reporting
  the branch it started from in `current_branch`. An agent passing that value
  on to `dochub_publish` committed the schema into `main` instead of the work
  branch — past the merge request and past review. The report now says where
  the repository actually is, and publishing into the target branch is refused
  with an explanation
- `dochub_draft_from_schema` never wrote a file, while every later step works
  with one, and nothing said so — the chain broke at `dochub_validate_context`.
  The draft now states where to save it, and the skill has the step
- a foreign component whose owner could not be found was printed as
  `-> None`, which reads as a known owner

- `dochub_brief` offered `own_root` candidates as frame captions from the
  diagram (`OrderService.Api`), while `dochub_draft_from_schema` matches
  `own_root` against DocHub identifier roots (`dotnet.orderServiceApi`). An
  agent that passed the brief's answer straight on got a draft where every
  component was declared foreign: an empty `components` block and a context
  referencing identifiers that exist nowhere. The brief now returns the roots
  the draft expects, and the draft refuses a value that matches none of them
  instead of building a silently wrong file

- git was asked for credentials in a terminal that does not exist: with
  `GIT_TERMINAL_PROMPT=0` an inaccessible repository now reports a reason
  instead of hanging

## [0.2.0] - 2026-07-21

The project became an MCP server. The desktop interface was removed: the
meaningful part of the work is done by an agent together with a person, and a
form-filling interface only stood in the way.

### Added

- MCP server with 18 tools over stdio
- Rendering through the DocHub metamodel taken from the DocHub IDE plugin
- Split-screen preview with correct zoom and live reload; both sides can be
  downloaded (original as SVG, result as YAML, PlantUML or SVG)
- Diagram classifier: sequence, activity and other logical diagrams are
  rejected with an explanation
- Reconciliation of a diagram with service source code, reporting
  discrepancies with file and line
- Ownership rules: a component is described in the file of the subdomain that
  owns it, and referenced from your own file
- Registration into shared and foreign files with a dry-run plan
- Publication: branch, commit, push, merge request; requires explicit
  confirmation
- Accumulated knowledge: rules confirmed by the user are stored and read
  before the next schema
- Support for multi-page DrawIO files and for compressed pages
- 129 tests, including tests on a real diagram

### Fixed

Found by the release audit, which drove every tool through a real MCP client
instead of calling the functions in process:

- Every `git` call made by the server hung until its timeout. A child process
  inherits the server's stdin, and that is the MCP protocol pipe, which never
  closes; `dochub_changed_entities` and the whole publication path were
  unusable when the server ran the way it is meant to run
- `setup_assets.py` could not download the drawio viewer at all: the site
  answers 403 to the default `urllib` User-Agent, and on Windows the default
  certificate store may carry an expired root, so verification failed before
  the request. The left preview pane stayed empty on a fresh install
- The latest PlantUML needs Java 11+, and an older Java produced a raw
  `UnsupportedClassVersionError` from the JVM that named neither PlantUML nor
  the way out
- `dochub_check_registration` answered with an empty report when the root
  manifest was not found, which reads exactly like "the file is simply not
  connected yet"

- The DrawIO parser found nothing on real files: C4 attributes live on
  `<object>` wrappers, not on `<mxCell>`, and links are an `edge` attribute,
  not a substring in `style`
- Nesting in the C4 template is geometric, not expressed by `parent`
- The DDD parser found zero contexts: dashed boxes are annotations with the
  names of responsible people, not bounded contexts
- Component ids were generated from internal DrawIO ids
- Links were written into a `links` section, which DocHub does not have — they
  belong in `uml.$after`
- Logs went to stdout, which corrupts the MCP protocol
- Paths were resolved relative to the working directory

### Removed

- The desktop interface and everything serving it
- The legacy renderers, superseded by the DocHub metamodel
- Dependencies: from 20 down to 8

## [0.1.0] - 2026-05-25

The first version: a desktop application with a three-pane interface,
parsers, transformers, renderers and GitLab integration.

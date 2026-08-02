# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Kept in English only, like the commit history; the prose docs are bilingual.

## [0.3.0] - 2026-08-02

The steps around the schema itself: configuring an installation, preparing the
repository before the work, and the brief that decides what is worth asking.

### Added

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

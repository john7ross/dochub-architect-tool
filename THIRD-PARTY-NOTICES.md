# Third-party notices

DocHub Architect Tool itself is MIT — see [LICENSE](LICENSE). It redistributes
the components below, each under its own licence.

## DocHub metamodel

**What ships:** `vendor/dochub/base.yaml`, `vendor/dochub/plantuml.yaml`,
`vendor/dochub/smartants.yaml`, `vendor/dochub/template.puml`.

**Licence:** Apache License 2.0.

**Source:** https://github.com/DocHubTeam/DocHub — the files come from the
DocHub plugin for VS Code, version 0.1.6, out of
`extension/public/metamodel/dochub/entities/contexts/`.

**Modifications:** none. All four files are shipped byte for byte as released
upstream.

**Why they are here:** the renderer executes DocHub's own rules over these
files with JSONata instead of reimplementing them, so a generated context is
drawn exactly as DocHub draws it. See [ARCHITECTURE.md](ARCHITECTURE.md).

## Fetched at install time, not redistributed

`python setup_assets.py` downloads these; they are not kept in git and are not
part of this repository.

| What | Where it lands | Source | Licence |
|---|---|---|---|
| PlantUML | `lib/plantuml.jar` | github.com/plantuml/plantuml | GPL, with LGPL / Apache / MIT / EPL variants — see plantuml.com/license |
| drawio viewer | `vendor/viewer-static.min.js` | viewer.diagrams.net | Apache License 2.0 |
| Eclipse Layout Kernel, EMF | `lib/elk/` | Maven Central | Eclipse Public License 2.0 |
| Guava and its annotation deps | `lib/elk/` | Maven Central | Apache License 2.0 |

## The portable archive

The portable archive built by `tools/build_release.py` ships everything needed
to run, so the recipient installs nothing:

| What | Where it sits | Licence |
|---|---|---|
| CPython 3.11 (Windows embeddable) | `runtime/` | PSF License Agreement — full text in `runtime/LICENSE.txt` |
| Eclipse Temurin JRE 17 (OpenJDK) | `runtime/jre/` | GPLv2 with the Classpath Exception — full text in `runtime/jre/legal/`; source at github.com/adoptium/temurin17-binaries |
| MinGit (Git for Windows) | `runtime/git/` | GPLv2 — full text in `runtime/git/mingw64/share/doc/git-doc/`; source at github.com/git-for-windows/git |
| The runtime dependencies below | `runtime/Lib/site-packages/` | each under its own licence, texts inside the package folders |
| PlantUML | `lib/plantuml.jar` | GPL, with LGPL / Apache / MIT / EPL variants — see plantuml.com/license |
| drawio viewer | `vendor/viewer-static.min.js` | Apache License 2.0 |
| Eclipse Layout Kernel, EMF | `lib/elk/` | Eclipse Public License 2.0 |
| Guava and its annotation deps | `lib/elk/` | Apache License 2.0 |

## Runtime dependencies

Installed by `pip install -r requirements.txt`, or bundled into `runtime/` in
the portable archive; each under its own licence:

| Package | Licence |
|---|---|
| mcp | MIT |
| pydantic | MIT |
| ruamel.yaml | MIT |
| PyYAML | MIT |
| jsonata-python | Apache License 2.0 |
| httpx | BSD 3-Clause |
| certifi | Mozilla Public License 2.0 |
| pytest | MIT |

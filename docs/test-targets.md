# Real application test targets

The resettable mailbox remains a fast unit fixture. Real application acceptance
uses local instances of three upstream applications through the same MaaFramework
browser controller.

| Application | Pinned release and license | Task and starting states | Independent result |
| --- | --- | --- | --- |
| diagrams.net / draw.io | [`v32.6.10`](https://github.com/jgraph/drawio/releases/tag/v32.6.10), Apache-2.0 | Create one rectangle labeled `Vibe Maa` in two fresh blank diagrams. | The canvas model contains the requested label and rectangle renderer. |
| JupyterLab | [`4.6.6`](https://github.com/jupyterlab/jupyterlab/releases/tag/v4.6.6), BSD-3-Clause | Create and save `release-notes.md` in an empty workspace and in a workspace with existing notes. | The Contents API reads the expected Markdown from disk and confirms the original files are unchanged. |
| Mozilla PDF.js | [`v6.5.438`](https://github.com/mozilla/pdf.js/releases/tag/v6.5.438), Apache-2.0 | Find `CEDAR LANTERN` and select the second result in two documents with different pagination. | The viewer reports the selected match, its visible highlight, and the actual page text under `Final Release Notes`. |

## Shared browser acceptance

Each target supplies the task, launch and readiness steps, starting states, and
the check against actual application state. The shared grader opens a fresh
1280×720 Chromium session for each state, records application state before and
after native execution, and requires both a successful MaaFW run and a passing
independent check. Grading limits HTTP and WebSocket connections to the target's
loopback origin. The agent explores through screenshots, native recognition,
and input tools. Browser internals and application APIs used by the grader stay
outside the agent tool interface.

The `application-replay` jobs run the checked-in JupyterLab and PDF.js Pipeline
projects on Ubuntu with the Chromium build from the locked Playwright dependency.
They upload the replay verdict and run evidence. The separate model-backed workflow
asks a coding agent to create a fresh project and runs the same independent grader
on its submission. [CI workflow](../.github/workflows/ci.yml) · [Agent acceptance and local reproduction](agent-ci.md)

The JupyterLab dependency set is pinned in a dedicated Python environment, and
each launch gets a separate workspace with kernels and terminals disabled. PDF.js
uses the verified upstream distribution ZIP and locally generated PDF fixtures.
[JupyterLab requirements](../integration/agent/jupyterlab.requirements.txt) · [PDF.js version and SHA256](../integration/agent/pdfjs_target.py)

## Draw.io provisioning and acceptance

| Property | Value |
| --- | --- |
| Upstream | [jgraph/drawio](https://github.com/jgraph/drawio) |
| Release | `v32.6.10` |
| Source commit | `c72c9a98b7a6ce392556ed04c05e0ff19f43f5c3` |
| Official artifact | `draw.war`, 49,729,549 bytes |
| SHA256 | `a57b06b3b6a876a351dacfe65d027d9509319cd030e9bacf6ae04a034d66a38f` |
| License | [Apache-2.0](https://github.com/jgraph/drawio/blob/c72c9a98b7a6ce392556ed04c05e0ff19f43f5c3/LICENSE) |

The artifact includes the compiled editor and local resources. Tests do not use
the public website, cloud accounts, remote fonts, export servers or telemetry.
Before testing, run the explicit [provisioning step](../integration/targets/drawio/README.md).
Runtime network routing permits only the fixture's exact loopback origin.

Use a fresh browser context, English `atlas` UI, 1280×720 viewport, scale 1 and a
blank local document. The initial acceptance task is to add a named diagram
shape and verify that the resulting document contains that shape. This tests a
real application's response to input and provides meaningful positive/negative
screenshots for recognition checks. It is separate from Android/game coverage.

The regular [`Real draw.io acceptance`](../.github/workflows/drawio.yml) workflow
runs identical native Pipeline tests on Ubuntu and Windows with the browser
build selected by the locked Playwright dependency. The independent test oracle
reads draw.io's actual graph model, verifies a single vertex labeled `Vibe Maa`,
and establishes the positive reference before a fresh browser executes the
tested Pipeline. A second case omits label editing: native execution succeeds,
while the goal and independent application result correctly fail. Every browser
request is restricted to the fixture's local origin; tests assert no external
request was attempted.

The workflow uploads screenshots, experiment records, source files and the
independent oracle verdict on success or failure. The scripted acceptance tests
prove application/controller/recognition integration. A coding agent authoring
the automation from a request is a separate evaluation and is not implied by a
green deterministic integration run.

Updates to the upstream version require changing the immutable artifact hash,
re-running acceptance, and reviewing visual fixture changes. Keeping the pin
avoids unrelated upstream UI changes silently breaking CI.

# Real application test targets

The resettable mailbox remains a fast unit fixture. Product acceptance also
uses a separate, well-known application: **diagrams.net / draw.io**. Its web UI
is shared across Windows, Linux and macOS, supports screenshot-driven diagram
editing, and can run entirely from a local static deployment.

## Pinned target

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

## Acceptance boundary

The agent gets original screenshots, MaaFramework recognition results and input
tools. It explores and edits ordinary MaaFW resources. The browser transport
must not expose DOM locators, JavaScript evaluation or draw.io's graph model as
agent capabilities. Test setup and the independent result oracle can use those
APIs so that a successful task is verified outside the agent's own assertion.

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

# diagrams.net / draw.io test target

The pinned upstream target is draw.io `v32.6.10`, commit
`c72c9a98b7a6ce392556ed04c05e0ff19f43f5c3`. The official `draw.war` release
archive contains the complete compiled browser application. Java and an app
build are unnecessary for the local static-editor workflow.

```bash
uv run python integration/targets/drawio/prepare.py --cache work/targets
```

The command downloads about 50 MB once, verifies its pinned byte length and
SHA256, safely extracts the web application, and records hashes of every
extracted file. Subsequent calls verify the cache instead of downloading. A
corrupted extraction is rebuilt from the checked archive. `--archive PATH`
accepts a pre-downloaded identical archive for disconnected provisioning.

Serve the returned `webroot` on an ephemeral loopback port and launch:

```text
/?offline=1&local=1&lang=en&ui=atlas&splash=0&picker=0
```

This opens a blank document directly, skips cloud storage and language pickers,
and fixes the editor layout and language. The fixture must prohibit all network
requests except the exact local origin. Provisioning is the only step that
needs upstream network access. Runtime tests use a fresh browser context at a
fixed 1280×720 viewport and device scale 1.

The maintenance trial changes only the upstream `splash` query parameter to `1`.
The unchanged application then opens its native Device startup dialog before a
document exists. Both startup URLs keep the same pinned bytes, offline mode,
language, layout and viewport. No repair nodes or UI coordinates are added by
the harness. The original isolated agent project must pass the blank start and
fail this new start before a model is called. Final grading repeats both starts
three times and also rejects an open modal. See [the agent CI](../../../docs/agent-ci.md).

The browser is the platform-neutral screenshot/input transport. AI automation
must receive image pixels and send input through MaaFramework's controller; it
must not obtain DOM selectors or the editor's internal graph model. Independent
integration-test assertions may inspect the DOM/model to check the real result.

Run the real application acceptance locally after installing the browser extra:

```bash
uv sync --locked --extra browser
uv run playwright install chromium
uv run pytest -q integration/targets/drawio/test_drawio.py
```

Linux machines without Chromium system libraries can use
`uv run playwright install --with-deps chromium`. The regular
`Real draw.io acceptance` workflow executes the same test on Windows and Ubuntu.
The tests add a rectangle and label it `Vibe Maa` through a standard MaaFW
Pipeline, verify the result with a private independent graph-model oracle, and
check native recognition against positive and negative screenshots. An
intentionally incomplete Pipeline still returns native success but must fail
the protected visual goal and the independent oracle.

CI uploads the resulting source configuration, screenshots, native event records
and oracle verdicts. This is deterministic application integration coverage;
autonomous code generation by an external AI is evaluated separately.

Upstream source is not copied into this repository. See the installed target's
[`manifest.json`](../../../src/vibe_maa/targets/drawio/manifest.json) for
version, download hash, license and attribution. The app is distributed by
JGraph/draw.io under Apache-2.0, and its bundled asset licenses remain applicable.

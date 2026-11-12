# Contributing

Changes should improve the full workflow: install the package, give a coding agent a task, observe the application, edit standard MaaFW resources, verify the result, resume the work, and deliver a portable project.

## Development setup

```bash
git clone https://github.com/MaaXYZ/vibe-maa.git
cd vibe-maa
uv sync --locked --extra browser
uv run playwright install chromium
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

Tests without the browser extra cover the core and native MaaFW fixture. Browser tests are explicitly marked and require Chromium. The ordinary CI suite does not call an LLM; model-based acceptance is separate and documented in [docs/agent-ci.md](docs/agent-ci.md).

## Change boundaries

- Keep device mutation serialized and observation provenance explicit. Do not automatically replay a lost input request.
- Recognition-only probes must never execute actions or traverse successor nodes.
- Keep request processing, native execution, goal checks, and task acceptance separate. Missing evidence must remain visible.
- Do not silently convert fixture results into live-application coverage or turn natural-language constraints into claimed runtime enforcement.
- Do not weaken a goal or change expected case labels merely to make a test pass.
- New resources needed after installation belong in the package and must pass the installed-wheel smoke test outside the source checkout.
- Update the packaged Skill whenever changing its canonical file at `skills/vibe-maa/SKILL.md`; a test checks that the copies match.

Add focused tests for changed behavior and explain which target and runtime were exercised. Report browser/OS/device coverage accurately. Changes to draw.io's version or fixture images require rerunning the target checks.

## Package check

```bash
uv build
uv run --isolated --no-project --with dist/vibe_maa-VERSION-py3-none-any.whl python integration/package_smoke.py
```

The smoke test creates its workspace outside the repository and uses the installed package to create a demo, install a Skill, run and resume a task, and export evidence and ordinary MaaFW resources.

## Releases

Update the version in `pyproject.toml` and `src/vibe_maa/__init__.py`, refresh `uv.lock`, and record meaningful changes in `CHANGELOG.md`. Run CI before tagging `vVERSION`.

The release workflow verifies the tag matches the package version, rebuilds and smoke-tests the distribution, and creates a **draft GitHub Release** containing wheel and sdist artifacts. It does not publish to PyPI or automatically make the draft public. Re-running against an existing release does not silently overwrite it.

## Issues and pull requests

For bugs, include the installed version, OS, backend and target version, a compact report, and the relevant evidence scope. Runtime `.maa-dev/` can contain screenshots and local application data; select the relevant report rather than committing an entire working directory.

Pull requests should describe the concrete before/after behavior, the target used for validation, and remaining limits. Keep unrelated changes separate.

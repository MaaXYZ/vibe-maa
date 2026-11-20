# JupyterLab reference

This developer-authored MaaFramework Pipeline creates and saves `release-notes.md` in JupyterLab at a viewport of 1280×720. It follows the launcher's Markdown icon, renames through the file browser, and saves using Ctrl+S. It checks the editor's unsaved and saved tab icons without matching rendered text. The replay checks the saved Markdown through JupyterLab's Contents API in two fresh workspaces, including one with existing project notes that must be preserved.

```bash
uv sync --locked --extra browser
uv run playwright install chromium
uv venv --python 3.12 .cache/targets/jupyterlab/venv
uv pip sync --python .cache/targets/jupyterlab/venv integration/agent/jupyterlab.requirements.txt
uv run python integration/agent/regrade.py --target jupyterlab \
  --project examples/jupyterlab-portable --output work/jupyterlab-replay --cache .cache/targets
```

# Reference-assisted low-thinking draw.io sample

This is the **unmodified model deliverable** from the explicitly requested `low` run of `deepseek-flash`, using pinned Codex CLI 0.167.2 and vibe-maa. It creates a rectangle labelled `Vibe Maa` in a fresh real draw.io document, using six ordinary MaaFW Pipeline nodes and two image templates. Entry: `AgentTask`. The project includes one durable task and a visual goal; it supplies no case files.

**REFERENCE-ASSISTED:** visible tool commands read the repository's existing solution for this exact task under `examples/agent-generated/drawio/` and the portable example's `interface.json`. This is a successful example-assisted development run, not a clean independent benchmark. It cannot establish a success rate, general game automation ability, or an isolated benefit from changing reasoning effort.

The [original CI run](https://github.com/MaaXYZ/vibe-maa/actions/runs/43196041030) completed model execution in 135.097 seconds within a 600-second budget. An independent grader then ran the deliverable in two fresh application contexts and checked the actual rectangular renderer and exact canvas label; both passed. This result covers that Ubuntu runner and fixed 1280×720 viewport. Cross-platform replay has not been established for this sample.

Visible commands used vibe-maa observations and native input for application exploration. No direct application DOM or Playwright access appeared in those commands. The published bounded activity trace does not prove complete coverage of screenshot-view tools.

[ORIGIN.json](ORIGIN.json) records the run, source revision, usage and original archive checksum. `manifest.json` and all seven files it lists are preserved byte for byte. This README and ORIGIN record are maintainer-added provenance outside the model's original manifest.

Replay without calling a model:

```bash
uv sync --locked --extra browser
uv run vibe-maa browser install
uv run python integration/agent/regrade.py \
  --project examples/agent-generated/drawio-low-assisted \
  --output work/drawio-low-assisted-replay
```

A replay on another host can fail if fonts or rendering differ; record that result without changing these original templates or thresholds.

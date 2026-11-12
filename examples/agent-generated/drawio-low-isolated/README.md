# Low-thinking draw.io sample with filesystem isolation

This is the **unmodified model deliverable** from `deepseek-flash` with explicitly requested `low` reasoning, pinned Codex CLI 0.167.2 and vibe-maa. It creates a rectangle labelled `Vibe Maa` in a fresh real draw.io document. Entry: `AgentTask`. The project contains eight ordinary MaaFW Pipeline nodes, two image templates, one durable task and one case file with positive/negative screenshots.

The [original CI run](https://github.com/MaaXYZ/vibe-maa/actions/runs/43198829628) reported 151.232 seconds within a 600-second wall-time budget. An independent grader then executed the deliverable in two fresh application contexts and checked the actual rectangular renderer and exact canvas label; both passed. This covers the original Ubuntu runner and fixed 1280×720 viewport. Cross-platform replay has not been established for this sample.

The agent used a checkout filesystem allowlist: existing solutions, grader code, benchmark records, tests and Git history were inaccessible. A free preflight verified protected reads were denied before the model ran. Retained visible commands show vibe-maa observations and native input, with no observed prior-solution/grader access, direct application DOM or Playwright access, or network answer retrieval.

**Scope of isolation:** this is filesystem isolation, not network isolation or complete information-flow proof. Network egress remained available. The bounded published activity trace does not establish complete screenshot-view tool coverage or what the model knew before the run. This single result does not establish a success rate, general game automation ability, or a causal improvement from changing reasoning effort.

[ORIGIN.json](ORIGIN.json) records provenance, usage, the source revision and original archive checksum. `manifest.json` and its ten original source files are preserved byte for byte. This README and ORIGIN record are maintainer-added provenance outside that original manifest.

Replay without calling a model:

```bash
uv sync --locked --extra browser
uv run vibe-maa browser install
uv run python integration/agent/regrade.py \
  --project examples/agent-generated/drawio-low-isolated \
  --output work/drawio-low-isolated-replay
```

Record rendering or recognition differences on another host without editing these original templates or thresholds. The separate [reference-assisted sample](../drawio-low-assisted/README.md) retains the earlier successful run that read existing same-task examples; it is a distinct observation.

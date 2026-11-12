# Cross-platform draw.io reference

This developer-authored reference complements the unmodified [small-model submission](../agent-generated/drawio/README.md). It is not presented as that model's original output.

The task contract supplies a fresh blank diagram in a fixed 1280×720 viewport. This flow matches a stable vector palette icon, inserts a rectangle, edits with F2, and types the required label. It avoids matching the text-heavy new-document dialog. The independent grader checks the actual shape and label in two fresh application contexts.

```bash
uv run python integration/agent/regrade.py --project examples/drawio-portable --output work/portable-replay
```

The test uses pinned draw.io and Chromium. The exported Pipeline is ordinary MaaFW; screenshot coordinates still depend on the declared viewport and layout.

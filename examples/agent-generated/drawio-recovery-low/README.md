# Original low-model startup recovery

This is the unmodified exported submission from [run 43202606848](https://github.com/MaaXYZ/vibe-maa/actions/runs/43202606848): `deepseek-flash`, explicit `low`, pinned Codex 0.167.2, 446.007 seconds of a 600-second wall-time budget. It continues the earlier isolated project rather than developing from scratch.

Before model execution, the seed passed a blank start and failed the real application's Device startup dialog. The agent read failure reports, operated the application through vibe-maa, extracted new assets, added the missing branch and reran it. The independent oracle passed blank and splash starts three times each in fresh contexts, preserving exactly one rectangle labelled `Vibe Maa` with no modal.

The original final durable task stayed `needs_validation`: its two startup URLs were treated as different targets. Its individually accepted attempts must not be presented as aggregate task acceptance. A second Linux host passed blank 3/3 but failed splash 0/3 at `SplashStorage`; the original title template did not match the changed rendering. Those failures are retained and are inputs to the next redevelopment trial.

`manifest.json` and its 17 files are unchanged. This README and [ORIGIN.json](ORIGIN.json) are maintainer provenance outside that manifest. The [entire original published artifact](../../../docs/records/archives/drawio-recovery-43202606848.zip) retains the seed, baseline, activity, eleven development runs and six independent runs; the [second-host replay](../../../docs/records/drawio-recovery-local-replay.json) remains a failure.

Replay without a model (rendering differences may reproduce the retained failure):

```bash
uv run python integration/agent/regrade.py \
  --project examples/agent-generated/drawio-recovery-low \
  --starting-states blank splash --repetitions 3 --output work/recovery-original
```

This is one maintenance trial. It does not establish general adaptation or a success rate. The bounded activity shows screenshot observations and native inputs; it does not establish complete image-view coverage or isolate network egress.

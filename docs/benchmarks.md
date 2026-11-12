# Recorded agent acceptance

## First real-model run

- Harness: pinned open-source Codex CLI 0.167.2.
- Model: `deepseek-flash`, official Responses API.
- Requested reasoning: not overridden; the provider documents `high` as its default.
- Task: create one rectangle labelled `Vibe Maa` in a fresh real draw.io document and deliver a standard MaaFW Pipeline.
- Original job: [43185072506](https://github.com/MaaXYZ/vibe-maa/actions/runs/43185072506).
- Unmodified deliverable: [source, templates, cases and task](../examples/agent-generated/drawio/README.md).

The agent completed within the 600-second wall-time budget and produced nine Pipeline nodes, three image templates, positive/negative screenshots and a task definition. Independent fresh-context executions both succeeded and showed the requested rectangle and label.

The initial grader incorrectly required the internal shape name `rectangle`. draw.io's standard rectangle uses an `mxLabel` renderer; the initial red job is retained. The corrected oracle checks the actual rectangular renderer, rejects an actual ellipse with the same text, and replays the unchanged submission. Ubuntu CI replay passes. Windows and a separate Linux host fail the original text-heavy dialog template; those failures are retained in [the cross-platform run](https://github.com/MaaXYZ/vibe-maa/actions/runs/43187724753).

Reported usage, summed by the harness:

| Metric | Tokens |
| --- | ---: |
| Input, including cached input | 3,508,388 |
| Cached input | 3,442,560 |
| Output | 30,804 |
| Reasoning output | 9,588 |

These counters are reported model usage, not a monetary estimate. They are one observation, not a statistical success rate or a claim about arbitrary games.

## Separate cross-platform reference

[The portable reference](../examples/drawio-portable/README.md) is developer-authored and is not substituted for the model's output. It uses the declared fresh blank document, matches a stable vector palette icon and edits the inserted shape with native inputs. The same independent oracle tests fresh contexts. App-level native tests and this reference run on both Ubuntu and Windows; the exact original agent sample is gated only on its original Ubuntu environment.

## First explicit low run: service lifecycle blocked

[Run 43192717285](https://github.com/MaaXYZ/vibe-maa/actions/runs/43192717285) used the same configured `deepseek-flash`, target, pinned harness and 600-second budget, with reasoning explicitly set to `low`. It used the new discovery/context tools at source `7071d1c`; this is not a controlled comparison that changes only reasoning.

The run timed out after 600.059 seconds without producing a Pipeline. The first browser session opened successfully, but the next observation returned `SESSION_NOT_FOUND`; subsequent status showed a dead service with stale metadata. Each Codex shell command ran in its own Linux PID namespace, whose termination killed the detached service. A model-free `unshare` reproduction confirmed that lifetime failure. A [before/after record](records/pidnamespace-service-lifetime.json) shows the original loss and the supervised same-session observation succeeding across two new namespaces. The trial therefore does not assess whether the model can implement the UI task once the tools work.

| Observed metric | Value |
| --- | ---: |
| Unique tool items | 24 |
| Completed shell commands | 23 |
| Nonzero completed commands | 1 |
| Capability queries | 4 |
| Context snapshots | 1 |
| Image views / input operations / Pipeline files | 0 / 0 / 0 |
| Reported token usage | Unavailable before timeout |

Usage is unreported, not zero spend. Historical runs did not retain precise model elapsed time or tool counters; those cells cannot be reconstructed from total job duration. The published command history showed diagnostics rather than forbidden DOM access, but no actual application exploration occurred. A [committed result record](records/drawio-low-43192717285.json) retains configuration, outcome and audit facts without raw diagnostic output.

The failed run also exposed a mixed-log redaction gap for an expired local daemon token. No LLM API key was found exposed. The affected artifact was removed after preserving a [re-sanitized activity record](records/drawio-low-43192717285-activity.json); publication now removes authentication fields even when JSON follows non-JSON log text.

## Next comparison

Fix and verify cross-command service ownership without model calls first, then repeat the bounded `low` trial with the same model and protected grader. Declare the actual time budget and the grader's existing 1280×720 viewport explicitly. Keep this infrastructure-blocked result alongside the rerun; do not replace it with the later outcome.

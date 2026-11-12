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

## Next comparison

Use the same task, model, budget and protected grader with an explicit `low` setting. Retain model failures and tool failures separately; compare tool activity, elapsed time and usage before changing the prompts or acceptance contract. A lower-thinking result must be observed rather than inferred from the successful default-thinking run.

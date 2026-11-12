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

## Low rerun after service repair: reference-assisted success

[Run 43196041030](https://github.com/MaaXYZ/vibe-maa/actions/runs/43196041030) at `bd9a92b` passed the API-free real Codex sandbox preflight, then completed with `deepseek-flash`, explicit `low`, and the same 600-second budget. The agent took 135.097 seconds and produced six nodes, two templates and a task definition. The independent real-application oracle passed both fresh-context repetitions. No offline case manifest was delivered. The original manifest and source also passed an unchanged two-context replay on a separate Linux host; Linux CI now retains a free replay of this assisted sample. Windows replay of this exact sample has not been established.

**This was reference-assisted.** The visible command history includes reading the existing same-task draw.io solution and the portable example's interface file. The model also performed native observation, three guarded inputs and recognition probes, but this result cannot establish independent development without prior solution access. The [unaltered deliverable](../examples/agent-generated/drawio-low-assisted/README.md), [result record](records/drawio-low-assisted-43196041030.json) and [published activity](records/drawio-low-assisted-43196041030-activity.json) retain that distinction.

| Reported metric | Value |
| --- | ---: |
| Tool items | 53 |
| Input tokens, including cached | 1,265,484 |
| Cached input tokens | 1,220,096 |
| Output tokens | 14,931 |
| Reasoning output tokens | 5,641 |

These figures are one observation, not a price or success-rate claim. The retained shell activity shows no direct DOM or Playwright app access; it does not independently establish image-view tool use. The single nonzero command used an unsupported explicit `--headless` flag and was corrected using help. The CLI now accepts that ordinary flag, and a generic ProjectInterface reference reduces the need to inspect unrelated examples for handoff syntax.

## Filesystem-isolated low trial

The shared permission policy now exposes the installed tool runtime and current submission, while blocking existing solutions, the privileged grader, old results, tests, Git metadata and private logs. The [actual Codex sandbox check](records/codex-isolation-proof.json) verifies denied direct and symlink reads while native session reuse still succeeds. A final preflight uses the same policy helper as model execution. This filesystem policy does not isolate network egress; the task contract also forbids fetching prior solutions and retained tool activity remains subject to audit.

[Run 43198829628](https://github.com/MaaXYZ/vibe-maa/actions/runs/43198829628), source `40d5b51`, completed successfully with the same configured `deepseek-flash`, explicit `low`, target and 600-second budget. Model execution took **151.232 seconds**. The unmodified deliverable contains **eight nodes, two templates, one durable task, and a case manifest with positive/negative screenshots**. The independent oracle passed both fresh real-application contexts. The original files also passed unchanged replay in two fresh contexts on a second Linux host; Linux CI now retains a free replay of this isolated sample. Exact-source Windows replay has not been established.

The [original source](../examples/agent-generated/drawio-low-isolated/README.md), [result](records/drawio-low-isolated-43198829628.json) and [tool activity](records/drawio-low-isolated-43198829628-activity.json) are retained. Visible commands show no reads of prior solutions, privileged grader data or network answers, and no direct DOM/Playwright app access. The activity format still does not independently establish complete screenshot-view tool coverage. One nonzero command used an unsupported `task context --max-bytes` option; the agent continued and delivered valid resources.

| Reported metric | Value |
| --- | ---: |
| Tool items / completed shell commands | 41 / 40 |
| Input tokens, including cached | 1,440,197 |
| Cached input tokens | 1,395,200 |
| Output tokens | 18,335 |
| Reasoning output tokens | 5,750 |

This is one successful constrained draw.io task, not a statistical success rate or evidence that arbitrary game dailies are solved. The earlier infrastructure-blocked and reference-assisted trials remain visible. Tooling, references, lifecycle and permission policies changed between trials, so elapsed time or token differences cannot be attributed only to reasoning effort.

## Next acceptance work

Expand application and unseen-start-state coverage, retain original outputs and failure evidence, and improve tool ergonomics from observed command errors. Keep offline recognition cases, fixture results and real application outcomes distinct.

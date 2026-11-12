---
name: vibe-maa
description: Develop and debug ordinary MaaFramework resources using persistent device sessions, same-frame observations, recognition-only probes, and recorded execution evidence.
---

# MaaFramework development workflow

Use this workflow when the user asks to implement or repair a MaaFramework automation and `vibe-maa` is installed. Operate on the requested project. The CLI does not contain a model; you remain responsible for understanding the task, editing project files, and assessing evidence.

## Establish the task

1. Read the project's instructions. Run `vibe-maa capabilities` once to discover the installed methods, then `vibe-maa capabilities --method METHOD` only for the operation you need. The focused response gives exact request fields, CLI examples, prerequisites and result semantics without loading a full manual. `--help` lists local CLI commands.
2. Run `vibe-maa --project PATH context` on entry, or `context --task NAME` when resuming a known task. This bounded, read-only snapshot covers project nodes, saved tasks, existing sessions and recent evidence without starting a service. Follow its next commands as needed. Read `task context NAME` for the full objective, constraints and authoritative acceptance details before continuing a task. A service restart expires device sessions, not the saved task.
3. Establish the task's starting conditions and observable completion condition from the request or project. Ask only for information that cannot be obtained from the device or project. For a new task, use `task create NAME --objective "OBJECTIVE" --entry ENTRY --goal GOAL`, adding `--cases PATH`, repeated `--constraint "REQUIREMENT"`, and `--max-attempts N` as appropriate. Existing definitions are ordinary JSON files in `dev/tasks/`; edit them with your coding tools, then reread task context. There is no `task update` command. Changing a definition requires fresh validation.
4. Start with a repeatable segment where possible. Existing rewards, resources, or server-side state may make a completed task impossible to repeat immediately. Natural-language constraints are requirements you must honor; the task ledger does not automatically enforce their business semantics.
5. Check `doctor` for environment/project readiness and `devices` for discoverable Android targets. Follow an actionable failed check rather than guessing an address or treating missing OCR as an empty screen.
6. Keep the service alive across tool calls. Run `daemon status` first and reuse a running compatible service. Otherwise, in a coding-agent sandbox, launch `vibe-maa --project PATH daemon serve` once in a long-lived exec or PTY session: let the execution tool yield while the command continues running. A stable host terminal or supervisor can also own it. Run `daemon status` in a separate call and confirm `running` and `compatible` before opening a session. Appending shell `&` is insufficient when the sandbox destroys a command's PID namespace after exit. Keep the foreground session alive until work finishes. If the harness declares the service externally managed, use that service, close only sessions you opened, and leave service shutdown/restart to its owner.
7. Explicitly open the intended backend with `vibe-maa --project PATH session open --backend adb --device ADDRESS`. Backend selection is required. Use `--backend demo` only for the resettable fixture, and label fixture results as such.

The installed package includes the workflow and demo data. `agent show` returns these instructions; `agent install --target codex` or `agent install --target claude` installs a new project-local Skill without changing home directories or overwriting an existing Skill. Refresh the coding-agent session after installation. For a reproducible development exercise, `demo init --starter` creates a project with a genuinely missing announcement-handling branch, ready for source editing.

## Observe and explore

- Call `observe --session SESSION_ID`, then inspect the returned original image. OCR and element boxes refer to this exact observation's controller coordinate system.
- Pass its observation ID with `act`; target references such as `e2` belong only to that observation. Use the returned new observation after an action.
- Key/text inputs also require a target or guard box that identifies the relevant current UI region.
- Context snapshots mark retained observations as historical; they do not authorize input. Observe the current device before acting. If input is rejected as stale, inspect the replacement observation or observe again. Do not bypass the check or retry old coordinates blindly.
- Check action `status` separately from the JSON envelope's `ok`. Use structured `failure.code` and `observation_failure.code` when present. If input times out or the response is lost, inspect persisted evidence and observe the current state before retrying: the input may already have happened.
- If `DAEMON_REQUIRES_SUPERVISOR` or `SESSION_OWNER_LOST` occurs, fix the service lifetime first and read saved context/evidence. Establish a new session only after a stable service exists, then observe its current state. Repeatedly opening replacement sessions does not repair process lifetime; never replay old inputs automatically.
- Missing OCR models produce an explicit warning, not proof that the screen contains no text. Use original images or provision appropriate project models.

## Implement and probe

- Edit standard files under `resource/pipeline/` and related custom code using normal coding tools.
- Use `probe --session SESSION_ID --observation OBS_ID --node NODE` to evaluate recognition on saved pixels without input. Check `result.hit`, not only candidate lists or scores.
- Probe both relevant positive and negative screenshots. Offline Custom/context-dependent nodes may be unsupported; do not replace an unsupported result with a fabricated match.
- Use `asset extract --observation OBS_ID --box X,Y,W,H --name NAME` to generate a template with source provenance. Keep the original frame available while debugging.

## Run and repair

1. Run a bounded experiment: `run --session SESSION_ID --entry ENTRY --goal GOAL --timeout-seconds 20`. This is useful for temporary debugging. To validate a saved task against the actual source and its selected cases, use `task run NAME --session SESSION_ID --timeout-seconds 20`.
2. Read both `execution_status` and `goal_status`. A completed RPC or succeeded native task is insufficient proof that the user's goal occurred. Missing or unavailable checks remain `unknown`.
3. If the experiment fails, inspect its `report_path`, structured `failure` or `verification_failure`, native events, failed recognition records, and original images. An unavailable completion check stays `unknown`. Distinguish observed facts from suspected causes.
4. Modify the relevant nodes or assets, then repeat. Resources are reloaded between experiments. Use temporary `--override` only for deliberate experiments and record any changes that should become permanent in source files.
5. Never change a goal or expected test label solely to obtain a passing result. If a completion condition needs correction, explain the evidence for the correction.
6. Re-read `context --task NAME` after editing or resuming; use `task context NAME` for details. Acceptance is tied to the saved task and current input files; a passing old run is not validation of new source. Check whether the attempt budget remains available rather than silently changing or replacing the task to evade it.

When a previously working target changes, treat the new failure as development input. Preserve the last working source and failing native report, observe the current interface again, and compare the failed recognition/action with its saved pixels. Add grounded cases for the new state while retaining earlier labels. Choose the repair from those observations, then rerun both the changed state and earlier required starts from fresh sessions. Earlier acceptance covers its recorded source, target and checks; it does not prove that the current application has stayed unchanged. Recognition inside a branch may depend on its predecessor's established state: test that whole branch and document such dependencies rather than presenting a contextual match as a general UI detector.

## Preserve verification

- Curate labeled full screenshots into `dev/cases/` and use `case check --session SESSION_ID --cases dev/cases/NAME.json`.
- Each case has `node`, `image` relative to the manifest, and explicit boolean `expected_hit`. Labels need support from the request, visible evidence, or established project cases. Your own prediction is not independent ground truth.
- Report offline recognition checks separately from live device runs. Keep untested branches and unknown completion states explicit.
- Runtime files in `.maa-dev/` are ignored by Git. Deliver standard project files, selected verification samples, asset provenance, and a concise statement of what was actually tested.
- Close your sessions when finished; stop a project daemon only when you own its lifecycle. An externally managed service belongs to its harness or supervisor. Never claim a real game's daily task was validated using only the mailbox fixture.

## Keep tool use small and explicit

- Use `context --max-bytes 6000` when you need a smaller orientation snapshot; check omission counts and request only the missing detail. Avoid repeatedly dumping project trees, full reports or all method schemas.
- Read `vibe-maa reference --topic pipeline` before writing unfamiliar node fields. Other topics: recognition, actions, errors. For an ordinary ProjectInterface handoff, read `reference --topic project` instead of browsing unrelated automation solutions.
- `run` returns a compact summary by default. Use `--full` or saved artifacts only when needed.
- If the objective requires multiple starting states, declare `task create ... --scenario NAME=START_NODE` for each and validate using `task run ... --scenario NAME`. One passing start does not cover the others.
- Task scenario proofs belong to one exact target, including the complete browser URL and runtime. If one application has different startup URLs, explicitly declare every `--scenario-url NAME=URL` with its `--scenario NAME=START_NODE` (or edit `scenario_urls` in the task JSON). URLs must have the same origin/path, each session must match its scenario's exact URL, and runtime/resources must still agree. A new declaration needs fresh validation. If individually accepted runs leave `needs_validation`, inspect `acceptance_target` and `excluded_target_attempts`; do not repeat passing attempts or change the goal to force aggregation.

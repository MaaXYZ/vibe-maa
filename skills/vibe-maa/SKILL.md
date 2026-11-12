---
name: vibe-maa
description: Develop and debug ordinary MaaFramework resources using persistent device sessions, same-frame observations, recognition-only probes, and recorded execution evidence.
---

# MaaFramework development workflow

Use this workflow when the user asks to implement or repair a MaaFramework automation and `vibe-maa` is installed. Operate on the requested project. The CLI does not contain a model; you remain responsible for understanding the task, editing project files, and assessing evidence.

## Establish the task

1. Read the project's instructions and existing resources. Run `vibe-maa --help` to confirm the installed command interface.
2. Establish the task's starting conditions and observable completion condition from the request or project. Ask only for information that cannot be obtained from the device or project.
3. Start with a repeatable segment where possible. Existing rewards, resources, or server-side state may make a completed task impossible to repeat immediately.
4. Open a session for the intended device with `vibe-maa --project PATH session open --backend adb --device ADDRESS`. Use `--backend demo` only for the resettable fixture, and label fixture results as such.

## Observe and explore

- Call `observe --session SESSION_ID`, then inspect the returned original image. OCR and element boxes refer to this exact observation's controller coordinate system.
- Pass its observation ID with `act`; target references such as `e2` belong only to that observation. Use the returned new observation after an action.
- Key/text inputs also require a target or guard box that identifies the relevant current UI region.
- If input is rejected as stale, inspect the replacement observation or observe again. Do not bypass the check or retry old coordinates blindly.
- Check action `status` separately from the JSON envelope's `ok`. If the response is lost, inspect persisted evidence before retrying: the input may already have happened.
- Missing OCR models produce an explicit warning, not proof that the screen contains no text. Use original images or provision appropriate project models.

## Implement and probe

- Edit standard files under `resource/pipeline/` and related custom code using normal coding tools.
- Use `probe --session SESSION_ID --observation OBS_ID --node NODE` to evaluate recognition on saved pixels without input. Check `result.hit`, not only candidate lists or scores.
- Probe both relevant positive and negative screenshots. Offline Custom/context-dependent nodes may be unsupported; do not replace an unsupported result with a fabricated match.
- Use `asset extract --observation OBS_ID --box X,Y,W,H --name NAME` to generate a template with source provenance. Keep the original frame available while debugging.

## Run and repair

1. Run a bounded experiment: `run --session SESSION_ID --entry ENTRY --goal GOAL --timeout-seconds 20`.
2. Read both `execution_status` and `goal_status`. A completed RPC or succeeded native task is insufficient proof that the user's goal occurred. Missing or unavailable checks remain `unknown`.
3. If the experiment fails, inspect its `report_path`, native events, failed recognition records, and original images. Distinguish observed facts from suspected causes.
4. Modify the relevant nodes or assets, then repeat. Resources are reloaded between experiments. Use temporary `--override` only for deliberate experiments and record any changes that should become permanent in source files.
5. Never change a goal or expected test label solely to obtain a passing result. If a completion condition needs correction, explain the evidence for the correction.

## Preserve verification

- Curate labeled full screenshots into `dev/cases/` and use `case check --session SESSION_ID --cases dev/cases/NAME.json`.
- Each case has `node`, `image` relative to the manifest, and explicit boolean `expected_hit`. Labels need support from the request, visible evidence, or established project cases. Your own prediction is not independent ground truth.
- Report offline recognition checks separately from live device runs. Keep untested branches and unknown completion states explicit.
- Runtime files in `.maa-dev/` are ignored by Git. Deliver standard project files, selected verification samples, asset provenance, and a concise statement of what was actually tested.
- Close sessions or stop the project daemon when finished. Never claim a real game's daily task was validated using only the mailbox fixture.

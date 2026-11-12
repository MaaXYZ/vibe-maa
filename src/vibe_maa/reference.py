"""Small offline MaaFW references for agents working without web access."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import __version__
from .errors import DevError

_FLOW = {
    "Start": {
        "recognition": "DirectHit",
        "action": "DoNothing",
        "next": ["ClickBlack"],
        "timeout": 1500,
        "rate_limit": 20,
    },
    "ClickBlack": {
        "recognition": "ColorMatch",
        "roi": [0, 0, 16, 16],
        "lower": [0, 0, 0],
        "upper": [5, 5, 5],
        "count": 100,
        "action": "Click",
        "target": [20, 20],
        "next": ["WhiteVisible"],
        "timeout": 1500,
        "rate_limit": 20,
    },
    "WhiteVisible": {
        "recognition": "ColorMatch",
        "roi": [0, 0, 16, 16],
        "lower": [250, 250, 250],
        "upper": [255, 255, 255],
        "count": 100,
        "action": "DoNothing",
    },
}

_TOPICS: dict[str, dict[str, Any]] = {
    "pipeline": {
        "content": (
            "Write a JSON/JSONC object mapping unique node names to node objects in "
            "resource/pipeline/*.json. Images belong in resource/image/; optional OCR files belong "
            "in resource/model/ocr/. All coordinates use the returned controller screenshot pixels. "
            "Use a DirectHit + DoNothing entry with next candidates for a predictable dispatcher. "
            "In the verified 5.16 runtime an entry with conditional recognition is also recognized "
            "before its action, so an entry may wait or fail without dispatching input. "
            "MaaFW checks next in order on each frame; the first matching candidate executes its "
            "action, then follows that candidate's next. An empty next ends that path. "
            "After a node runs, its timeout controls waiting for its NEXT list: change the "
            "predecessor timeout to extend that candidate wait. Initial entry recognition uses "
            "the entry's own timeout. timeout, rate_limit, pre_delay and post_delay are milliseconds; the "
            "vibe-maa run --timeout-seconds budget is seconds. Use bounded waits. on_error handles "
            "action failure or no next match before timeout. Verify the goal separately using "
            "observable recognition; DirectHit is not a valid completion check."
        ),
        "examples": {"model_free_click_flow": _FLOW},
        "example_setup": (
            "The model-free example expects a black 64x64 image and a target that turns white "
            "after clicking [20,20]. Entry: Start. Goal: WhiteVisible. Adapt observations to the real UI."
        ),
        "commands": {
            "inspect": "vibe-maa --project PATH observe --session SESSION_ID",
            "probe": "vibe-maa --project PATH probe --session SESSION_ID --observation OBS_ID --node NODE",
            "run": "vibe-maa --project PATH run --session SESSION_ID --entry Start --goal WhiteVisible --timeout-seconds 10",
        },
    },
    "recognition": {
        "content": (
            "roi is [x,y,width,height], not two corner points. Prefer a small stable region with "
            "positive and negative examples. TemplateMatch uses template relative to resource/image/ "
            "and threshold in [0,1]. Crop an observed original with asset extract before using it; "
            "keep the same screenshot scale. OCR uses expected text or regex and requires det.onnx, "
            "rec.onnx and keys.txt in resource/model/ocr/. Missing OCR is not an empty-screen result. "
            "ColorMatch uses lower/upper bounds; its default method 4 uses RGB values, although "
            "adapter arrays internally use BGR. count is the minimum matching pixel count. "
            "DirectHit is unconditional. Probe uses saved pixels and never runs action/next; inspect "
            "result.hit, since nonempty all_results can still fail filtering. Offline probes reject "
            "Custom, And/Or and context-dependent ROI. A DirectHit + DoNothing entry with guarded "
            "next candidates clearly separates dispatch from UI-dependent actions."
        ),
        "examples": {
            "template_node": {
                "recognition": "TemplateMatch",
                "template": "confirm.png",
                "roi": [100, 100, 300, 200],
                "threshold": 0.85,
                "action": "Click",
            },
            "ocr_node": {
                "recognition": "OCR",
                "expected": "^Confirm$",
                "roi": [100, 100, 300, 200],
                "action": "Click",
            },
        },
        "commands": {
            "crop": "vibe-maa --project PATH asset extract --observation OBS_ID --box 100,100,150,40 --name confirm",
            "check_cases": "vibe-maa --project PATH case check --session SESSION_ID --cases dev/cases/checks.json",
        },
    },
    "actions": {
        "content": (
            "Use exact native action names: DoNothing, Click, Swipe, ClickKey, InputText. "
            "Click target defaults to true: the just-matched box; target [x,y] is a fixed point; "
            "[x,y,width,height] is a region, with a sampled click location. Numeric coordinates "
            "must come from observations. Swipe uses begin/end and duration in milliseconds. "
            "ClickKey uses numeric key, not key_name; InputText uses input_text, not text. "
            "Key codes depend on the controller: browser/Win32 VK 113 is F2, while Android 4 is "
            "Back. Browser supports its documented subset of VK codes and no DOM selectors; "
            "some native controllers limit text to ASCII. Observe again after input. CLI act "
            "uses its own type/key/text fields and always needs a fresh observation; key/text "
            "inputs also need a target or guard box. Pipeline field names and CLI arguments differ."
        ),
        "examples": {
            "click_matched_node": {
                "recognition": "OCR",
                "expected": "Confirm",
                "action": "Click",
                "target": True,
            },
            "browser_or_win32_f2": {"recognition": "DirectHit", "action": "ClickKey", "key": 113},
            "android_back": {"recognition": "DirectHit", "action": "ClickKey", "key": 4},
            "type_text": {
                "recognition": "DirectHit",
                "action": "InputText",
                "input_text": "Sample label",
            },
            "swipe": {
                "recognition": "DirectHit",
                "action": "Swipe",
                "begin": [200, 300],
                "end": [200, 100],
                "duration": 300,
            },
        },
        "commands": {
            "click": "vibe-maa --project PATH act --session SESSION_ID --observation OBS_ID --target e2",
            "key": "vibe-maa --project PATH act --session SESSION_ID --observation OBS_ID --type key --key 113 --box 0,0,100,100",
        },
    },
    "errors": {
        "content": (
            "A JSON envelope ok:true means the request completed, not that the operation or goal "
            "succeeded. Inspect action status, run execution_status and independent goal_status. "
            "STALE_OBSERVATION: inspect its replacement observation or call observe; do not "
            "replay old coordinates. SESSION_NOT_FOUND: confirm the original owner is running before "
            "opening a new session and observing. SESSION_OWNER_LOST/DAEMON_REQUIRES_SUPERVISOR: "
            "keep daemon serve running in a persistent tool session or stable host supervisor; "
            "shell backgrounding does not survive per-command PID namespace teardown. Do not "
            "loop session.open or replay inputs. EXTERNAL_DAEMON_*: the harness owns the service; "
            "close your sessions and let its owner repair or stop it, rather than replacing it. "
            "DAEMON_VERSION_MISMATCH: stop the old daemon, reopen sessions. OCR_UNAVAILABLE: "
            "provision compatible models or inspect the original picture with --no-ocr. "
            "NODE_NOT_FOUND/INVALID_PIPELINE: check names, JSON and resource paths. "
            "UNSUPPORTED_PROBE: use a supported numeric-ROI node or a bounded live task; do not "
            "invent results. A failed/timeout run: inspect report show, waiting_for and original "
            "last_recognition; repair the missing transition or recognition and rerun current "
            "source. Unknown goal remains unaccepted. Do not weaken goals or case labels to pass. "
            "If a response is lost, inspect saved evidence before retrying: input may have occurred. "
            "Fixture scope does not establish real-software success."
        ),
        "examples": {
            "processed_but_failed": {
                "ok": True,
                "result": {"execution_status": "failed", "goal_status": "unknown"},
            },
        },
        "commands": {
            "reports": "vibe-maa --project PATH report list --limit 5",
            "inspect_run": "vibe-maa --project PATH report show RUN_ID",
            "resume_task": "vibe-maa --project PATH task context TASK_NAME",
        },
    },
}


def show(topic: str = "pipeline") -> dict[str, Any]:
    if not isinstance(topic, str) or topic not in _TOPICS:
        raise DevError(
            "INVALID_ARGUMENT", "Reference topic must be pipeline, recognition, actions or errors"
        )
    return {
        "schema_version": 1,
        "version": __version__,
        "protocol": "MaaFramework 5.16 core subset",
        "topic": topic,
        **deepcopy(_TOPICS[topic]),
    }

"""Small, read-only contracts for agents discovering the development service."""

from __future__ import annotations

import importlib
import inspect
import types
from copy import deepcopy
from typing import Any, Union, get_args, get_origin, get_type_hints

from . import __version__
from .errors import DevError

# Operational metadata lives here; parameter names, required fields and defaults
# come from the real handlers so a changed Python API cannot silently go stale.
_METHODS = {
    "session.open": {
        "handler": ("service", "DevelopmentService", "session_open"),
        "summary": "Open a persistent, explicitly selected device or fixture connection.",
        "effects": ["connects a device or starts a browser/fixture", "retains a live session"],
        "prerequisites": [
            "persistent, compatible project daemon",
            "existing project resource directory",
            "backend-specific setup",
        ],
        "returns": ["session_id", "backend", "device", "coordinates", "runtime"],
        "example": {"backend": "browser", "url": "http://127.0.0.1:8765"},
        "notes": ["ADB requires device; browser requires url; demo is synthetic, not a game."],
    },
    "session.list": {
        "handler": ("service", "DevelopmentService", "session_list"),
        "summary": "List the current service's live connections.",
        "effects": [],
        "prerequisites": [],
        "returns": ["sessions"],
        "example": {},
        "notes": ["Sessions expire on service restart; saved evidence does not."],
    },
    "session.close": {
        "handler": ("service", "DevelopmentService", "session_close"),
        "summary": "Stop a session and release its connection/lease.",
        "effects": ["closes the selected connection"],
        "prerequisites": ["live session_id"],
        "returns": ["closed"],
        "example": {"session_id": "session_ID"},
    },
    "observe": {
        "handler": ("service", "DevelopmentService", "observe"),
        "summary": "Capture one original frame and optional OCR from those exact pixels.",
        "effects": ["writes an observation and PNG; sends no UI input"],
        "prerequisites": ["live session_id", "OCR models for real OCR, or ocr=false"],
        "returns": [
            "observation_id",
            "image_path",
            "sha256",
            "width",
            "height",
            "elements",
            "warnings",
        ],
        "example": {"session_id": "session_ID", "ocr": True},
        "notes": ["Read image_path. Missing OCR is a warning, not evidence of an empty screen."],
    },
    "act": {
        "handler": ("service", "DevelopmentService", "act"),
        "summary": "Dispatch one guarded input using a current observation.",
        "effects": ["changes the UI", "records intent, result and a new observation"],
        "prerequisites": ["live session_id", "same-session current observation", "target or box"],
        "returns": ["action_id", "status", "observation", "failure"],
        "example": {"session_id": "session_ID", "observation_id": "obs_ID", "target": "e2"},
        "notes": [
            "target is an eN reference from that observation. box is [x,y,width,height].",
            "All input types require target or box, including key/text as a visual guard.",
            "Click/swipe starts at the selected box center; swipe also needs x2,y2.",
            "Inspect status and the returned new observation; do not reuse the old reference.",
        ],
    },
    "probe": {
        "handler": ("service", "DevelopmentService", "probe"),
        "summary": "Evaluate one node on saved pixels without executing its actions.",
        "effects": ["writes a probe record; sends no UI input"],
        "prerequisites": ["live backend session", "saved observation", "existing node"],
        "returns": ["probe_id", "observation_id", "node", "definition", "result"],
        "example": {"session_id": "session_ID", "observation_id": "obs_ID", "node": "HomeVisible"},
        "notes": [
            "Historical observations are valid recognition inputs after reopening a session.",
            "Check result.hit. Candidate lists may be nonempty even when hit is false.",
            "override contains fields for this node, not a node-name-to-fields mapping.",
            "Custom, And/Or and live-context ROI references are unsupported offline.",
        ],
    },
    "run": {
        "handler": ("service", "DevelopmentService", "run"),
        "summary": "Run the current Pipeline source with a finite native execution budget.",
        "effects": [
            "executes the project's configured actions",
            "records source-linked run evidence",
        ],
        "prerequisites": [
            "live session_id",
            "existing entry node",
            "appropriate actual starting UI",
        ],
        "returns": [
            "run_id",
            "execution_status",
            "goal_status",
            "report_path",
            "scope",
            "runtime",
            "failure",
            "verification_failure",
        ],
        "example": {
            "session_id": "session_ID",
            "entry": "Start",
            "goal": "HomeVisible",
            "timeout_seconds": 20,
        },
        "notes": [
            "Resources reload from disk. override maps node names to field objects and is temporary.",
            "goal must be observable recognition; it is checked independently without actions.",
            "Native node timeouts are milliseconds; timeout_seconds here is seconds.",
            "A timeout cooperatively stops work; it does not undo UI or server-side state.",
        ],
    },
    "asset.extract": {
        "handler": ("service", "DevelopmentService", "asset_extract"),
        "summary": "Crop a source-tracked template from a saved observation.",
        "effects": ["creates resource/image/NAME.png and dev/assets/NAME.json; never overwrites"],
        "prerequisites": ["saved observation", "in-bounds box", "unused asset name"],
        "returns": ["image_path", "sha256", "source_sha256", "observation_id", "box"],
        "example": {
            "observation_id": "obs_ID",
            "box": [220, 140, 200, 60],
            "name": "MailboxButton",
        },
    },
    "case.check": {
        "handler": ("service", "DevelopmentService", "case_check"),
        "summary": "Check positive and negative labeled screenshots with native recognition.",
        "effects": ["loads resources and images; sends no UI input"],
        "prerequisites": ["live backend session", "case manifest and images inside the project"],
        "returns": ["passed", "scope", "cases"],
        "example": {"session_id": "session_ID", "cases": "dev/cases/mailbox.json"},
        "notes": [
            "Manifest is [{node,image,expected_hit}]; image paths are relative to it. Offline checks do not prove a live task completed."
        ],
    },
    "artifact.read": {
        "handler": ("service", "DevelopmentService", "artifact_read"),
        "summary": "Read an existing immutable-identity record by kind and ID.",
        "effects": [],
        "prerequisites": ["saved artifact ID; no live session needed"],
        "returns": ["the saved record"],
        "example": {"kind": "observations", "id": "obs_ID"},
        "notes": ["Use report show for a compact run summary before reading a full run record."],
    },
    "task.create": {
        "handler": ("tasks", "TaskManager", "create"),
        "summary": "Save an objective and executable acceptance contract.",
        "effects": ["creates dev/tasks/NAME.json; never overwrites"],
        "prerequisites": ["unused name", "entry and observable goal planned for the project"],
        "returns": ["name", "state", "task_path", "next_action"],
        "example": {
            "name": "mailbox",
            "objective": "Open mailbox and return home",
            "entry": "Start",
            "goal": "HomeVisible",
        },
        "notes": [
            "scenarios maps required scenario names to observable start-recognition nodes.",
            "Natural-language constraints are recorded, not automatically verified policies.",
        ],
    },
    "task.list": {
        "handler": ("tasks", "TaskManager", "list"),
        "summary": "List durable objectives and their current verification states.",
        "effects": [],
        "prerequisites": [],
        "returns": ["tasks"],
        "example": {},
    },
    "task.context": {
        "handler": ("tasks", "TaskManager", "context"),
        "summary": "Recover the objective, latest evidence and remaining work after interruption.",
        "effects": [],
        "prerequisites": ["saved task name; no live session needed"],
        "returns": [
            "state",
            "scope",
            "latest_attempt",
            "remaining_attempts",
            "missing_scenarios",
            "next_action",
        ],
        "example": {"name": "mailbox"},
        "notes": ["Edited sources, missing reports or new failures invalidate old acceptance."],
    },
    "task.run": {
        "handler": ("service", "DevelopmentService", "task_run"),
        "summary": "Validate a saved task and named starting state against current source files.",
        "effects": [
            "consumes one durable attempt",
            "executes configured UI actions and acceptance checks",
        ],
        "prerequisites": [
            "live session_id",
            "saved task",
            "remaining attempt budget",
            "matching starting UI",
        ],
        "returns": ["attempt", "context"],
        "example": {"session_id": "session_ID", "name": "mailbox", "timeout_seconds": 20},
        "notes": [
            "Select scenario when multiple starts are configured; it must pass its start check.",
            "All required scenarios need current proofs for the same backend/device/runtime.",
            "Unlike run, this accepts no temporary override. Save intended source edits first.",
        ],
    },
}

_RECOVERY = {
    "BROWSER_UNAVAILABLE": {
        "next": "doctor",
        "instruction": "Check the browser extra and executable; install matching Chromium with browser install.",
    },
    "DEVICE_BUSY": {
        "next": "session.list",
        "instruction": "Use the intended existing session or explicitly close it; another project may own the address.",
    },
    "INVALID_RESOURCE": {
        "next": "doctor",
        "instruction": "Check the configured in-project resource path. Initialize only a new project; preserve existing files.",
    },
    "SESSION_NOT_FOUND": {
        "next": "daemon status",
        "instruction": "Confirm a stable compatible owner, read task.context, then reopen the intended backend once and observe. Repeated session.open calls cannot repair a short-lived daemon.",
    },
    "STALE_OBSERVATION": {
        "next": "observe",
        "instruction": "Inspect the replacement observation or capture anew; do not retry old coordinates.",
    },
    "OCR_UNAVAILABLE": {
        "next": "observe",
        "instruction": "Inspect saved pixels or set ocr=false; provision project OCR models before expecting text.",
    },
    "UNSUPPORTED_PROBE": {
        "next": "reference --topic recognition",
        "instruction": "Use supported saved-image recognition or test the contextual node in a bounded run.",
    },
    "NODE_NOT_FOUND": {
        "next": "reference --topic pipeline",
        "instruction": "Read or create the named source node before trying again.",
    },
    "TASK_BUDGET_EXHAUSTED": {
        "next": "task.context",
        "instruction": "Review evidence and the declared budget; do not silently weaken checks or reset history.",
    },
    "TASK_NOT_FOUND": {
        "next": "task.list",
        "instruction": "Use the saved task's exact name, or create the intended acceptance contract first.",
    },
    "DAEMON_VERSION_MISMATCH": {
        "next": "context",
        "instruction": "Read saved context. For your own service, stop the old build and establish a persistent compatible owner before reopening connections. For an externally managed owner, report the mismatch to its runner; do not stop or replace it.",
    },
    "DAEMON_UNAVAILABLE": {
        "next": "daemon status",
        "instruction": "Inspect saved actions/runs before retrying; input may already have occurred. Repair the service lifetime before opening another session; preserve an externally managed owner.",
    },
    "DAEMON_REQUIRES_SUPERVISOR": {
        "next": "daemon serve",
        "instruction": "Start once in a long-lived exec or PTY session and let the tool yield while the foreground command keeps running. In a separate call confirm daemon status is running and compatible before session.open. Shell & alone may be killed with its command sandbox.",
    },
    "SESSION_OWNER_LOST": {
        "next": "context",
        "instruction": "Read saved evidence first. Restore a persistent owner, confirm daemon status, then open one new session and observe the current UI. Do not loop session.open or replay old inputs; an external supervisor must restore its own owner.",
    },
    "EXTERNAL_DAEMON_CONFIGURATION": {
        "next": "context",
        "instruction": "The owning runner must supply a valid external-project/PID environment pair. Preserve evidence and report the configuration failure; do not invent PID values or start a replacement daemon.",
    },
    "EXTERNAL_DAEMON_PROJECT_MISMATCH": {
        "next": "context",
        "instruction": "Use the project assigned by the owning runner. Report the mismatch; do not replace its daemon or change the external-owner environment to bypass it.",
    },
    "EXTERNAL_DAEMON_UNAVAILABLE": {
        "next": "context",
        "instruction": "Read saved evidence and report the unavailable owner to the supervising runner. Do not run daemon serve, restart it, or loop session.open.",
    },
    "EXTERNAL_DAEMON_PID_MISMATCH": {
        "next": "context",
        "instruction": "The external owner was replaced. Preserve evidence and report to the supervising runner; do not attach to the replacement or change its expected PID.",
    },
    "EXTERNAL_DAEMON_MANAGED": {
        "next": "session.close",
        "instruction": "Close your device sessions when finished; only the owning runner may stop or replace the externally managed daemon.",
    },
}

_LOCAL_TASK_METHODS = {"task.create", "task.list", "task.context"}
_OWNER_ERRORS = [
    "DAEMON_REQUIRES_SUPERVISOR",
    "SESSION_OWNER_LOST",
    "EXTERNAL_DAEMON_CONFIGURATION",
    "EXTERNAL_DAEMON_PROJECT_MISMATCH",
    "EXTERNAL_DAEMON_UNAVAILABLE",
    "EXTERNAL_DAEMON_PID_MISMATCH",
]

_ERRORS = {
    "session.open": [
        "DAEMON_VERSION_MISMATCH",
        "DEVICE_BUSY",
        "BROWSER_UNAVAILABLE",
        "INVALID_RESOURCE",
    ],
    "act": ["STALE_OBSERVATION", "SESSION_NOT_FOUND", "DAEMON_UNAVAILABLE"],
    "observe": ["SESSION_NOT_FOUND", "OCR_UNAVAILABLE"],
    "probe": ["SESSION_NOT_FOUND", "NODE_NOT_FOUND", "UNSUPPORTED_PROBE"],
    "run": ["SESSION_NOT_FOUND", "NODE_NOT_FOUND", "DAEMON_UNAVAILABLE"],
    "case.check": ["SESSION_NOT_FOUND", "NODE_NOT_FOUND", "UNSUPPORTED_PROBE"],
    "task.run": ["SESSION_NOT_FOUND", "TASK_BUDGET_EXHAUSTED", "DAEMON_UNAVAILABLE"],
    "task.context": ["TASK_NOT_FOUND"],
}

_CLI_EXAMPLES = {
    "session.open": "session open --backend browser --url http://127.0.0.1:8765",
    "session.list": "session list",
    "session.close": "session close --session SESSION_ID",
    "observe": "observe --session SESSION_ID",
    "act": "act --session SESSION_ID --observation OBS_ID --target e2",
    "probe": "probe --session SESSION_ID --observation OBS_ID --node HomeVisible",
    "run": "run --session SESSION_ID --entry Start --goal HomeVisible --timeout-seconds 20",
    "asset.extract": "asset extract --observation OBS_ID --box 220,140,200,60 --name MailboxButton",
    "case.check": "case check --session SESSION_ID --cases dev/cases/mailbox.json",
    "artifact.read": "artifact --kind observations --id OBS_ID",
    "task.create": 'task create mailbox --objective "Open mailbox and return home" --entry Start --goal HomeVisible',
    "task.list": "task list",
    "task.context": "task context mailbox",
    "task.run": "task run mailbox --session SESSION_ID --timeout-seconds 20",
}


def _handler(method: str) -> Any:
    module, owner, member = _METHODS[method]["handler"]
    return getattr(getattr(importlib.import_module(f"vibe_maa.{module}"), owner), member)


def _type_schema(annotation: Any) -> dict[str, Any]:
    origin, arguments = get_origin(annotation), get_args(annotation)
    if annotation is Any:
        return {}
    if annotation is type(None):
        return {"type": "null"}
    if origin in {types.UnionType, Union}:
        return {"anyOf": [_type_schema(item) for item in arguments]}
    if origin is list:
        return {"type": "array", "items": _type_schema(arguments[0])}
    if origin is dict:
        return {"type": "object", "additionalProperties": _type_schema(arguments[1])}
    return {"type": {str: "string", int: "integer", float: "number", bool: "boolean"}[annotation]}


def _schema(method: str) -> dict[str, Any]:
    handler = _handler(method)
    annotations = get_type_hints(handler)
    properties, required = {}, []
    for name, parameter in inspect.signature(handler).parameters.items():
        if name == "self":
            continue
        properties[name] = _type_schema(annotations[name])
        if parameter.default is inspect.Parameter.empty:
            required.append(name)
        else:
            properties[name]["default"] = parameter.default
    schema = {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }
    if method == "session.open":
        properties["backend"]["enum"] = ["demo", "adb", "browser"]
        properties["initial_state"]["enum"] = ["home", "mailbox", "popup"]
        for name in ["width", "height"]:
            properties[name].update(minimum=64, maximum=4096)
        schema["allOf"] = [
            {
                "if": {"properties": {"backend": {"const": "adb"}}},
                "then": {"required": ["device"], "properties": {"device": {"minLength": 1}}},
            },
            {
                "if": {"properties": {"backend": {"const": "browser"}}},
                "then": {
                    "required": ["url"],
                    "properties": {"url": {"type": "string", "pattern": "^https?://"}},
                },
            },
            {
                "if": {"properties": {"backend": {"const": "demo"}}},
                "then": {"properties": {"resource_path": {"const": "resource"}}},
            },
        ]
    if method in {"run", "task.run"}:
        properties["timeout_seconds"].update(exclusiveMinimum=0, maximum=300)
    if method == "run":
        properties["override"]["anyOf"][0]["additionalProperties"] = {"type": "object"}
    if method in {"act", "asset.extract"}:
        box = {
            "type": "array",
            "items": {"type": "integer"},
            "minItems": 4,
            "maxItems": 4,
            "prefixItems": [{"minimum": 0}, {"minimum": 0}, {"minimum": 1}, {"minimum": 1}],
        }
        if method == "asset.extract":
            properties["box"].update(box)
            properties["name"].update(pattern="^[A-Za-z0-9_-]{1,100}$")
        else:
            properties["box"]["anyOf"] = [box, {"type": "null"}]
            properties["type"]["enum"] = ["click", "swipe", "key", "text"]
            schema["oneOf"] = [
                {
                    "required": ["target"],
                    "properties": {
                        "target": {"type": "string", "minLength": 1},
                        "box": {"type": "null"},
                    },
                },
                {"required": ["box"], "properties": {"box": box, "target": {"type": "null"}}},
            ]
            schema["allOf"] = [
                {
                    "if": {"properties": {"type": {"const": "swipe"}}, "required": ["type"]},
                    "then": {
                        "required": ["x2", "y2"],
                        "properties": {
                            "x2": {"type": "integer", "minimum": 0},
                            "y2": {"type": "integer", "minimum": 0},
                            "duration": {"minimum": 1, "maximum": 10000},
                        },
                    },
                },
                {
                    "if": {"properties": {"type": {"const": "key"}}, "required": ["type"]},
                    "then": {
                        "required": ["key"],
                        "properties": {"key": {"type": "integer", "minimum": 0}},
                    },
                },
                {
                    "if": {"properties": {"type": {"const": "text"}}, "required": ["type"]},
                    "then": {
                        "required": ["text"],
                        "properties": {"text": {"type": "string", "maxLength": 10000}},
                    },
                },
            ]
    if method == "artifact.read":
        properties["kind"]["enum"] = ["observations", "actions", "probes", "runs"]
    if method.startswith("task.") and "name" in properties:
        properties["name"]["pattern"] = "^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$"
    if method == "task.create":
        properties["max_attempts"].update(minimum=1, maximum=1000)
        for name in ["objective", "entry", "goal"]:
            properties[name]["pattern"] = r"\S"
        properties["scenarios"]["anyOf"][0].update(
            propertyNames={"pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$"},
            additionalProperties={"type": "string", "pattern": r"\S"},
        )
        properties["constraints"]["anyOf"][0]["items"]["pattern"] = r"\S"
    return schema


def show(method: str | None = None) -> dict[str, Any]:
    """Describe supported contracts without opening a project, daemon or device."""
    base = {
        "schema_version": 1,
        "tool": "vibe-maa",
        "version": __version__,
        "readiness": "not_checked",
    }
    if method is not None:
        if method not in _METHODS:
            raise DevError(
                "UNKNOWN_METHOD", f"Unknown capability: {method}", {"next": "vibe-maa capabilities"}
            )
        metadata = _METHODS[method]
        codes = list(_ERRORS.get(method, []))
        if method not in _LOCAL_TASK_METHODS:
            codes.extend(code for code in _OWNER_ERRORS if code != "SESSION_OWNER_LOST")
            if "session_id" in _schema(method)["properties"]:
                codes.append("SESSION_OWNER_LOST")
        recovery = {code: deepcopy(_RECOVERY[code]) for code in codes}
        recovery["INVALID_ARGUMENT"] = {
            "next": f"capabilities --method {method}",
            "instruction": "Check required and conditional parameters against this method's input_schema before retrying.",
        }
        return {
            **base,
            "method": method,
            "summary": metadata["summary"],
            "input_schema": _schema(method),
            "side_effects": metadata["effects"],
            "prerequisites": metadata["prerequisites"],
            "returns": metadata["returns"],
            "notes": metadata.get("notes", []),
            "example": {"method": method, "params": deepcopy(metadata["example"])},
            "cli_example": "vibe-maa --project PATH " + _CLI_EXAMPLES[method],
            "recovery": recovery,
            "invocation": "method/params describe the service contract; use the matching CLI command or SDK dispatch, not a raw unauthenticated socket",
            "failure_delivery": "Request errors use envelope.error. An executed operation can return ok=true with result.failure; check status fields. verification_failure explains an unavailable goal check.",
        }
    return {
        **base,
        "methods": [
            {
                "method": name,
                "summary": metadata["summary"],
                "required": _schema(name)["required"],
                "effect": "writes_or_executes" if metadata["effects"] else "reads",
                "sends_ui_input": name in {"act", "run", "task.run"},
                "details": f"vibe-maa capabilities --method {name}",
            }
            for name, metadata in _METHODS.items()
        ],
        "backends": {
            "adb": {
                "setup": "Android platform-tools and a reachable device address",
                "scope": "device",
            },
            "browser": {
                "setup": "vibe-maa[browser] extra, Chromium, and an http(s) URL",
                "scope": "device",
                "perception": "screenshots only; no DOM/evaluate API",
            },
            "demo": {
                "setup": "demo init creates the packaged resource fixture",
                "scope": "fixture",
                "perception": "synthetic UI/text; real native recognition and execution",
            },
        },
        "status_fields": {
            "ok": "Request handling completed; not proof of action/task success.",
            "status": "act: succeeded/failed; inspect its new observation.",
            "execution_status": "run: succeeded/failed/timeout.",
            "goal_status": "run: passed/failed/unknown, independently checked if requested.",
            "state": "task: draft/needs_validation/accepted/blocked; acceptance covers configured checks only.",
            "scope": "fixture/device for runs; offline_recognition_only for cases. Fixture or offline success does not prove a live-game result.",
        },
        "coordinates": "Full controller screenshot pixels. A box is [x,y,width,height], never [x1,y1,x2,y2].",
        "daemon": {
            "foreground_command": "vibe-maa --project PATH daemon serve",
            "readiness_command": "vibe-maa --project PATH daemon status",
            "lifetime": "Keep daemon serve running in a long-lived exec/PTY tool session; let the tool yield, then use a separate call. Confirm running and compatible before session.open. Shell & alone is insufficient in command-lifetime sandboxes.",
            "owner_loss": "Read context and saved evidence, repair service lifetime, then open one new session and observe. Never loop session.open or automatically replay old inputs.",
            "external_owner": "When a runner provides VIBE_MAA_EXTERNAL_DAEMON_PROJECT and VIBE_MAA_EXTERNAL_DAEMON_PID, attach only to that owner. The runner manages its lifetime; do not serve, stop, replace it or change these values.",
            "stop_recovery": deepcopy(_RECOVERY["EXTERNAL_DAEMON_MANAGED"]),
        },
        "cli_tools": [
            "context [--task NAME]: bounded project orientation and retained evidence",
            "daemon serve|status|stop: persistent foreground owner, readiness and shutdown; externally managed owners belong to their runner",
            "reference --topic pipeline|recognition|actions|errors: offline protocol examples",
            "doctor / devices: readiness checks and ADB discovery",
            "agent show|install|export: bundled Skill integration",
            "demo init --starter: intentionally incomplete, repeatable fixture project",
            "target prepare|serve drawio: explicit pinned-app download/local serving",
            "browser install: explicit Chromium installation",
            "report list|show|export and export: compact evidence and portable project handoff",
        ],
        "next": "Read context and doctor. Establish or attach to a persistent compatible daemon, select an explicit backend, then observe. Resume with task.context and focused method contracts.",
    }

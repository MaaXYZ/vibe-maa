"""Bounded tool activity, never model reasoning or opaque authentication payloads."""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any, BinaryIO

MAX_TRACE_BYTES = 1024 * 1024
MAX_OUTPUT_BYTES = 6000
TOOL_TYPES = {"command_execution", "file_change", "mcp_tool_call", "web_search", "image_view"}
EVENT_TYPES = {
    "thread.started",
    "turn.started",
    "turn.completed",
    "turn.failed",
    "error",
    "item.started",
    "item.updated",
    "item.completed",
}
PRIVATE_KEYS = {
    "authorization",
    "authentication",
    "auth",
    "auth_token",
    "authtoken",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "id_token",
    "token",
    "secret",
    "client_secret",
    "secrets",
    "password",
    "session_token",
    "sessiontoken",
    "encrypted_content",
    "encryptedcontent",
    "cookie",
    "set_cookie",
    "reasoning",
    "reasoning_content",
    "analysis",
    "thoughts",
}
PRIVATE_LINE = re.compile(
    r"(?i)(?:authorization|authentication|api[_-]?key|(?:access|refresh|session|id|auth)[_-]?token|"
    r"\btoken|\bauth|\bsecret|client[_-]?secret|password|encrypted[_-]?content|set-cookie|cookie|"
    r"reasoning_content)\s*[\"']?\s*[:=]"
)


def _strip_private(value: Any) -> Any:
    if isinstance(value, dict):
        kind = value.get("type")
        if isinstance(kind, str) and kind in {"reasoning", "thinking", "analysis"}:
            return {"type": kind, "content": "[omitted model reasoning]"}
        return {
            key: _strip_private(item)
            for key, item in value.items()
            if key.lower().replace("-", "_") not in PRIVATE_KEYS
        }
    if isinstance(value, list):
        return [_strip_private(item) for item in value]
    return value


def _strip_embedded_json(text: str) -> str:
    """Sanitize complete JSON objects even when a tool concatenates them with native logs."""
    decoder = json.JSONDecoder()
    pieces = []
    cursor = 0
    for match in re.finditer(r"[\[{]", text):
        position = match.start()
        if position < cursor:
            continue
        try:
            value, end = decoder.raw_decode(text, position)
        except ValueError:
            continue
        pieces.extend(
            (text[cursor:position], json.dumps(_strip_private(value), ensure_ascii=False))
        )
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces)


def safe_text(value: Any, secret: str | None, limit: int = MAX_OUTPUT_BYTES) -> str:
    text = (
        value if isinstance(value, str) else json.dumps(_strip_private(value), ensure_ascii=False)
    )
    if secret:
        text = text.replace(secret, "[REDACTED]")
    try:
        structured = json.loads(text)
    except (ValueError, TypeError):
        pass
    else:
        text = json.dumps(_strip_private(structured), ensure_ascii=False)
    text = _strip_embedded_json(text)
    text = "\n".join(
        "[REDACTED private/auth field]" if PRIVATE_LINE.search(line) else line
        for line in text.splitlines()
    )
    text = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*", "Bearer [REDACTED]", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}", "[REDACTED credential]", text)
    text = re.sub(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "[REDACTED token]", text)
    encoded = text.encode("utf-8")
    return encoded[:limit].decode("utf-8", errors="ignore") + (
        "\n[truncated]" if len(encoded) > limit else ""
    )


def retained_command_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    completed = {
        item.get("item_id", f"record_{index}"): item
        for index, item in enumerate(records)
        if item.get("tool_type") == "command_execution" and item.get("event") == "item.completed"
    }
    return {
        "completed_command_items": len(completed),
        "failed_command_items": sum(
            type(item.get("exit_code")) is int and item["exit_code"] != 0
            for item in completed.values()
        ),
        "command_failure_measurement": "retained_completed_command_items",
    }


class ActivityTrace:
    def __init__(self, secret: str | None = None, max_bytes: int = MAX_TRACE_BYTES):
        self.secret = secret
        self.max_bytes = max_bytes
        self.events: list[dict[str, Any]] = []
        self.event_types: Counter = Counter()
        self.tool_ids: set[str] = set()
        self.errors: list[dict[str, Any]] = []
        self.dropped = 0
        self.used_bytes = 0
        self.completed_turns = 0
        self.usage: Counter = Counter()
        self.usage_last_turn: dict[str, int] | None = None
        self.terminal_state: str | None = None
        self.terminal_error: dict[str, Any] | None = None
        self._pending = b""

    def read(self, stream: BinaryIO, *, final: bool = False) -> None:
        self._pending += stream.read()
        parts = self._pending.split(b"\n")
        self._pending = parts.pop()
        if final and self._pending:
            parts.append(self._pending)
            self._pending = b""
        for part in parts:
            try:
                event = json.loads(part)
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(event, dict):
                self.add(event)

    def _error(self, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return {
                key: safe_text(value[key], self.secret, 2000)
                for key in ["message", "code", "type"]
                if key in value
            }
        return {"message": safe_text(value, self.secret, 2000)}

    def add(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if not isinstance(kind, str) or kind not in EVENT_TYPES:
            return
        self.event_types[kind] += 1
        if kind == "turn.started":
            self.terminal_state = "started"
            self.terminal_error = None
        elif kind == "turn.failed":
            self.terminal_state = "failed"
            self.terminal_error = self._error(event.get("error", event.get("message", "Failed")))
        elif kind == "turn.completed":
            self.terminal_state = "completed"
            self.terminal_error = None
            self.completed_turns += 1
            usage = event.get("usage", {})
            if isinstance(usage, dict):
                self.usage_last_turn = {
                    key: value
                    for key, value in usage.items()
                    if key
                    in {
                        "input_tokens",
                        "cached_input_tokens",
                        "cache_read_input_tokens",
                        "cache_write_input_tokens",
                        "output_tokens",
                        "reasoning_output_tokens",
                        "reasoning_tokens",
                        "total_tokens",
                    }
                    and type(value) is int
                    and value >= 0
                }
                self.usage.update(self.usage_last_turn)
        if kind in {"error", "turn.failed"}:
            record = {
                "event": kind,
                **self._error(event.get("error", event.get("message", "Failed"))),
            }
            self.errors.append(record)
            self.errors = self.errors[-10:]
        item = event.get("item")
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("type"), str)
            or item["type"] not in TOOL_TYPES
        ):
            return
        record = {"event": kind, "tool_type": item["type"]}
        identifier = item.get("id")
        if isinstance(identifier, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", identifier):
            self.tool_ids.add(identifier)
            record["item_id"] = identifier
        for key in ["status", "command", "server", "tool", "query"]:
            if key in item:
                record[key] = safe_text(item[key], self.secret, 2000)
        if type(item.get("exit_code")) is int:
            record["exit_code"] = item["exit_code"]
        if "aggregated_output" in item:
            record["output"] = safe_text(item["aggregated_output"], self.secret)
        changes = item.get("changes")
        if isinstance(changes, list):
            record["changes"] = [
                {
                    key: safe_text(change[key], self.secret, 500)
                    for key in ["kind", "path"]
                    if key in change
                }
                for change in changes[:20]
                if isinstance(change, dict)
            ]
        size = len(json.dumps(record, ensure_ascii=False).encode())
        if self.used_bytes + size > max(0, self.max_bytes - 16384):
            self.dropped += 1
            return
        self.events.append(record)
        self.used_bytes += size

    def progress(self, elapsed: float) -> dict[str, Any]:
        return {
            "event": "agent.progress",
            "elapsed_seconds": int(elapsed),
            "parsed_events": sum(self.event_types.values()),
            "tool_items": len(self.tool_ids),
        }

    def result(self) -> dict[str, Any]:
        result = {
            "schema_version": 1,
            "scope": "tool_activity_only_no_model_reasoning",
            "event_count": sum(self.event_types.values()),
            "event_types": dict(self.event_types),
            "tool_items": len(self.tool_ids),
            "records": list(self.events),
            "truncated_records": self.dropped,
            "byte_budget": self.max_bytes,
            "terminal_state": self.terminal_state,
            "historical_errors": self.errors,
            "usage": dict(self.usage),
            "usage_last_turn": self.usage_last_turn,
            "completed_turns": self.completed_turns,
            "usage_measurement": "sum_of_reported_turn.completed_events",
        }
        while True:
            result.update(retained_command_metrics(result["records"]))
            if (
                len(json.dumps(result, ensure_ascii=False).encode()) <= self.max_bytes
                or not result["records"]
            ):
                return result
            result["records"].pop(0)
            result["truncated_records"] += 1

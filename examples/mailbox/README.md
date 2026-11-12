# Resettable mailbox fixture

This demo needs no ADB device or OCR model download. Its in-memory 640×360 UI has
`home`, `mailbox`, and `popup` states. The synthetic observation OCR reads labels
from the supplied frame; it does not inspect the current live state.

Pipeline execution and ColorMatch probes use the installed **real MaaFramework**
Tasker through a CustomController. Native OCR recognition still requires OCR
models in the normal MaaFW resource path.

The UI supports clicking and Back/Escape keys. Swipe, text input, app launching,
and other keys fail explicitly because this fixture has no corresponding UI.

`OpenMailbox` opens the mailbox and returns home. It handles all three starting
states. To reproduce a missing announcement branch, pass this run override:

```json
{"OpenMailbox": {"next": ["ClickMailbox", "MailboxVisible"]}}
```

From `initial_state=popup`, the incomplete flow fails. Its native recognition
events retain the announcement frame. Remove the override (or add
`DismissAnnouncement` to the next list) to repair the flow. A new demo session
starts with a fresh state; resetting this fixture does not imply real games can
restore server-side state.

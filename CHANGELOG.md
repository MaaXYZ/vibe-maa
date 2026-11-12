# Changelog

## 0.3.0

- Add compact capability discovery with method-specific JSON Schemas, examples, prerequisites and recovery guidance; include a generic ProjectInterface handoff reference and explicit browser display flags.
- Add a bounded, read-only project context snapshot to help coding agents resume without scanning full histories or reconnecting devices.
- Preserve structured action, execution and verification failures in saved evidence and compact reports.
- Make the README a product introduction and move operational details into the Agent Skill and dedicated documentation.
- Allow per-run reasoning-effort selection in real-agent CI and record configuration provenance, elapsed time and tool activity.
- Keep device services alive across coding-agent PID namespaces with foreground serving and externally supervised CI ownership; reject lost-session retries without replacing the owner.
- Redact authentication fields in mixed native logs and JSON, and keep metrics consistent with the final published activity trace.
- Preserve reference-assisted model results explicitly, and use a real sandbox preflight to block access to prior solutions and privileged acceptance code before independent trials.

## 0.2.0

- Include the mailbox starter, positive/negative screenshot cases, and Agent Skill in installed distributions.
- Add project-local Codex/Claude Skill installation, environment diagnostics, ADB discovery, and same-runtime Chromium provisioning.
- Preserve development tasks, attempt budgets, current-source acceptance, and resumable context independently of live device sessions.
- Add compact run reports, checksummed portable evidence archives, and ordinary MaaFW project export.
- Add an optional screenshot/input browser backend and a pinned draw.io target for real-application acceptance.
- Label fixture/device evidence explicitly and reject stale resident services after package upgrades.
- Build and smoke-test installed distributions in CI, and prepare draft GitHub Releases from versioned tags.
- Keep external coding-agent acceptance separate from deterministic tests and document its required model configuration.

## 0.1.0

- Initial persistent service, JSON CLI, ADB adapter, same-frame observations and guarded inputs.
- Native recognition-only probes, bounded Pipeline runs, original-frame evidence and independent goal checks.
- Deterministic mailbox UI driven by the real MaaFramework runtime.
- Ubuntu/Windows and Python 3.11/3.12 core coverage.

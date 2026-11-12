# Architecture

`vibe-maa` is a development environment for an external coding agent. The agent edits ordinary MaaFramework resources and uses the CLI to gather observations, perform experiments, and check its changes. The project does not host a model or introduce another pipeline language.

## Ownership and transport

A project-local daemon owns live sessions and their native controllers. Independent CLI processes communicate with that daemon over an authenticated loopback TCP connection. The daemon metadata file carries the protocol version, port, PID, and random token; on POSIX its permissions are `0600`. Operating-system locks protect startup and lifetime ownership.

The service serializes operations, including observation and recognition, so concurrent requests cannot interleave device mutations. Health checks remain responsive during an operation. The client never automatically replays a mutation when a response is lost: the device may already have received it.

ADB sessions retain a per-user OS file lock keyed by the normalized endpoint across project daemons. This prevents two projects from controlling the same address; different aliases for the same physical device cannot always be identified. Shutdown rejects queued operations before dispatch while allowing an already-running bounded operation to finish.

The daemon's public request envelope is versioned. Application errors carry stable codes. A successful RPC means that a request was processed, not that a device action or business task succeeded.

## Observations and actions

An observation contains an immutable PNG, SHA256, capture timestamp, screenshot dimensions, session identity, mutation generation, and optional OCR results from those exact pixels. Images retain MaaFramework controller coordinates. Image arrays inside adapters are BGR `uint8`; PNGs are stored in RGB.

Before input, the service verifies the source session, generation, and age. Click and swipe targets additionally use a local image comparison against a fresh capture. Changes elsewhere in an animated screen do not invalidate a stable local target. This is a heuristic rather than a semantic guarantee, and a screen can still change between checking and dispatching input.

Key and text input also require an explicit target or guard box. Saved observation images are checked against their recorded SHA256 before reuse for input, probes, or asset extraction.

Action intent and its before observation are persisted before input dispatch. Every attempted mutation advances the generation, even if its API reports failure. A final result and after observation are then saved when available. A process interruption can leave a pending record; callers must inspect evidence rather than assuming the action did not occur.

History remains available after daemon restart; live sessions expire. Historical pictures are useful recognition inputs but are not valid authorization to dispatch new device input.

## Recognition and native execution

The MaaFramework backend retains its controller and reloads a fresh Resource and Tasker for each experiment. This picks up edited files without carrying previous experiment overrides forward.

`probe` passes a saved image to MaaFramework's recognition-only API. A node's action and successors are never executed. The result uses the native `hit` value; a nonempty candidate list is not sufficient proof of a match. The first version rejects unsupported offline semantics such as Custom, And/Or, and context-dependent ROI references instead of guessing their meaning.

`run` delegates ordinary Pipeline execution to the real native Tasker with a finite budget. Debug mode is enabled inside the dedicated daemon to retrieve original recognition images. Native event callbacks enqueue records; image retrieval, serialization, and disk writes happen outside those callbacks. Missing or truncated evidence is explicit.

The first version does not launch AgentServer processes or register project-specific Custom handlers. A native task requiring such handlers needs that integration before it can run successfully.

Experiment records include source hashes, the requested override, pre/post observations, native events, recognition details, images when retained, and execution outcome. `events.jsonl` preserves event progress while the final report is being assembled. Configuration hashes establish which files were present; they are not a replacement for resolved contextual state or a byte-for-byte runtime snapshot of every dependency.

All resource files are hashed, JSON/JSONC source bytes are copied into the run's `input-files/`, and the MaaFW runtime version is recorded. Resource changes during execution invalidate the goal verification. Each report retains at most 4096 regular events and 256 regular images, with explicit omission counts. The last available original recognition image is retained separately so an exhausted regular image budget does not hide the final scene.

Task deadlines include cooperative native task stopping. Controller and resource operations have separate deadlines and quarantine a session on timeout. A completely stuck native thread would require process isolation for a hard termination guarantee; no account or server state is rolled back.

## Completion and validation

Execution and business completion are separate fields:

- `execution_status`: native task success, failure, or tool timeout.
- `goal_status`: a separately requested recognition check passed, failed, or could not be established.

A missing goal is `unknown`. A goal cannot be unconditional DirectHit. Goals are checked against the final saved observation, without running goal-node actions. A passing goal only proves the configured observable condition. The agent must not silently weaken that condition to make a test pass.

Offline cases label screenshot/node pairs with explicit expected hits. They test recognition, including false positives on negative examples. They do not simulate the application's reaction to input, restore an account's server-side state, or prove live end-to-end behavior.

## Resettable fixture

The mailbox fixture renders a deterministic in-memory UI through MaaFramework CustomController. Its observation OCR decodes the supplied frame so it can describe old observations without consulting current UI state. Its pipelines and probes run through the same native backend as real-device sessions; no parallel Pipeline interpreter exists.

Opening a fresh demo session selects `home`, `mailbox`, or `popup` as its starting state. This provides repeatable tests of missing transitions, stale references, timeout evidence, and repair. It does not imply that a real game's daily rewards or server state can be reset.

## Project layout

```text
interface.json                  ordinary ProjectInterface metadata
resource/pipeline/              ordinary MaaFW nodes
resource/image/                 deliverable image assets
resource/model/ocr/             optional user-provided OCR models
dev/cases/                     labeled offline cases
dev/assets/                    extracted asset provenance
.maa-dev/observations/          saved frames and metadata
.maa-dev/actions/               intents and action outcomes
.maa-dev/probes/                saved recognition experiments
.maa-dev/runs/                  reports, events, and recognition images
.maa-dev/native-logs/           MaaFW logs
```

Runtime records are ignored by Git. Stable cases and asset provenance may be curated into the project. Exported automation continues to run with MaaFramework independently from the development daemon.

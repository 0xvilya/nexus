# Security model

The current worker grants READ/CREATE/UPDATE within its own workspace. It rejects DELETE/EXECUTE/SHARE/PUBLISH and unknown RPC methods. No arbitrary path RPC or shell endpoint exists. The note compiler copies user text into a file; it does not treat that text as instructions to another tool.

Path checks reject absolute/UNC/drive paths, traversal, Windows device names, ADS syntax, trailing dot/space components and reparse/symlink components. Resolved paths must remain beneath the workspace. These checks do not provide an OS sandbox or eliminate races against a hostile process modifying the directory tree. A native handle-based broker is required before untrusted concurrent writers are supported.

Development HTTP binds only to 127.0.0.1 and requires the exact Host/Origin plus a random process nonce on POST. It has no CORS grants. Assets are served from one root and CSP blocks remote scripts/frames. This is not an internet service or authenticated multi-user server. An attacker already running as the same Windows user remains outside the current isolation guarantees.

Native inference binds loopback, uses a random in-memory API key, ignores inherited LLAMA_* settings and bypasses proxy settings for loopback calls. No prompt/header logs are written. The process is owned by a Windows kill-on-close job. Cancellation terminates/unloads the model. Job cleanup after worker crash is integration-tested.

Downloads are allowlisted by the shipped catalog, pinned by revision/hash/length, and never register partial or corrupt files. Model download needs explicit license consent. Runtime/model updates require a reviewed catalog change. HTTPS plus pinned integrity protects retrieval; there is no digital signature claim or complete software-supply-chain audit.

No accounts, tokens, microphone, recording, network control, cloud inference, telemetry or public listeners are configured. Future connectors must use official authentication and Credential Manager/DPAPI; credentials must never enter Git, JSON state or logs. External documents, repositories and service responses remain untrusted data and cannot change policy.

Future work: native path handles and isolation, full parameter schemas, resource budgets, authentication and secret vault, prompt-injection regression fixtures, signed release/update manifests and external security review.


Dataset processing reads only explicitly selected local files or explicitly consented public HF files. Downloads pin revision/size/hash and constrain HTTPS redirects. Metadata/content remain untrusted data; no dataset code is executed. Upload and record/output limits bound individual jobs. Email detection is only a heuristic. Retained inputs/partial outputs consume local disk until removed; retention controls are pending. Source/license declarations do not automatically authorize external publication or training.

The browser now supports bounded visible-element/text assertions and Windows Job ownership. Narrow recognized verification-only requests reject click/type/navigation/app plans before execution. This is not complete natural-language policy enforcement. A real 1.5B planner previously chose a link click for an inspection request; the deterministic restriction addresses this case and is regression-tested.

Iterative browser mode: caller must grant mutation; actual node refs/fingerprints are checked in an isolated execution world. Model options are restricted by observed capabilities and a generated JSON schema; schema/system controls are private internal generation parameters. Main-world page instructions remain untrusted. Shared action lock rejects competing browser RPCs. Pending mutating intent is journaled before dispatch; transport errors stop, recovery never auto-replays it. This is scoped control, not complete natural-language authorization, network isolation or a hostile-code sandbox.

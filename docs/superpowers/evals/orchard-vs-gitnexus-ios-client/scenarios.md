# Scenario Cards

## Coverage Summary

| Family | Scenario IDs |
|--------|--------------|
| Code Location | S1, S2 |
| Impact Analysis | S3, S4 |
| Apple Semantic Accuracy | S5, S6 |
| Flow Understanding | S7, S8 |

## Scenario Card Format

- `task`
- `target`
- `expected_evidence`
- `reference_truth`
- `expected_difficulty`

### S1: Find login flow entry points
- `task`: Find the main login flow entry points in ios-client.
- `target`: Login and authentication startup path.
- `expected_evidence`: At least one concrete entry symbol plus the next two meaningful hops.
- `reference_truth`: `knowledge-base/components/login-and-authentication.md` and cited `file:line` anchors.
- `expected_difficulty`: Retrieval may find auth-adjacent helpers but miss the actual entry path.

### S2: Find meeting join / rejoin control points
- `task`: Locate the main control points for meeting join or rejoin.
- `target`: Meeting join and scene recovery entry logic.
- `expected_evidence`: A concrete control symbol and at least one related scene-recovery or transition symbol.
- `reference_truth`: `knowledge-base/components/meeting-join-and-scene-recovery.md`.
- `expected_difficulty`: Query terms overlap with generic meeting code and can produce noisy results.

### S3: Estimate impact of changing a login-state decision
- `task`: Estimate what breaks if a login-state decision point changes.
- `target`: A login-state branching symbol chosen from the knowledge-base truth anchors.
- `expected_evidence`: Direct callers or impacted flows plus evidence for why they are linked.
- `reference_truth`: `knowledge-base/components/login-and-authentication.md` and manual source inspection.
- `expected_difficulty`: False-positive callers and missing upstream explanation are both likely.

### S4: Estimate impact of changing a meeting lifecycle method
- `task`: Estimate what breaks if a meeting lifecycle method changes.
- `target`: A lifecycle method referenced by meeting-core or scene-recovery documentation.
- `expected_evidence`: Direct dependents, affected flows, and at least one meeting-related path explanation.
- `reference_truth`: `knowledge-base/components/meeting-core.md` and `knowledge-base/components/meeting-join-and-scene-recovery.md`.
- `expected_difficulty`: Framework callbacks and app lifecycle boundaries can distort impact results.

### S5: Resolve protocol implementation and override chains
- `task`: Resolve a protocol implementation path and any relevant override chain.
- `target`: One protocol-backed service or controller path in ios-client.
- `expected_evidence`: Protocol symbol, implementation symbol, and override or dispatch relation if present.
- `reference_truth`: Maintainer-verified source path plus any matching knowledge-base citations.
- `expected_difficulty`: Generic call graphs often flatten protocol dispatch semantics.

### S6: Resolve a Swift / Objective-C bridge identity
- `task`: Connect one Swift-facing symbol with its Objective-C or bridge-side identity.
- `target`: A cross-language symbol pair known to the maintainer.
- `expected_evidence`: Both symbol identities plus evidence that they refer to the same conceptual target.
- `reference_truth`: Maintainer-approved source anchors and bridging-related source files.
- `expected_difficulty`: Text matching may find both names without proving semantic identity.

### S7: Trace cold start to login screen
- `task`: Explain the flow from app cold start to login screen presentation.
- `target`: Startup and scene lifecycle path ending at login UI.
- `expected_evidence`: Entry point, at least three meaningful intermediate steps, and a recognizable terminal UI handoff.
- `reference_truth`: `knowledge-base/architecture/app-startup-and-scene-lifecycle.md` plus login page citations.
- `expected_difficulty`: Process extraction may stop too early or spill into framework internals.

### S8: Trace notification-driven meeting scene recovery
- `task`: Explain how a notification or external event restores a meeting scene.
- `target`: Notification-driven meeting recovery path.
- `expected_evidence`: Entry trigger, recovery control point, and at least one meeting-scene restoration handoff.
- `reference_truth`: `knowledge-base/components/meeting-join-and-scene-recovery.md`, `knowledge-base/components/apns-and-im-notifications.md`.
- `expected_difficulty`: Cross-subsystem transitions make process grouping and explanation harder.

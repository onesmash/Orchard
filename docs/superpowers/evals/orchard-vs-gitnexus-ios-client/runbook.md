# Runbook

## Before You Start

1. Confirm the `ios-client` knowledge base exists and the reference pages still match the chosen scenarios.
2. Confirm `GitNexus` index metadata is readable in `/Users/hui.xu/Work/SourceCode/Zoom_Client/ios-client/.gitnexus/meta.json`.
3. Confirm orchard can query the same repository or an intentionally comparable graph snapshot.

## Run Order

1. S1-S2 code location
2. S3-S4 impact analysis
3. S5-S6 Apple semantic accuracy
4. S7-S8 flow understanding

## Per-Scenario Rules

- Run both tools on the same task wording before editing the score sheet.
- Record raw query phrases used for each tool in the notes fields.
- Do not mark `success` unless the result surfaces the scenario's expected evidence.
- If a result is partially correct but missing grounding, tag both the score notes and the failure taxonomy.
- Prefer `knowledge-base/` citations, known `file:line` anchors, and maintainer-accepted traces as truth checks.

## After All Scenarios

1. Group failures by taxonomy tag.
2. Summarize which scenario families orchard loses, ties, or wins.
3. Convert repeated failure tags into capability gaps.
4. Convert capability gaps into `Directly Borrow`, `Apple-Specific Rebuild`, or `Explicitly Not Chased`.
5. Write the final results into `report-template.md`.

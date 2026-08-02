# Archived AI Chats

Recovered Copilot chat transcripts for this workspace, from the July 2026 move
of the checkout to a different filesystem.

Retired here from `docs/` because they are raw event records rather than
documentation: what they concluded already lives in
`docs/moondancer/riscv_alternatives.md`, and nothing else in the tree cites
them. Kept rather than deleted because they cannot be regenerated -- the source
below is per-machine editor state, not a repository.

Machine-specific paths in these files were replaced with placeholders
(`<workspace>`, `<repos-root>`, `<home>`, ...) before this repo was published;
the transcripts are otherwise verbatim.

## Source

- VS Code workspace storage ID: c7267954bd2c8b231b54032a6f9ca56a
- Source folder: `<home>/.config/Code - Insiders/User/workspaceStorage/<id>/GitHub.copilot-chat/transcripts/`

## Archived Sessions

1. Session ID: 0d96eff5-b9df-40fc-ab31-da17e9bab40d
   - File: 2026-07-21_0d96eff5-b9df-40fc-ab31-da17e9bab40d.jsonl
   - Start time in transcript: 2026-07-21T12:19:00.343Z
   - Notes: Includes repository/upstream checks and Cynthion repo discovery workflow.

2. Session ID: a3759343-6812-4517-aced-4fd49640f6d1
   - File: 2026-07-21_a3759343-6812-4517-aced-4fd49640f6d1.jsonl
   - Start time in transcript: 2026-07-21T12:53:19.030Z
   - Notes: Includes duplicate/architecture comparison discussion.

## De-duplication Note

- Consolidated RISC-V alternatives and option analysis now live in `docs/moondancer/riscv_alternatives.md`.
- Transcript files are preserved as raw records; this index avoids repeating detailed alternatives content.

## Format

- Files are preserved as raw JSON Lines export from GitHub Copilot Chat transcript storage.
- Each line is an event record (user.message, assistant.message, tool.execution_start, tool.execution_complete, etc.).

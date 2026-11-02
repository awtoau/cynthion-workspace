# History

> **Agents: do not read these files unless the task needs project history.** They are verbose by design. Live docs state the current result only; anything removed from a doc or a code comment is logged here (verbatim, source file named, issue linked where one exists).

## Rules
- A doc or comment holds how it is now. The story of how it got there goes here.
- A superseded measurement is deleted from the live doc and logged here; no live number sits beside a warning that it is wrong.
- One section per source file, newest sections last. Add the issue link when one exists.
- Link syntax inside logged text is disarmed (`] (`) so the link checker ignores it.

## Files
| file | covers |
|---|---|
| [`history/chips-hardware.md`](history/chips-hardware.md) | hardware.md, sbu.md, chip notes: the "claims that keep coming back wrong" table, superseded tables, struck-through passages |
| [`history/upstream-toolchain.md`](history/upstream-toolchain.md) | the five merged upstream-* docs, old install guide, CI/Actions docs, toolchain snapshots and drift tables |
| [`history/soc-usb.md`](history/soc-usb.md) | SoC, USB host, clocking, Apollo plans and investigations, patchset, GSG scenario drafts |

# patches/

Gateware and firmware that was **retired while still working**, kept as an
applicable patch so putting it back is one command rather than an archaeology
session.

    git apply patches/<issue>-<name>.patch

## What belongs here

- removed because it is no longer *used*, not because it is wrong
- the issue number is in the filename, and that issue says why it went and what
  would justify bringing it back
- it applied cleanly to `main` at the commit named in the file's own header

## What does not

- anything deleted as broken or superseded — git history is the record
- anything still referenced by current code
- `debris/`, which is for retired content a user could not recreate. A patch is
  regenerable from git; it lives here because the *convenience* is the point.

## Staleness

**A patch here rots.** It is a diff against the tree as it was, and nothing
re-applies it in CI. Expect to resolve conflicts. If one stops applying and the
feature is still wanted, that is the signal to reimplement rather than to fight
the patch.

| patch | issue | what it restores |
|---|---|---|
| `294-clock-mirror.patch` | [#294](https://github.com/awtoau/cynthion-workspace/issues/294) | divided copies of every clock on PMOD A, for a scope. Off by default; cost was two axes of variant space. |

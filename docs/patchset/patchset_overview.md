## Apollo Modification History

The canonical patch artifact for the current set is:

- [patches/apollo/0000-wip-issue22-apollo-fixes-20260722.diff](../../patches/apollo/0000-wip-issue22-apollo-fixes-20260722.diff)

For the evidence/issue workflow that goes with these changes, see [apollo_change_process.md](../apollo_samd11_mcu/apollo_change_process.md).

### Related Build-System Changes

The build-system work that introduced logging, fail-fast checks, and parallel execution is summarized separately here:

- [0001-improved-build-system-logging-fail-fast-parallelization.md](0001-improved-build-system-logging-fail-fast-parallelization.md)
- [0002-parallel-build-execution-setup-and-build-threading.md](0002-parallel-build-execution-setup-and-build-threading.md)
- [install.md](../install.md)

### 2026-07-22 - Apollo console/UART race hardening

Patch set:
- [0000-wip-issue22-apollo-fixes-20260722.diff](../../patches/apollo/0000-wip-issue22-apollo-fixes-20260722.diff)

Files touched:
- `firmware/src/console.c`
- `firmware/src/main.c`

What changed:
- Buffered UART RX in interrupt context instead of writing directly into TinyUSB.
- Flushed buffered console data from task context.
- Added a fallback path to initialize UART if callbacks were missed.

Why it matters:
- Reduces contention between UART callbacks and TinyUSB critical sections.
- Makes the Apollo console path less sensitive to timing around REPL and `riscv` command handling.

Verification status:
- Track runtime evidence and build output alongside the patch set, not here.
- Use the issue comment / `tmp/` log pattern from [apollo_change_process.md](../apollo_samd11_mcu/apollo_change_process.md).

---
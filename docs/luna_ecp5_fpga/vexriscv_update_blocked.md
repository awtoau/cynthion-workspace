# VexRISCV Update Attempt — Blocked

**Date**: 2026-05-23  
**Status**: ❌ Deferred  
**Current Version**: VexRISCV 1.5-2 years old (last rebuilt April 2024)  

## Verification Status (Issue #51)

- Last verified: 2026-07-22
- Verification owner: cynthion-workspace maintainers
- Scope: re-check blocker validity and risk profile against current workspace/tooling context
- Next review trigger:
	1. first successful end-to-end candidate update build in a reproducible environment, or
	2. explicit decision to allocate migration work for Scala/sbt/JDK upgrade, or
	3. hardware/debug issue that removes confidence in the current frozen baseline

## Objective

Update VexRISCV to latest version to capture bug fixes and potential performance improvements, particularly the JTAG reset state issue (SpinalHDL/VexRiscv#381).

## Findings

### Toolchain Incompatibility

- **Current build uses**: Scala 2.11.12 + SpinalHDL 1.6.0
- **Build tool**: sbt 1.12.11
- **System Java**: OpenJDK 25.0.3
- **Problem**: Scala 2.11.12 is not compatible with Java 25

### Blocker Status Matrix

| Blocker | Status | Evidence | Risk impact |
|---|---|---|---|
| Legacy toolchain mismatch (`Scala 2.11.12` vs `Java 25`) | current | This workspace is currently on `OpenJDK 25.0.3`; prior failed error signature remains documented below. | High: update path still likely to fail without migration work. |
| `JAVA_HOME` workaround behavior for sbt launcher | partial | Prior run showed launcher resolving to Java 25 despite JDK 17 attempt; not re-executed in this pass on the historical update path. | Medium: workaround reliability remains uncertain. |
| Migration/regression scope (Scala/sbt/plugins/RTL differences) | current | Required work items remain unchanged (`build.sbt`, `project/build.properties`, plugin/runtime validation, RTL/regression checks). | High: non-trivial migration and verification effort. |
| Immediate product pressure to migrate now | resolved (for now) | Current decision remains to retain stable baseline; no new evidence forcing urgent migration in this workspace. | Low immediate pressure; revisit only on trigger. |

### Error

```
java.lang.NoClassDefFoundError: Could not initialize class sbt.internal.parser.SbtParser$
bad constant pool index: 0 at pos: 49428
```

### Attempted Mitigation

1. Installed JDK 17 (compatible with Scala 2.11.12)
2. Attempted to use `JAVA_HOME=/usr/lib/jvm/temurin-17-jdk sbt ...`
3. **Result**: sbt launcher still resolved to Java 25 instead of respecting JAVA_HOME

The sbt launcher script does not properly honor JAVA_HOME in the current environment.

## Why This Is a Toolchain Migration

Fixing this properly requires:

1. Update `build.sbt` to Scala 2.12+ or Scala 3.x
2. Update `project/build.properties` 
3. Validate sbt plugin compatibility
4. Re-test generated RTL output
5. Check for synthesis or behavioral differences

This is **not** a simple package update — it's a build system upgrade with real risk of regression.

## Current Status

✅ **Decision**: Keep current stable VexRISCV (April 2024 build)

**Rationale:**
- Existing VexRISCV is proven stable on all 4/4 builds
- Hardware selftest passes all 5/5 checks
- JTAG issue has documented manual workaround
- Migration cost outweighs immediate benefit

## Current Risk Profile (2026-07-22)

Decision guidance remains:
1. Keep the current frozen VexRiscv baseline for practical product-path work.
2. Treat update work as a dedicated migration project, not opportunistic maintenance.
3. Do not reopen update execution until a reproducible migration environment and regression budget are explicitly approved.

Net change from previous document state:
- No recommendation reversal.
- Blocker status now explicitly tagged as current/partial/resolved.
- Review ownership and next-review triggers are now explicit.

## Follow-up

If VexRISCV update becomes necessary later:
- Treat as separate toolchain migration task
- Allocate time for Scala/sbt/JDK version updates
- Plan for comprehensive regression testing
- Consider upstream SpinalHDL/VexRiscv deprecation status

## Related Issues

- SpinalHDL/VexRiscv#381 — JTAG TAP FSM state initialization
- Cynthion/luna-soc#0 — VexRISCV version tracking

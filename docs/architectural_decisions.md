## Architectural Decisions

### Core Finding: Unified Entry Point
**Decision:** Create single `cyn` command instead of scattered scripts

**Rationale:**
- Reduces cognitive load (single command to learn)
- Enables AI agent discovery (JSON schema)
- Future GUI can integrate via HTTP daemon
- Consistent for developers and automation

### Daemon-Client Architecture
**Decision:** Smart routing (daemon optional, auto-detected)

**Rationale:**
- Users don't need to think about daemon state
- Faster for sequential commands (cached environment)
- Fallback to inline execution if daemon not running
- Supports future multi-user scenarios

### No-GIL Parallelization
**Decision:** Use Python 3.14 concurrent.futures for parallel builds

**Rationale:**
- 55% speedup over sequential
- No-GIL enables true threading (not just concurrency)
- Scales well to 4+ cores
- Simpler than subprocess-based approach

### Consolidated Documentation
**Decision:** Consolidated docs tree with informative filenames and one optional snapshot (`full.md`)

**Rationale:**
- Easier to maintain (single source of truth)
- Reduces duplication
- Simpler for users to navigate
- Git history is cleaner

---


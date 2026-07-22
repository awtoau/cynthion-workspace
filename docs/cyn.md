# Cyn Entry Point Architecture

**`cyn` = Single unified entry point for AI agents, developers, and automation**

---

## What `cyn` Provides

### Information and Discovery
```bash
cyn list                  # All available commands
cyn status                # Project status (human-readable)
cyn versions              # Tool versions

cyn ai-brief              # AI-friendly project summary
cyn ai-schema             # Machine-readable command schema (JSON)
cyn ai-tasks              # Available tasks for AI agents
```

### Component Control
```bash
cyn fpga sim_test         # Run FPGA simulator
cyn apollo build          # Build Apollo firmware
cyn moondancer build      # Build moondancer
cyn gateware elaborate    # Elaborate gateware
```

### Workspace Setup
```bash
cyn setup                 # Sequential setup
cyn setup --parallel      # Parallel setup
cyn prereqs               # Check prerequisites (fail-fast)
cyn versions              # Show versions
```

### CI/CD Management
```bash
cyn ci install            # Install GitHub Actions runner (act)
cyn ci list               # List available workflows
cyn ci apollo             # Run Apollo CI locally
cyn ci cynthion           # Run Cynthion CI locally
cyn ci luna               # Run Luna CI locally
```

### Global Options
```bash
cyn --json <cmd>          # JSON output (AI-friendly)
cyn --log file <cmd>      # Log to file
cyn --verbose <cmd>       # Verbose output
```

---

## AI Agent Workflow

### Step 1: Discover Project State
```bash
cyn --json ai-brief
```

### Step 2: Get Available Commands
```bash
cyn --json ai-schema
```

### Step 3: See What Tasks Can Be Run
```bash
cyn --json ai-tasks
```

### Step 4: Execute Tasks
```bash
cyn --json apollo build
cyn --log build.log setup
cyn --verbose ci apollo
```

---

## Daemon and Client Architecture

The daemon is implemented (`scripts/cyn-daemon.py`). `cyn` uses **smart routing**:
detects whether the daemon is running; if so, connects via HTTP (fast, cached
environment); otherwise runs commands directly (inline).

```bash
# Daemon lifecycle (runs as background service)
cyn daemon start
cyn daemon stop
cyn daemon status
cyn daemon restart
```

### Daemon HTTP API (port 8765)
- `/health` — health check
- `/status` — daemon status + uptime
- `/project/status` — project state
- `/commands` — available commands list

### Benefits
- GUI layer can connect via socket or HTTP
- Tools can communicate with `cyn` daemon
- Daemon keeps environment cached for faster sequential commands
- Transparent daemon switching; single entry point for all operations
- Better shutdown and lifecycle behavior

### Implementation Strategy
- Phase 1 (done): command-line entry point
- Phase 2 (done): daemon mode with HTTP API and smart routing
- Phase 3 (future): GUI layer on top of daemon

### Active Shutdown Contract (Canonical)

Chosen active runtime path for shutdown semantics:
1. `cyn-daemon` lifecycle (`start` / `stop` / `status` / `restart`) is the canonical active path.
2. Operator flow is process-level lifecycle management, not the historical `{"cmd":"shutdown"}` socket message from archived `apollod` path.

Required behavior contract for active path:
1. `start` acquires daemon PID lock and exposes HTTP status endpoints.
2. `stop` sends SIGTERM and daemon exits cleanly.
3. Immediate relaunch path is supported (`start` after `stop` without stale lock state).
4. `status` accurately reports running/not-running state.

Validation snapshot (2026-07-22):
1. `status` -> not running
2. `start` -> running with PID and HTTP API visible
3. `stop` -> clean stop reported
4. `status` -> not running

Historical note:
1. `apollod` / `apollo-mux` graceful shutdown semantics remain preserved in patch/archive references for lineage tracking.
2. Those references are not the canonical active runtime path in this workspace.

---

## Files Involved

| File | Purpose |
|------|---------|
| `cyn` | Main entry point |
| `scripts/cyn_main.py` | Core command routing |
| `scripts/cyn-daemon.py` | Daemon runtime |
| `scripts/install.py` | Workspace setup automation |
| `scripts/logging_utils.py` | Logging infrastructure |
| `docs/patchset/patchset_overview.md` | Patch-set history snapshot |
| `docs/install.md` | Toolchain and build-system configuration |

---

## Examples

### Run full setup with JSON output
```bash
cyn --json setup --parallel
```

### Build Apollo firmware with logging
```bash
cyn --log apollo-build.log apollo build
```

### Check system readiness
```bash
cyn prereqs
cyn ai-brief
```

### Run CI locally
```bash
cyn ci install
cyn ci apollo
cyn ci cynthion
```

---

## See Also

- [docs/patchset/patchset_overview.md](patchset/patchset_overview.md) - Patch-set history snapshot
- [scripts/install.py](../scripts/install.py) - Build automation
- [docs/install.md](install.md) - Toolchain and build-system setup


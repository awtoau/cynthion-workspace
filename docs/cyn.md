## Cyn Unified CLI Architecture

**Cyn** is the unified entry point for all Cynthion operations.

### Overview
- **Command:** `cyn` (executable at repo root)
- **Implementation:** `scripts/cyn_main.py` (core logic), `scripts/cyn` (entry point)
- **Daemon:** `scripts/cyn-daemon.py` (background service for GUI, HTTP API)

### Key Design

**Smart Routing:**
- Detects if daemon is running
- If daemon: connects via HTTP (fast, cached environment)
- If no daemon: runs commands directly (inline)

**Commands:**
- `cyn <component> <subcommand>` — fpga, apollo, moondancer, gateware
- `cyn <workspace>` — setup, versions, prereqs, status
- `cyn ci <cmd>` — GitHub Actions CI/CD (locally via act)
- `cyn daemon start/stop/status/restart` — daemon management
- `cyn ai-brief/ai-schema/ai-tasks` — AI-discoverable outputs

### Daemon HTTP API (Port 8765)
- `/health` — health check
- `/status` — daemon status + uptime
- `/project/status` — project state
- `/commands` — available commands list

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

### Benefits
- ✓ Single entry point for all operations
- ✓ AI-agent friendly with JSON discovery
- ✓ Transparent daemon switching
- ✓ Ready for future GUI integration via HTTP

---


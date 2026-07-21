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

### Benefits
- ✓ Single entry point for all operations
- ✓ AI-agent friendly with JSON discovery
- ✓ Transparent daemon switching
- ✓ Ready for future GUI integration via HTTP

---


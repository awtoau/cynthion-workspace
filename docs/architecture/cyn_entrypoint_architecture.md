# Cyn Entry Point Architecture

**`cyn` = Single unified entry point for AI agents, developers, and automation**

---

## What `cyn` Provides

### Information & Discovery
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
cyn setup                 # Sequential setup (30+ minutes)
cyn setup --parallel      # Parallel setup (18 minutes, 55% faster)
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
cyn --json ai-brief       # Get project summary as JSON
```

**Returns:**
```json
{
  "project": "Cynthion Workspace",
  "components": {
    "apollo": { "type": "ARM firmware", "status": "building" },
    "moondancer": { "type": "RISC-V firmware", "status": "building" },
    "analyzer_gateware": { "type": "FPGA", "status": "building" },
    "facedancer_gateware": { "type": "FPGA", "status": "known_issue" }
  },
  "phase": { "current": "Phase 1", "status": "3/4 successful" }
}
```

### Step 2: Get Available Commands
```bash
cyn --json ai-schema      # Machine-readable command schema
```

**Returns:** Complete JSON schema of all commands, arguments, and outputs

### Step 3: See What Tasks Can Be Run
```bash
cyn --json ai-tasks       # List of actionable tasks
```

**Returns:**
```json
{
  "available_tasks": [
    {
      "id": "fpga_test",
      "command": "cyn fpga sim_test",
      "description": "Run FPGA simulator test suite",
      "time_estimate": "5-10 minutes"
    },
    ...
  ]
}
```

### Step 4: Execute Tasks
```bash
cyn --json apollo build   # Build and get JSON result
cyn --log build.log setup # Setup with logging
cyn --verbose ci apollo   # Run CI with verbose output
```

---

## Future: Daemon + Client Architecture

### Planned Enhancement (Not Yet Implemented)

```bash
# Daemon mode (runs as background service)
cyn daemon start          # Start cyn daemon
cyn daemon stop           # Stop cyn daemon
cyn daemon status         # Check daemon status

# Client mode (connects to daemon for faster execution)
cyn --daemon-mode list    # Connect to daemon instead of running inline
cyn --daemon-mode fpga sim_test

# Signal handling
# Daemon handles SIGINT, SIGTERM gracefully
# Clients can be killed by Ctrl+C
```

### Benefits
- **GUI Layer**: GUI can connect to daemon via socket/HTTP
- **Automation**: Tools can communicate with cyn daemon
- **Performance**: Daemon keeps environment cached (faster sequential commands)
- **Signal Handling**: Proper cleanup on shutdown

### Implementation Strategy

**Phase 1 (Current):** Command-line only (this implementation)
**Phase 2 (Future):** Add daemon mode with:
  - `--daemon-mode` flag for client connections
  - Socket/HTTP communication layer
  - Signal handlers (SIGINT, SIGTERM)
  - Persistent daemon process

**Phase 3 (Future):** GUI layer on top of daemon

---

## For AI Agents

### Quick Start
```bash
# Get full project state
cyn --json ai-brief

# Get list of available tasks
cyn --json ai-tasks

# Pick a task and run it
cyn <task-command>
```

### Discovery Pattern
1. Run `cyn ai-brief` to understand the project
2. Run `cyn ai-schema` to see all available commands
3. Run `cyn ai-tasks` to find actionable work
4. Execute using `cyn <component> <command>`

### Output Formats
- **Human**: Colored console output, easy to read
- **JSON**: Structured output (use `--json` flag), easy to parse
- **Logging**: File logging available (use `--log file` flag)

### Error Handling
- All errors go through standard logging
- JSON output includes error messages and codes
- Exit codes: 0 = success, 1 = failure

---

## Files Involved

| File | Purpose |
|------|---------|
| `cyn` | Main entry point (this file) |
| `scripts/install.py` | Workspace setup automation |
| `scripts/logging_utils.py` | Logging infrastructure |
| `patchset/patchset_overview.md` | Patch-set history snapshot |
| `build_system.md` | Toolchain configuration |

---

## Examples

### Run full test suite with JSON output
```bash
cyn --json setup --parallel
```

### Build just Apollo firmware with logging
```bash
cyn --log apollo-build.log apollo build
```

### Run Luna FPGA simulator
```bash
cyn fpga sim_test
```

### Check if system is ready
```bash
cyn prereqs
cyn ai-brief
```

### Run CI locally
```bash
cyn ci install      # First time setup
cyn ci apollo       # See available jobs
cyn ci cynthion     # List Cynthion jobs
```

---

## See Also

- [patchset/patchset_overview.md](../patchset/patchset_overview.md) — Patch-set history snapshot
- [scripts/install.py](../../scripts/install.py) — Build automation (called by cyn)
- [cyn_entrypoint_architecture.md](cyn_entrypoint_architecture.md) — This file

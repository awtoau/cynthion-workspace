# FPGA job queue

This directory is the foreground queue for the one shared FPGA board.

- Develop and check the design in simulation before adding a job.
- Put each job in `queued/<job-name>/job.json`; the runner moves it to `completed/`.
- Run `python3 scripts/fpga_job_runner.py`; it exits after the current queue drains.
- Read `result.json` and `result.log` inside the completed job.
- Use `--hardware-free-only` only for jobs whose manifest sets `hardware` to `false`;
  this takes the runner lock without scanning or opening the board.

| Field | Value |
|---|---|
| `schema` | `1` |
| `hardware` | Whether the driver can access the board |
| `artifact.bitstream` | Existing bitstream, relative to the job or absolute within this workspace |
| `artifact.build.command` | Build command as an argument array |
| `artifact.build.flags` | Explicit build flags appended to the command |
| `artifact.build.sources` | Every source needed by the build |
| `artifact.build.output` | Bitstream or simulation artifact produced by the build |
| `driver.command` | Driver argument array; Python scripts belong in `scripts/` |
| `expected` | Positive result, printed before the driver runs |
| `negative_control` | Deliberately wrong result; its driver invocation must fail |
| `restore.bitstream` | Known bitstream restored after every hardware job; `null` for hardware-free jobs |

The driver receives the job contract through environment variables.

| Variable | Meaning |
|---|---|
| `FPGA_JOB_DIR` | Absolute path to the job directory |
| `FPGA_JOB_BITSTREAM` | Absolute path to the existing or built artifact |
| `FPGA_JOB_CONTROL` | `positive` or `negative` |
| `FPGA_JOB_EXPECTED` | Compact JSON for the current expectation |

The driver configures the job bitstream, performs the measurement, compares the result
with `FPGA_JOB_EXPECTED`, and exits zero only on a match. The runner invokes it once with
the positive expectation and once with the negative control. The runner itself restores
`restore.bitstream` in its cleanup path.

The board claim is deliberately small.

- An advisory lock in `tmp/fpga-job-runner.lock` serialises runners.
- A `/proc` scan refuses a board already open through its USB or tty device node.
- Direct board tools outside the queue remain detectable while they hold a device open.

| Path | Contents |
|---|---|
| `queued/` | Jobs waiting in lexical order |
| `completed/` | Jobs with `result.json` and `result.log` attached |
| `tmp/logs/fpga_job_runner.log` | Combined runner history |

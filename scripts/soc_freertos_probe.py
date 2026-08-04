#!/usr/bin/env python3
"""Build a FreeRTOS skeleton for this SoC and report what the kernel costs.

The C half of `scripts/soc_model_probe.py`. The skeleton in
`scripts/freertos-model/` does the same visible work as every Rust skeleton in
`firmware/cynthion-soc/src/bin/model_*.rs` -- a PLIC front end, two sources, one
shared counter, an idle loop -- so its `.text` is comparable with theirs, and
`docs/soc-concurrency-models.md` puts them in one table.

It also reports what the Rust skeletons have no equivalent of: per-task RAM. A
FreeRTOS task is a TCB and a STACK, and the stack is the number that decides
whether this model fits in the ~46 KiB of block RAM that is free.

The kernel is fetched to ./tmp/ on first run. `-Os -flto --gc-sections`, and a
FreeRTOSConfig.h with everything off that this firmware would not use, because
the question is what the model costs at BEST.

Writes ./tmp/logs/soc_freertos_probe.log as well as stdout.
"""
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
MODEL = WORKSPACE / "scripts/freertos-model"
BUILD = WORKSPACE / "tmp/freertos-build"
LOG = WORKSPACE / "tmp/logs/soc_freertos_probe.log"

VERSION = "V11.2.0"
URL = f"https://github.com/FreeRTOS/FreeRTOS-Kernel/archive/refs/tags/{VERSION}.tar.gz"
KERNEL = WORKSPACE / f"tmp/FreeRTOS-Kernel-{VERSION.lstrip('V')}"

# A riscv64 gcc compiles rv32 objects perfectly well with -march/-mabi, and
# nothing here links a libc, so no rv32 multilib is needed.
CC = "riscv64-linux-gnu-gcc"

# `-Os -flto` is the closest gcc gets to the Rust skeletons' `opt-level = "z"`
# with LTO. `--gc-sections` matters more than either: without it the whole of
# queue.c links whether or not the skeleton reaches it.
CFLAGS = [
    # `_zicsr` explicitly: gcc 12 split the CSR instructions out of the base
    # ISA, and the port's context save is nothing but `csrr`/`csrw`. The core
    # itself has them -- see the CSR table in `docs/hardware.md`.
    "-march=rv32imac_zicsr_zifencei",
    "-mabi=ilp32",
    "-mcmodel=medany",
    "-Os",
    "-flto",
    "-ffunction-sections",
    "-fdata-sections",
    # Freestanding, so `stdint.h` comes from gcc rather than from a libc this
    # cross compiler has no rv32 copy of. Nothing here calls a library function.
    "-ffreestanding",
    "-fno-builtin",
    "-nostdlib",
    "-nostartfiles",
    "-Wall",
]

# The kernel this firmware would actually link. croutine, event_groups,
# stream_buffer and timers are all left out -- see FreeRTOSConfig.h.
SOURCES = [
    "tasks.c",
    "list.c",
    "queue.c",
    "portable/GCC/RISC-V/port.c",
    "portable/GCC/RISC-V/portASM.S",
]

# Which sections the kernel's own code lands in, so the report can separate the
# kernel from the skeleton around it.
KERNEL_PREFIXES = ("tasks.c", "list.c", "queue.c", "port.c", "portASM.S")


def fetch() -> None:
    if KERNEL.is_dir():
        return
    KERNEL.parent.mkdir(parents=True, exist_ok=True)
    archive = KERNEL.parent / f"freertos-{VERSION}.tar.gz"
    if not archive.is_file():
        urllib.request.urlretrieve(URL, archive)
    with tarfile.open(archive) as tar:
        tar.extractall(KERNEL.parent, filter="data")


def sections(elf: Path) -> dict[str, int]:
    tool = shutil.which("llvm-size") or shutil.which("size")
    out = subprocess.run(
        [tool, "-A", elf.as_posix()], capture_output=True, text=True, check=True
    ).stdout
    sizes = {}
    for line in out.splitlines():
        match = re.match(r"^(\.\S+)\s+(\d+)\s+\d+", line)
        if match:
            sizes[match.group(1)] = int(match.group(2))
    return sizes


def probe_sizes(includes: list[str]) -> dict[str, int]:
    """Compile `sizes.c` alone and read the symbol sizes back with `nm -S`.

    Alone, and without LTO or `--gc-sections`, because the arrays exist only to
    be measured and any link that could remove them would."""
    obj = BUILD / "sizes.o"
    argv = [
        CC, "-march=rv32imac_zicsr_zifencei", "-mabi=ilp32", "-ffreestanding",
        "-Os", *includes, "-c", (MODEL / "sizes.c").as_posix(), "-o", obj.as_posix(),
    ]
    proc = subprocess.run(argv, capture_output=True, text=True)
    if proc.returncode != 0:
        return {}
    nm = shutil.which("llvm-nm") or shutil.which("nm")
    out = subprocess.run(
        [nm, "-S", "--defined-only", obj.as_posix()],
        capture_output=True, text=True, check=True,
    ).stdout
    sizes = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 4 and parts[3].startswith("probe_"):
            sizes[parts[3]] = int(parts[1], 16)
    return sizes


def main() -> int:
    out: list[str] = []

    def emit(line: str = "") -> None:
        print(line)
        out.append(line)

    if shutil.which(CC) is None:
        emit(f"RESULT: SKIP - {CC} is not on PATH")
        return 0

    fetch()
    BUILD.mkdir(parents=True, exist_ok=True)

    includes = [
        f"-I{MODEL}",
        f"-I{MODEL}/shim",
        f"-I{KERNEL}/include",
        f"-I{KERNEL}/portable/GCC/RISC-V",
        f"-I{KERNEL}/portable/GCC/RISC-V/chip_specific_extensions/RISCV_MTIME_CLINT_no_extensions",
    ]

    def link(label: str, sources: list[str], elf: Path) -> dict[str, int] | None:
        argv = (
            [CC, *CFLAGS, *includes]
            + sources
            + [(MODEL / "start.S").as_posix()]
            + [f"-T{MODEL / 'link.ld'}", "-Wl,--gc-sections",
               "-Wl,-Map," + elf.with_suffix(".map").as_posix(),
               "-o", elf.as_posix()]
        )
        emit(f"$ {CC} -Os -flto ... -o tmp/freertos-build/{elf.name}")
        proc = subprocess.run(argv, capture_output=True, text=True)
        if proc.returncode != 0:
            for line in (proc.stderr or "").splitlines():
                emit(f"  {line}")
            emit(f"  FAIL: {label} did not link")
            return None
        emit(f"  ok: {label}")
        return sections(elf)

    # The floor first: the same visible work in the same language with the same
    # compiler and no kernel, so the kernel's cost is a difference and not a
    # total. `firmware/cynthion-soc/src/bin/model_bare.rs` is its Rust twin.
    bare = link("bare C: no kernel", [(MODEL / "bare.c").as_posix()],
                BUILD / "bare.elf")
    elf = BUILD / "freertos.elf"
    sizes = link(
        f"FreeRTOS {VERSION}, RISC-V port, static allocation only",
        [(KERNEL / source).as_posix() for source in SOURCES]
        + [(MODEL / "main.c").as_posix()],
        elf,
    )
    if sizes is None or bare is None:
        emit("RESULT: FAIL")
        LOG.parent.mkdir(parents=True, exist_ok=True)
        LOG.write_text("\n".join(out) + "\n")
        return 1

    emit()
    emit(f"{'section':<12} {'bare C':>8} {'FreeRTOS':>9} {'kernel':>8}")
    emit("-" * 40)
    for name in (".text", ".rodata", ".data", ".bss", ".isr_stack"):
        emit(
            f"{name:<12} {bare.get(name, 0):>8} {sizes.get(name, 0):>9} "
            f"{sizes.get(name, 0) - bare.get(name, 0):>8}"
        )
    emit()
    emit("`kernel` is the difference: FreeRTOS and nothing else.")

    probe = probe_sizes(includes)
    tcb = probe.get("probe_static_task")
    queue = probe.get("probe_static_queue")
    stack = probe.get("probe_minimal_stack")

    emit()
    emit("per task, in block RAM:")
    emit(f"  TCB                          {tcb} bytes")
    emit(f"  configMINIMAL_STACK_SIZE     {stack} bytes")
    emit(f"  StaticSemaphore_t            {queue} bytes")
    if tcb is not None and stack is not None:
        emit(f"  one task, at the minimum     {tcb + stack} bytes")
        emit()
        emit("N tasks against the ~46 KiB of block RAM that is free:")
        emit(f"  {'stack':>7}  " + "  ".join(f"{n:>2} tasks" for n in (3, 5, 8)))
        for stack_bytes in (512, 1024, 2048, 4096):
            row = "  ".join(
                f"{(n * (tcb + stack_bytes)) / 1024:>6.1f}K" for n in (3, 5, 8)
            )
            emit(f"  {stack_bytes:>7}  {row}")

    emit()
    emit("RESULT: PASS - the FreeRTOS skeleton links")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text("\n".join(out) + "\n")
    print(f"\n(log written to {LOG})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

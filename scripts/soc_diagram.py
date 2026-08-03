#!/usr/bin/env python3
#
# A mermaid architecture diagram of the RISC-V SoC, read out of the SoC itself.
# SPDX-License-Identifier: BSD-3-Clause

"""
Draws the SoC by elaborating it and walking what comes out, not by drawing a picture.

## Why it is generated

A hand-drawn block diagram is wrong the day after it is drawn, and nothing tells you.
This SoC has already grown a flash controller, a pin probe, a logic analyser and a third
CPU master since anyone last described it in prose, and the one thing every one of those
additions had in common is that no diagram changed. A diagram that is *derived* cannot
drift: add a peripheral to `vexii_hello_soc.py`, rerun this, and it is in the picture,
with its address, without touching this file.

The same argument as `scripts/soc_generate_pac.py`, applied to the topology rather than
to the register map.

## Where every fact comes from

    Amaranth elaboration          modules, hierarchy, clock domains
    `Design.fragments`            which signals each module touches, and who drives them
    wishbone `Decoder` memory map peripheral list and address ranges
    wishbone `Arbiter`            which buses are masters (the three-master split)
    `platform.request` calls      which board resources the design actually uses
    `Instance` types              hard macros: EHXPLLL, JTAGG, USRMCLK
    an `ast` pass over the source peripherals sitting behind a `if False:` guard

Edges are *net-level*: two modules are joined either by using the same signal, or by an
assignment in the SoC's own `elaborate` that joins one module's signal to another's --
which is every `wiring.connect`, and about half the wiring. Each edge is labelled with
the ports it lands on, taken from the modules' own signatures, so the CPU reaches the
arbiter over three separate edges, `ibus`, `dbus` and `iobus`. That distinction is
load-bearing and has cost real time: the uncached `iobus` is how every peripheral access
travels, and a design missing it runs, passes timing and enumerates while reaching
nothing.

The number on an edge is how many signal pairs join those two modules -- 11 for a
wishbone bus, 3 for a stream. It is a weight, not a wire count: one assignment of a
concatenation, such as the six LED bits, counts every source against every bit.

Boxes are distance from the bus fabric, measured on those same edges, so a new peripheral
is filed next to the ones it structurally resembles rather than by a list of names kept
here.

The yosys netlist (`tmp/vexii_hello/build/top.json`) was the other candidate source and
is not used: `synth_ecp5` flattens, so that file is one `top` module of 14,700 LUT-level
cells with no hierarchy left. The unflattened `top.il` does keep it -- and its module
list matches this script's fragment tree exactly -- but it is an 8.6 MB build artifact
of the *last* build, while elaboration needs no build at all and cannot be stale.

## What is NOT derived

One table, `OFF_CHIP` / `MACRO_PADS` below: what a pin group physically reaches on the
PCB, and who owns each JTAG user instruction. The gateware knows it drives `qspi_flash`;
it cannot know a W25Q32 is soldered to the other end. Resources with no entry are still
drawn, under their own name, so the table is an annotation and never a gate.

## Cost

No FPGA toolchain, no board, no synthesis: elaboration only, about two seconds.

    ./scripts/soc_diagram.py
    ./scripts/soc_diagram.py --stdout      # mermaid to the terminal as well
"""

import argparse
import ast
import itertools
import re
import shutil
import subprocess
import sys
import warnings
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "soc_diagram.log"
MMD = ROOT / "tmp" / "soc_diagram.mmd"
MD = ROOT / "tmp" / "soc_diagram.md"
HTML = ROOT / "tmp" / "soc_diagram.html"

SOC_SOURCE = ROOT / "ecp5-test" / "riscv" / "vexii_hello_soc.py"

sys.path.insert(0, str(ROOT / "ecp5-test"))
sys.path.insert(0, str(ROOT / "ecp5-test" / "riscv"))


# --------------------------------------------------------------------------------------
# THE CURATED TABLE -- the only hand-written knowledge in this script.
#
# Everything else is read out of the elaborated design. These two dicts hold facts that
# are physically true of the board and absent from the gateware: a design can see that it
# drives `qspi_flash`, but not that a W25Q32 is on the other end of those pads, and it
# certainly cannot see that Apollo has already claimed JTAG user instruction ER1.
#
# Keys are matched against things the design was observed to use. A resource or macro
# with no entry here is still drawn, under its own name -- so forgetting to extend this
# table loses a caption, never a node.
# --------------------------------------------------------------------------------------

OFF_CHIP = {
    # platform resource name -> what it reaches on the PCB
    "qspi_flash":     "SPI configuration flash<br/>W25Q32 (4 MiB), holds the bitstream",
    "spi_flash":      "SPI configuration flash (raw pins)",
    "ram":            "HyperRAM<br/>8 MiB self-refresh DRAM",
    "aux_phy":        "USB3343 ULPI PHY<br/>AUX USB-C port (FPGA owns it)",
    "control_phy":    "USB3343 ULPI PHY<br/>CONTROL USB-C port (shared with Apollo)",
    "target_phy":     "USB3343 ULPI PHY<br/>TARGET USB-C port (device under test)",
    "led":            "6 status LEDs<br/>red orange yellow green blue violet",
    "int":            "Apollo SAMD11<br/>FPGA_ADV one-wire sideband",
    "clk_60MHz":      "60 MHz oscillator",
    "button_user":    "USER button",
    "uart":           "Apollo UART<br/>pins shared with JTAG TDI/TMS on r1.4",
    "user_pmod":      "PMOD header",
    "user_mezzanine": "mezzanine header",
    "power_monitor":  "PAC1954 power monitor",
    "target_type_c":  "TARGET USB-C CC/SBU",
    "aux_type_c":     "AUX USB-C CC/SBU",
}

MACRO_PADS = {
    # hard macro instantiated in the design -> the off-chip thing it reaches, if any.
    # `None` means the macro is on-die and gets no external node of its own.
    "JTAGG":   "ECP5 configuration TAP<br/>Apollo debug SPI on ER1 (0x32)<br/>"
               "RISC-V debug module on ER2 (0x38)",
    "USRMCLK": "MCLK configuration clock pin<br/>the only route to the flash's SCK",
    "EHXPLLL": None,
}


# --------------------------------------------------------------------------------------
# Elaboration
# --------------------------------------------------------------------------------------

def elaborate(emit):
    """Elaborate the SoC and hand back the prepared design plus what the board saw.

    Returns `(design, platform, requests)`, where `requests` maps `(resource, number)` to
    the interface object handed back by `platform.request`. That mapping is how a pin
    group is later attributed to the module that actually drives it: the interface's
    signals appear in exactly one module's subtree.
    """
    # Elaboration warns about the pin buffers `platform.request` builds, because nothing
    # instantiates them outside a real build. Not a fault, and 16 lines of it drowns the
    # log.
    warnings.simplefilter("ignore")

    import vexii_cpu

    # The CPU's Verilog is regenerated by an sbt run inside `VexiiRiscv.elaborate`.
    # Reuse the last one instead when it is there: the core is an `Instance` black box
    # whose ports are declared in `vexii_cpu.py`, not read back from the Verilog, so its
    # contents cannot change one node or one edge of this graph -- only the tens of
    # seconds and the Java toolchain it takes to produce them. With no cached copy this
    # falls through to the real generator, so a clean tree still works.
    cached = ROOT / "repos" / "vexiiriscv" / "VexiiRiscv.v"
    if cached.exists():
        vexii_cpu.generate = lambda *args, **kwargs: cached
        emit(f"CPU verilog: reusing {cached.relative_to(ROOT)} (no sbt run)")
    else:
        emit("CPU verilog: no cached copy, running the generator")

    import vexii_hello_soc
    from amaranth.hdl._ir import Fragment
    from cynthion.gateware.platform.cynthion_r1_4 import CynthionPlatformRev1D4

    platform = CynthionPlatformRev1D4()

    # Wrapped on the instance rather than the class, so nothing leaks into another
    # platform built later in the same process.
    requests = {}
    inner = platform.request

    def watch(name, number=0, **kwargs):
        obj = inner(name, number, **kwargs)
        requests[(name, number)] = obj
        return obj

    platform.request = watch

    # Contents are irrelevant -- only the shape of the design is read -- but the SoC
    # refuses an image larger than its block RAM, so keep it small.
    soc = vexii_hello_soc.HelloSoC(firmware=[0] * 16)

    # `prepare()` rather than a bare `Fragment.get`: it is what propagates clock domains
    # down the tree and computes, for every fragment, which signals are used inside it.
    # Both are the raw material for everything below.
    design = Fragment.get(soc, platform).prepare()
    platform.request = inner
    return design, platform, requests, vexii_hello_soc


# --------------------------------------------------------------------------------------
# Reading structure out of the elaborated design
# --------------------------------------------------------------------------------------

def subtree(fragment):
    """Every fragment at or below this one."""
    out = [fragment]
    for sub, _name, _loc in fragment.subfragments:
        out += subtree(sub)
    return out


def driven_signals(fragment):
    """Signals this fragment assigns, including an Instance's outputs.

    Direction is not recorded anywhere in the fragment tree -- a signal is simply used by
    both ends -- so it is recovered from who writes it. `Instance` carries no statements
    at all, which is why its output ports are collected separately; without that the CPU
    would appear to drive nothing.
    """
    from amaranth.hdl._ir import Instance

    out = []
    for _domain, statements in fragment.statements.items():
        for statement in statements:
            out += list(statement._lhs_signals())
    if isinstance(fragment, Instance):
        for _name, (value, kind) in fragment.ports.items():
            if kind == "o":
                try:
                    out += list(value._lhs_signals())
                except (TypeError, AttributeError):
                    pass  # an IOValue port, which is a pad rather than a signal
    return out


def port_names(elaboratable):
    """Signal -> the name of the port it belongs to, from the module's own signature.

    `wiring.connect(m, a.source, b.sink)` leaves nothing behind but assignments between
    signals called `payload`, `valid` and `ready`, three times over, so raw signal names
    cannot say which port of which module a wire came from. The signature can: it maps
    every signal back to the member the author declared. This is what turns an edge label
    from "payload" into "source -> sink".
    """
    out = {}
    signature = getattr(elaboratable, "signature", None)
    if signature is not None and hasattr(signature, "flatten"):
        try:
            for path, _flow, value in signature.flatten(elaboratable):
                for signal in value._rhs_signals():
                    out[id(signal)] = path[0]
        except (TypeError, AttributeError, ValueError):
            pass  # not a wiring.Component, or a signature that will not flatten

    # Not everything is a Component. `USBSerialDevice` hangs its streams off plain
    # attributes, so without this its ports read as "payload" and "valid" -- true of
    # every stream in the design and therefore useless as a label. Public attributes
    # only, and the signature wins where both have an answer.
    for attr, value in list(vars(elaboratable).items()):
        if attr.startswith("_"):
            continue
        for signal in interface_signals(value):
            out.setdefault(id(signal), attr)
    return out


def interface_signals(obj, depth=0, seen=None):
    """Every Signal reachable from an interface object handed back by `platform.request`.

    Walks the wiring signature where there is one and `__dict__` otherwise, because a
    resource comes back as one of several shapes -- a flipped `Pin` interface for a plain
    output, a `PortGroup` for something with subsignals -- and only the signatures know
    the member names. `dir()` does not work here: a `FlippedInterface` resolves members
    through `__getattr__` and lists none of them.
    """
    from amaranth.hdl._ast import Signal

    seen = set() if seen is None else seen
    if id(obj) in seen or depth > 6:
        return []
    seen.add(id(obj))
    if isinstance(obj, Signal):
        return [obj]

    out = []
    signature = getattr(obj, "signature", None)
    if signature is not None and hasattr(signature, "members"):
        for name in signature.members:
            try:
                out += interface_signals(getattr(obj, name), depth + 1, seen)
            except (AttributeError, TypeError):
                pass
    if isinstance(obj, dict):
        # A legacy `Record` -- which is what LUNA's StreamInterface still is -- keeps its
        # signals in a plain `fields` dict, so stopping at objects would miss every USB
        # stream in the design.
        for value in list(obj.values()):
            out += interface_signals(value, depth + 1, seen)
    elif isinstance(obj, (list, tuple)):
        for value in obj:
            out += interface_signals(value, depth + 1, seen)
    else:
        for value in list(getattr(obj, "__dict__", {}).values()):
            out += interface_signals(value, depth + 1, seen)
    return out


def resource_directions(platform, name, number):
    """The pin directions a board resource declares, so an edge can point somewhere.

    A resource is a tree of `Subsignal`s bottoming out in `Pins`/`DiffPairs`, each with
    its own direction, so this is the union over the whole group: `{"o"}` is an output,
    `{"i"}` an input, anything else bidirectional.
    """
    from amaranth.build.dsl import DiffPairs, Pins

    dirs = set()

    def walk(node):
        for io in getattr(node, "ios", []):
            if isinstance(io, (Pins, DiffPairs)):
                dirs.add(io.dir)
            else:
                walk(io)

    walk(platform.lookup(name, number))
    return dirs


def guarded_out(source):
    """Peripherals present in the source but compiled out by a constant-false guard.

    Elaboration cannot see these -- that is the point of the guard -- so they are the one
    structural fact that has to come from the text. An `if False:` block around a
    peripheral is a deliberate, reversible state ("ISOLATION-TEST: does removing BootRAM
    restore the console?"), and a diagram that silently omits it says the design never
    had HyperRAM, which is a different and wrong claim.

    Matches any `if <constant that is falsy>:`, so `if False:` and `if 0:` both count.
    """
    tree = ast.parse(source.read_text())
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        try:
            dead = not bool(ast.literal_eval(node.test))
        except (ValueError, SyntaxError):
            continue  # a real runtime test, not a compile-time switch
        if not dead:
            continue

        classes = {}
        for sub in ast.walk(node):
            # `m.submodules.<name> = <name> = SomeClass()` -- the class is the label.
            if isinstance(sub, ast.Assign) and isinstance(sub.value, ast.Call):
                callee = sub.value.func
                cls = callee.id if isinstance(callee, ast.Name) else getattr(callee, "attr", "?")
                for target in sub.targets:
                    if isinstance(target, ast.Attribute):
                        classes[target.attr] = cls
        for sub in ast.walk(node):
            if not (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)):
                continue
            if sub.func.attr != "add":
                continue
            kwargs = {kw.arg: kw.value for kw in sub.keywords}
            if "addr" not in kwargs or "name" not in kwargs:
                continue
            try:
                name = ast.literal_eval(kwargs["name"])
            except ValueError:
                continue
            addr = kwargs["addr"]
            found.append({
                "name": name,
                # The address is nearly always a module constant; keep the expression as
                # a fallback so an inline literal or an expression still says something.
                "addr_name": addr.id if isinstance(addr, ast.Name) else ast.unparse(addr),
                "classes": classes,
            })
    return found


def analyse(emit):
    """Everything the diagram is drawn from, as plain data."""
    from amaranth_soc import wishbone

    design, platform, requests, soc_module = elaborate(emit)
    top = design.fragment
    top_info = design.fragments[top]

    # ---- modules -------------------------------------------------------------------
    #
    # One node per top-level submodule. That granularity is not a guess: it is the level
    # the SoC's own `elaborate` builds, so it tracks the author's own decomposition, and
    # going one level deeper turns 20 nodes into 165.
    from amaranth.hdl._ir import Instance

    nodes = {}
    used = {}     # module -> ids of signals touched anywhere in its subtree
    drives = {}   # module -> ids of signals assigned anywhere in its subtree
    signals = {}  # id -> Signal, since Signals are unhashable

    for fragment, name, _loc in top.subfragments:
        origins = [type(o).__name__ for o in (fragment.origins or ())]
        macros = sorted({f.type for f in subtree(fragment) if isinstance(f, Instance)})
        used[name] = set()
        drives[name] = set()
        for part in subtree(fragment):
            for signal in design.fragments[part].used_signals:
                used[name].add(id(signal))
                signals[id(signal)] = signal
            for signal in driven_signals(part):
                drives[name].add(id(signal))
                signals[id(signal)] = signal
        nodes[name] = {
            "name": name,
            # The first origin is the Elaboratable the author wrote; the rest are the
            # Modules it elaborated through.
            "cls": origins[0] if origins else "Fragment",
            "macros": macros,
            "domains": set(),
            "window": None,
            "master": [],
            "resources": [],
            "role": None,
            "disabled": False,
        }

    # The top level itself is a node only if it does something -- here it drives the
    # status LEDs and wires the console to the USB endpoint. Its signals are the ones in
    # its own statements, NOT `used_signals`, which also counts every signal merely
    # routed through it and would join it to everything.
    top_own = set()
    for signal in driven_signals(top):
        top_own.add(id(signal))
        signals[id(signal)] = signal
    for _domain, statements in top.statements.items():
        for statement in statements:
            for signal in list(statement._rhs_signals()) + list(statement._lhs_signals()):
                top_own.add(id(signal))
                signals[id(signal)] = signal

    top_name = type((top.origins or [None])[0]).__name__ if top.origins else "top"

    # ---- clock domains ---------------------------------------------------------------
    #
    # A module is in a domain when it uses that domain's clock. Derived rather than read
    # off the `m.d.<domain>` calls, so a module that only instantiates something clocked
    # still lands in the right place -- and it is what makes the console show up in two
    # domains at once, which is the crossing the whole console FIFO exists for.
    clock_of = {}
    for domain_name, domain in top.domains.items():
        clock_of[id(domain.clk)] = domain_name
        if domain.rst is not None:
            clock_of[id(domain.rst)] = domain_name
    for name, node in nodes.items():
        node["domains"] = {clock_of[sid] for sid in used[name] if sid in clock_of}
    clock_sources = {name for name, node in nodes.items()
                     if any(sid in clock_of for sid in drives[name])}

    # ---- the address map -------------------------------------------------------------
    #
    # Found by looking for a wishbone Decoder among the elaboratables rather than by
    # reaching for `soc.decoder`, which does not exist: the decoder is a local inside
    # `elaborate`. Each window is then attributed to the module that owns the bus it was
    # built from, by object identity on the memory map -- so the address lands on the
    # right node however the peripheral happens to be named.
    windows = []
    masters = []
    for elab, fragment in design.elaboratables.items():
        if isinstance(elab, wishbone.Decoder):
            nodes[design.fragments[fragment].name[-1]]["role"] = "fabric"
            for window, wname, (start, end, _ratio) in elab.bus.memory_map.windows():
                owner = None
                for other, other_frag in design.elaboratables.items():
                    for attr, value in list(vars(other).items()):
                        if getattr(value, "memory_map", None) is window:
                            owner = design.fragments[other_frag].name[-1]
                registers = sum(1 for _ in window.all_resources())
                # A memory map name is a path object, not a string: `Name('spi0')`.
                windows.append({"name": ".".join(str(part) for part in wname),
                                "start": start, "end": end,
                                "owner": owner, "registers": registers})
                if owner in nodes:
                    nodes[owner]["window"] = windows[-1]
        if isinstance(elab, wishbone.Arbiter):
            nodes[design.fragments[fragment].name[-1]]["role"] = "fabric"
            # `_intrs` is the list of buses handed to `Arbiter.add`. Each is an attribute
            # of the master that owns it, so identity gives both the master and the name
            # it knows the bus by -- which is how ibus/dbus/iobus stay three things.
            for bus in elab._intrs:
                for other, other_frag in design.elaboratables.items():
                    for attr, value in list(vars(other).items()):
                        if value is bus:
                            owner = design.fragments[other_frag].name[-1]
                            if owner in nodes:
                                nodes[owner]["master"].append(attr)
                                masters.append((owner, attr))

    # ---- board resources -------------------------------------------------------------
    #
    # Attributed by signal, not by who called `request`: the SoC asks for `aux_phy` in
    # its own `elaborate` and hands it to the USB device, so the caller is the SoC while
    # the module actually driving those pads is `serial`.
    externals = {}
    for (rname, number), obj in requests.items():
        sigs = {id(s) for s in interface_signals(obj)}
        owners = [name for name in nodes if used[name] & sigs]
        if not owners and top_own & sigs:
            owners = ["<top>"]
        key = rname
        entry = externals.setdefault(key, {
            "resource": rname,
            "count": 0,
            "owners": set(),
            "dirs": resource_directions(platform, rname, number),
            "label": OFF_CHIP.get(rname, rname),
        })
        entry["count"] += 1
        entry["owners"].update(owners)

    # ---- hard macros that leave the die ----------------------------------------------
    #
    # An `Instance` of a vendor primitive is the only trace some off-chip connections
    # leave: JTAGG is the whole JTAG path, and USRMCLK is the flash clock, neither of
    # which appears as a requestable resource at all.
    macro_pads = []
    for name, node in nodes.items():
        for macro in node["macros"]:
            if MACRO_PADS.get(macro):
                macro_pads.append({"macro": macro, "owner": name,
                                   "label": MACRO_PADS[macro]})

    # ---- edges -----------------------------------------------------------------------
    #
    # Two kinds of connection exist in this design and both have to be found, because
    # each accounts for about half the wiring:
    #
    #   direct  the two modules use the SAME signal. `Decoder.add(ram.bus)` works this
    #           way -- the peripheral's bus signals are the decoder's too.
    #   glue    the two modules use DIFFERENT signals, joined by an assignment in the
    #           SoC's own `elaborate`. Everything `wiring.connect` builds is this.
    #
    # Only looking for shared signals finds the first kind and makes the top level look
    # like a hub wired to everything, which is an artefact of where the assignment
    # happens to live rather than anything about the architecture. So top-level
    # assignments are traced through: an assignment whose right-hand side belongs to A
    # and whose left-hand side belongs to B is an edge from A to B.
    names = top_info.signal_names
    port_of = {}
    for fragment, name, _loc in top.subfragments:
        # `origins[0]` only. The rest of the chain is the `Module` the Elaboratable
        # returned, and a Module has a public `domain` attribute that touches every
        # signal it ever assigns -- walking it labels half the SoC "domain".
        for origin in (fragment.origins or ())[:1]:
            for sid, port in port_names(origin).items():
                port_of.setdefault(sid, (name, port))

    def label_of(sid):
        """The port this signal belongs to, falling back to its name's prefix."""
        if sid in port_of:
            return port_of[sid][1]
        label = names.get(signals[sid], signals[sid].name)
        return re.sub(r"\$\d+$", "", label).split("__")[0]

    def owner_of(sid):
        """Which module a signal belongs to, or None when that is genuinely ambiguous.

        Driven-by first, used-by second. A wishbone bus is used by both ends, so "who
        writes it" is the sharper question -- and for a status tap like `cpu.ibus.cyc`,
        read by the top level to light an LED, it is the only one with an answer.
        """
        drivers = [name for name in nodes if sid in drives[name]]
        if len(drivers) == 1:
            return drivers[0]
        users = [name for name in nodes if sid in used[name]]
        if len(users) == 1:
            return users[0]
        if not users:
            return "<top>"
        return None

    # Nets are counted per module pair and per pair of ports, in a canonical order, so
    # that the two directions of one bus are one relationship rather than two arrows.
    #     tally[(a, b)][(port on a, port on b)] = [a drives, b drives]
    tally = defaultdict(lambda: defaultdict(lambda: [0, 0]))

    def count(src, src_port, dst, dst_port):
        if src == dst:
            return
        if src < dst:
            tally[(src, dst)][(src_port, dst_port)][0] += 1
        else:
            tally[(dst, src)][(dst_port, src_port)][1] += 1

    # Direct: the same signal inside both modules. The top level is deliberately not a
    # candidate here -- every glue assignment mentions signals belonging to two other
    # modules, so including it would make it appear wired to the entire SoC when all it
    # did was host the assignment.
    for a, b in itertools.combinations(list(nodes), 2):
        for sid in used[a] & used[b]:
            if sid in clock_of:
                continue  # clocks are drawn as domains, not as twenty identical edges
            # Whoever writes it is upstream. On a wishbone bus the initiator drives adr,
            # dat_w, sel, cyc, stb, we, cti and bte against the target's ack, dat_r and
            # err, so the two ends never look alike.
            if sid in drives[b] and sid not in drives[a]:
                count(b, label_of(sid), a, label_of(sid))
            else:
                count(a, label_of(sid), b, label_of(sid))

    # Glue: an assignment in the SoC's own elaborate, joining two different signals.
    for _domain, statements in top.statements.items():
        for statement in statements:
            targets = list(statement._lhs_signals())
            sources = list(statement._rhs_signals())
            for target in targets:
                dst = owner_of(id(target))
                if dst is None:
                    continue
                signals.setdefault(id(target), target)
                for source in sources:
                    src = owner_of(id(source))
                    if src is None:
                        continue
                    signals.setdefault(id(source), source)
                    count(src, label_of(id(source)), dst, label_of(id(target)))

    edges = []
    for (a, b), ports in tally.items():
        groups = []
        for (port_a, port_b), (a_drives, b_drives) in ports.items():
            width = a_drives + b_drives
            src, src_port, dst, dst_port = a, port_a, b, port_b
            if b_drives > a_drives:
                src, src_port, dst, dst_port = b, port_b, a, port_a
            label = src_port if src_port == dst_port else f"{src_port} to {dst_port}"
            groups.append({"src": src, "dst": dst, "label": label, "width": width,
                           "directed": a_drives != b_drives})
        groups.sort(key=lambda g: -g["width"])

        # Up to three port groups are drawn separately, because that is where the
        # information is: the CPU reaches the arbiter over ibus, dbus and iobus, and
        # flattening those into one arrow would erase the cached/uncached split that
        # this SoC has already been debugged around twice. Beyond three the pair is
        # instrumentation -- the ILA taps eight PHY internals -- and one arrow with a
        # count says everything the eight would.
        #
        # The width floor is what keeps single-wire status taps together: the LED and
        # sideband logic reads one bit each from ibus, dbus and iobus, which is three
        # port groups and three arrows for what is one glance at whether the CPU moved.
        if len(groups) <= 3 and sum(g["width"] for g in groups) >= 4:
            edges += groups
        else:
            head = groups[0]
            more = f" +{len(groups) - 1} more" if len(groups) > 1 else ""
            edges.append({"src": head["src"], "dst": head["dst"],
                          "label": f"{head['label']}{more}",
                          "width": sum(g["width"] for g in groups),
                          "directed": head["directed"]})

    # ---- roles -----------------------------------------------------------------------
    #
    # Grouping is by distance from the bus fabric, measured on the edges just derived,
    # not by a list of names. So a new peripheral lands in the same box as the peripherals
    # it resembles structurally, and a module reachable only by its clock (the clock
    # generator, the sideband link) falls out as support because it genuinely is.
    adjacency = defaultdict(set)
    for edge in edges:
        adjacency[edge["src"]].add(edge["dst"])
        adjacency[edge["dst"]].add(edge["src"])

    fabric = {name for name, node in nodes.items() if node["role"] == "fabric"}
    hop = {name: 0 for name in fabric}
    frontier = list(fabric)
    while frontier:
        current = frontier.pop(0)
        for neighbour in adjacency[current]:
            if neighbour not in hop:
                hop[neighbour] = hop[current] + 1
                frontier.append(neighbour)

    for name, node in nodes.items():
        if node["master"]:
            node["role"] = "cpu"
        elif name in fabric:
            node["role"] = "fabric"
        elif hop.get(name) == 1:
            node["role"] = "mapped"
        elif name in hop:
            node["role"] = "downstream"
        else:
            # No data path to the fabric at all -- the clock generator and the sideband
            # link are here because that is genuinely what they are: the sideband answers
            # when the bus does not, which is the entire reason it exists.
            node["role"] = "support"
        node["hop"] = hop.get(name)

    # ---- compiled-out peripherals ----------------------------------------------------
    disabled = []
    for entry in guarded_out(SOC_SOURCE):
        addr = getattr(soc_module, entry["addr_name"], None)
        disabled.append({"name": entry["name"], "addr": addr,
                         "addr_name": entry["addr_name"],
                         "classes": entry["classes"]})

    return {
        "top": top_name,
        "nodes": nodes,
        # The top level is measured like any other node: it reaches the fabric through
        # the status bits it taps off the CPU's buses, or it does not.
        "top_node": {"name": "<top>", "cls": f"{top_name} glue and status logic",
                     "role": "downstream" if "<top>" in hop else "support",
                     "domains": {clock_of[sid] for sid in top_own if sid in clock_of},
                     "macros": [], "master": [], "window": None},
        "edges": edges,
        "windows": sorted(windows, key=lambda w: w["start"]),
        "masters": masters,
        "externals": externals,
        "macro_pads": macro_pads,
        "domains": list(top.domains),
        "clock_sources": clock_sources,
        "disabled": disabled,
        "fragment_count": len(design.fragments),
        "default_clk_mhz": platform.default_clk_frequency / 1e6,
    }


# --------------------------------------------------------------------------------------
# Emitting
# --------------------------------------------------------------------------------------

# Titles describe the measurement, not a category someone chose: each is the distance
# from the bus fabric that put a module in that box.
ROLE_TITLES = {
    "cpu":        "CPU: the buses registered with the arbiter",
    "fabric":     "Bus fabric",
    "mapped":     "One hop from the decoder: the address map",
    "downstream": "Two or more hops: cores behind bridges, PHYs, pads, top-level glue",
    "support":    "Reached only by its clock: no data path to the fabric",
}
ROLE_ORDER = ["cpu", "fabric", "mapped", "downstream", "support"]


def ident(name):
    """A mermaid-safe node id."""
    return "n_" + re.sub(r"[^A-Za-z0-9_]", "_", name)


def node_label(node):
    parts = [f"<b>{node['name'].strip('<>')}</b>", node["cls"]]
    window = node.get("window")
    if window:
        parts.append(f"0x{window['start']:08x}..0x{window['end'] - 1:08x}")
        if window["registers"] > 1:
            parts.append(f"{window['registers']} registers")
    if node.get("macros"):
        parts.append("macro: " + ", ".join(node["macros"]))
    if node.get("domains"):
        parts.append("[" + " + ".join(sorted(node["domains"])) + "]")
    return "<br/>".join(parts)


def mermaid(model):
    """The diagram, as mermaid flowchart source."""
    out = ["---", f"title: {model['top']} on Cynthion r1.4 -- derived from the gateware",
           "---", "flowchart LR"]

    every = dict(model["nodes"])
    every["<top>"] = model["top_node"]

    # Modules, boxed by the role each was measured to have.
    for role in ROLE_ORDER:
        members = [n for n in every.values() if n["role"] == role]
        if not members:
            continue
        out.append(f'  subgraph sg_{role}["{ROLE_TITLES[role]}"]')
        for node in sorted(members, key=lambda n: n["name"]):
            out.append(f'    {ident(node["name"])}["{node_label(node)}"]')
        out.append("  end")

    # Compiled-out peripherals: in the picture, visibly not in the design.
    if model["disabled"]:
        out.append('  subgraph sg_off["Present in the source, compiled out"]')
        for entry in model["disabled"]:
            cls = entry["classes"].get(entry["name"], "")
            label = [f"<b>{entry['name']}</b>"]
            if cls:
                label.append(cls)
            if entry["addr"] is not None:
                label.append(f"0x{entry['addr']:08x}")
            label.append(f"disabled: {entry['addr_name']} guarded by a false test")
            out.append(f'    {ident("off_" + entry["name"])}["{"<br/>".join(label)}"]')
        out.append("  end")

    # Off-chip: board resources the design actually asked for, plus the pads that only a
    # hard macro reaches.
    out.append('  subgraph sg_ext["Off-chip (board resources this design requests)"]')
    for entry in model["externals"].values():
        count = f" x{entry['count']}" if entry["count"] > 1 else ""
        out.append(f'    {ident("ext_" + entry["resource"])}'
                   f'["<b>{entry["resource"]}{count}</b><br/>{entry["label"]}"]')
    for pad in model["macro_pads"]:
        out.append(f'    {ident("pad_" + pad["macro"])}'
                   f'["<b>{pad["macro"]}</b><br/>{pad["label"]}"]')
    out.append("  end")

    # Clock domains. Only the generator and the modules that straddle a boundary get an
    # edge: every other module carries its domain in its own label, and drawing twenty
    # identical clock lines would bury the one crossing that matters.
    out.append('  subgraph sg_clk["Clock domains"]')
    for domain in model["domains"]:
        members = sum(1 for n in model["nodes"].values() if domain in n["domains"])
        out.append(f'    {ident("clk_" + domain)}(["{domain}<br/>{members} modules"])')
    out.append("  end")

    for name in sorted(model["clock_sources"]):
        for domain in sorted(model["nodes"][name]["domains"]):
            out.append(f'  {ident(name)} ==> {ident("clk_" + domain)}')
    for name, node in sorted(model["nodes"].items()):
        if len(node["domains"]) > 1 and name not in model["clock_sources"]:
            for domain in sorted(node["domains"]):
                out.append(f'  {ident("clk_" + domain)} -.->|"crossing"| {ident(name)}')

    # Module to module.
    for edge in sorted(model["edges"], key=lambda e: (-e["width"], e["label"])):
        label = edge["label"]
        window = model["nodes"].get(edge["dst"], {}).get("window")
        if window and edge["src"] in {n for n, x in model["nodes"].items()
                                      if x["role"] == "fabric"}:
            label = f"{label} @0x{window['start']:08x}"
        arrow = "-->" if edge["directed"] else "---"
        out.append(f'  {ident(edge["src"])} {arrow}|"{label} ({edge["width"]})"| '
                   f'{ident(edge["dst"])}')

    # Module to pads.
    for entry in model["externals"].values():
        target = ident("ext_" + entry["resource"])
        for owner in sorted(entry["owners"]):
            if entry["dirs"] == {"o"}:
                out.append(f'  {ident(owner)} --> {target}')
            elif entry["dirs"] == {"i"}:
                out.append(f'  {target} --> {ident(owner)}')
            else:
                out.append(f'  {ident(owner)} <--> {target}')
    for pad in model["macro_pads"]:
        out.append(f'  {ident(pad["owner"])} <-->|"{pad["macro"]}"| '
                   f'{ident("pad_" + pad["macro"])}')

    # The decoder window a compiled-out peripheral would occupy, dashed.
    fabric = [n for n, x in model["nodes"].items() if x["role"] == "fabric"]
    decoder = next((n for n in fabric if "ecoder" in model["nodes"][n]["cls"]), None)
    for entry in model["disabled"]:
        if decoder:
            addr = f" @0x{entry['addr']:08x}" if entry["addr"] is not None else ""
            out.append(f'  {ident(decoder)} -.->|"wishbone{addr}"| '
                       f'{ident("off_" + entry["name"])}')

    out += [
        "  classDef off stroke-dasharray: 6 4,color:#888,stroke:#888",
        "  classDef ext stroke-width:2px",
    ]
    if model["disabled"]:
        out.append("  class " + ",".join(ident("off_" + e["name"])
                                         for e in model["disabled"]) + " off")
    ext_ids = ([ident("ext_" + e["resource"]) for e in model["externals"].values()]
               + [ident("pad_" + p["macro"]) for p in model["macro_pads"]])
    if ext_ids:
        out.append("  class " + ",".join(ext_ids) + " ext")
    return "\n".join(out) + "\n"


def validate(diagram):
    """Structural check on the emitted mermaid, since there is no renderer here to fail.

    Not a parser. It catches the two mistakes an emitter actually makes -- an unbalanced
    `subgraph`, and an edge to a node that was never declared -- either of which renders
    as a silently different diagram rather than as an error.
    """
    complaints = []
    depth = 0
    declared = set()
    referenced = set()
    for line in diagram.splitlines():
        line = line.strip()
        if line.startswith("subgraph "):
            depth += 1
            continue
        if line == "end":
            depth -= 1
            if depth < 0:
                complaints.append("an `end` with no `subgraph`")
            continue
        declaration = re.match(r"^(n_\w+)\s*[\[(]", line)
        if declaration:
            declared.add(declaration.group(1))
            continue
        if "--" in line or "==" in line:
            referenced.update(re.findall(r"\bn_\w+\b", line))
    if depth:
        complaints.append(f"{depth} unclosed subgraph(s)")
    for node in sorted(referenced - declared):
        complaints.append(f"edge to an undeclared node: {node}")
    return complaints


def dot(model):
    """The same graph as graphviz, so the HTML renders offline.

    Mermaid needs a browser and a copy of mermaid.js; `dot` is already installed and
    produces a self-contained SVG. Same model, same nodes, same edges -- this is a second
    rendering, not a second diagram.
    """
    lines = ["digraph soc {", '  rankdir=LR; node [shape=box, fontname="sans", '
             'fontsize=10]; edge [fontname="sans", fontsize=8];']
    every = dict(model["nodes"])
    every["<top>"] = model["top_node"]
    for index, role in enumerate(ROLE_ORDER):
        members = [n for n in every.values() if n["role"] == role]
        if not members:
            continue
        lines.append(f'  subgraph cluster_{index} {{ label="{ROLE_TITLES[role]}"; '
                     'style=rounded; color="#888";')
        for node in members:
            label = node_label(node).replace("<br/>", r"\n")
            label = re.sub(r"</?b>", "", label)
            lines.append(f'    "{node["name"]}" [label="{label}"];')
        lines.append("  }")
    for entry in model["externals"].values():
        lines.append(f'  "ext:{entry["resource"]}" '
                     f'[shape=box3d,label="{entry["resource"]}"];')
        for owner in entry["owners"]:
            lines.append(f'  "{owner}" -> "ext:{entry["resource"]}" [dir=both];')
    for pad in model["macro_pads"]:
        lines.append(f'  "pad:{pad["macro"]}" [shape=box3d,label="{pad["macro"]}"];')
        lines.append(f'  "{pad["owner"]}" -> "pad:{pad["macro"]}" [dir=both];')
    for entry in model["disabled"]:
        lines.append(f'  "off:{entry["name"]}" [style=dashed,color="#888",'
                     f'label="{entry["name"]}\\n(compiled out)"];')
    for edge in model["edges"]:
        arrow = "" if edge["directed"] else ", dir=none"
        lines.append(f'  "{edge["src"]}" -> "{edge["dst"]}" '
                     f'[label="{edge["label"]}"{arrow}];')
    lines.append("}")
    return "\n".join(lines)


def html(model, diagram):
    """A page that renders without a network, and carries the mermaid source with it."""
    svg = ""
    if shutil.which("dot"):
        result = subprocess.run(["dot", "-Tsvg"], input=dot(model),
                                capture_output=True, text=True)
        if result.returncode == 0:
            svg = result.stdout[result.stdout.find("<svg"):]

    rows = "".join(
        f"<tr><td>{w['name']}</td><td><code>0x{w['start']:08x}</code></td>"
        f"<td><code>0x{w['end'] - 1:08x}</code></td><td>{w['owner']}</td>"
        f"<td>{w['registers']}</td></tr>"
        for w in model["windows"])

    return f"""<!doctype html>
<meta charset="utf-8">
<title>{model['top']} architecture</title>
<style>
 body {{ font-family: sans-serif; margin: 2rem; max-width: 100%; }}
 svg {{ max-width: 100%; height: auto; }}
 table {{ border-collapse: collapse; }}
 td, th {{ border: 1px solid #ccc; padding: 2px 8px; font-size: 90%; }}
 pre {{ background: #f4f4f4; padding: 1rem; overflow-x: auto; font-size: 85%; }}
</style>
<h1>{model['top']} on Cynthion r1.4</h1>
<p>Generated by <code>scripts/soc_diagram.py</code> from the elaborated gateware.
{len(model['nodes'])} top-level modules out of {model['fragment_count']} fragments.</p>
{svg or "<p>graphviz not found, so only the mermaid source is here.</p>"}
<h2>Address map, from the wishbone decoder</h2>
<table><tr><th>window</th><th>start</th><th>end</th><th>module</th><th>registers</th></tr>
{rows}</table>
<h2>Mermaid source</h2>
<pre>{diagram.replace('&', '&amp;').replace('<', '&lt;')}</pre>
"""


def markdown(model, diagram):
    rows = "\n".join(
        f"| `{w['name']}` | `0x{w['start']:08x}` | `0x{w['end'] - 1:08x}` | "
        f"`{w['owner']}` | {w['registers']} |"
        for w in model["windows"])
    masters = ", ".join(f"`{owner}.{bus}`" for owner, bus in model["masters"])
    return f"""# {model['top']} -- Cynthion r1.4 RISC-V SoC

Generated by `scripts/soc_diagram.py`. Every node, edge and address below was read out
of the elaborated gateware; do not edit this file, rerun the script.

```mermaid
{diagram.strip()}
```

## Address map (wishbone decoder windows)

| window | start | end | module | registers |
|---|---|---|---|---|
{rows}

## CPU masters

{masters}

## Clock domains

{", ".join(f"`{d}`" for d in model["domains"])}
"""


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stdout", action="store_true",
                        help="print the mermaid source as well as writing it")
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    MMD.parent.mkdir(parents=True, exist_ok=True)

    with LOG.open("w") as handle:
        def emit(text=""):
            print(text, flush=True)
            handle.write(text + "\n")

        emit("Deriving the SoC diagram from the gateware itself")
        emit(f"source: {SOC_SOURCE.relative_to(ROOT)}")
        emit()

        model = analyse(emit)

        emit(f"{len(model['nodes'])} top-level modules "
             f"({model['fragment_count']} fragments in total)")
        emit(f"clock domains: {', '.join(model['domains'])}"
             f"  (source: {', '.join(sorted(model['clock_sources']))})")
        emit(f"CPU masters: "
             f"{', '.join(f'{o}.{b}' for o, b in model['masters'])}")
        emit()
        emit("address map, from the decoder's own memory map:")
        for window in model["windows"]:
            emit(f"  {window['name']:<14} 0x{window['start']:08x}..0x{window['end'] - 1:08x}"
                 f"  {window['owner']}  ({window['registers']} registers)")
        emit()
        emit("board resources requested:")
        for entry in model["externals"].values():
            emit(f"  {entry['resource']:<14} x{entry['count']}  "
                 f"{'/'.join(sorted(entry['dirs']))}  <- {', '.join(sorted(entry['owners']))}")
        emit("hard macros reaching pads:")
        for pad in model["macro_pads"]:
            emit(f"  {pad['macro']:<14} in {pad['owner']}")
        if model["disabled"]:
            emit("compiled out by a false guard:")
            for entry in model["disabled"]:
                addr = f"0x{entry['addr']:08x}" if entry["addr"] is not None else "?"
                emit(f"  {entry['name']:<14} {addr} ({entry['addr_name']})")
        emit()

        diagram = mermaid(model)
        complaints = validate(diagram)
        for complaint in complaints:
            emit(f"  *** malformed mermaid: {complaint}")
        if not complaints:
            emit("mermaid structure checks out (subgraphs balanced, every edge lands "
                 "on a declared node)")

        MMD.write_text(diagram)
        MD.write_text(markdown(model, diagram))
        HTML.write_text(html(model, diagram))

        emit(f"{len(diagram.splitlines())} lines of mermaid, "
             f"{sum(1 for l in diagram.splitlines() if '[' in l and 'subgraph' not in l)} "
             f"nodes, {len(model['edges'])} module edges")
        emit(f"wrote {MMD.relative_to(ROOT)}")
        emit(f"wrote {MD.relative_to(ROOT)}")
        emit(f"wrote {HTML.relative_to(ROOT)}")
        emit(f"log:   {LOG.relative_to(ROOT)}")
        emit()
        emit(f"view it with:  code-insiders {HTML}")

        if args.stdout:
            emit()
            emit(diagram)

    return 0


if __name__ == "__main__":
    sys.exit(main())

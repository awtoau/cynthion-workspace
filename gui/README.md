# cynthion_monitor

Desktop dashboard for a Cynthion: board topology, the three consoles, and the
PAC1954 rails, in one window.

It was retired to `debris/` and brought back; this is where it lives now.

## Build and run

Flutter is not on the default PATH on this machine:

```bash
export PATH=/home/dan/development/flutter/bin:$PATH
cd gui
flutter pub get
flutter run -d linux          # or: flutter build linux --debug
flutter test
```

Built and run against **Flutter 3.44.0 / Dart 3.12.0**, which satisfies the
`sdk: ^3.11.5` in `pubspec.yaml`.

`pubspec.yaml` has a path dependency on `../../awto-gui-inspect-flutter`, a
sibling checkout of the `awto_gui_inspect` package one level above the
workspace. Without it `pub get` fails — it is not on pub.dev.

### Seeing it without a display of your own

```bash
gui/tools/screenshot.py --out tmp/gui.png      # runs it on an Xvfb display
gui/tools/screenshot.py --hold                 # leaves it up; prints the DISPLAY
```

Keep the `--size` at the size the engine starts with (1280x720 by default). A
window that differs makes the GL resize handshake time out under llvmpipe and
every capture comes back torn.

## What is real and what is not

Nothing here talks to hardware directly. The app connects to a daemon over a
WebSocket (`ws://127.0.0.1:8765` by default, auto-retried on startup) and
expects newline-delimited JSON.

With no daemon running — the state you get today — the consoles and the rail
readings are **generated**, and every panel says so with a `DEMO` chip in its
header. The topology graph is real: it is read from
`assets/hardware/cynthion.json`, which is extracted from the KiCad schematic.

On connect the app sends `{"cmd":"hello"}` and waits one second for:

```json
{"evt":"hello", "variant":"awto", "version":"0.3.1", "board":"cynthion r1.4",
 "caps":["tty","power","topology","riscv"]}
```

A peer that answers with `variant: "awto"` gets the advanced panels. A peer that
answers anything else, or nothing, is treated as a stock Cynthion: the panels
that need the fork say so rather than showing numbers. See
`lib/models/device_profile.dart`.

## Regenerating the board file

```bash
git submodule update --init repos/cynthion-hardware
gui/tools/extract-hardware.py --kicad repos/cynthion-hardware
gui/tools/extract-hardware.py --check          # report without writing
```

This exports a netlist with `kicad-cli` and fills in, for every node with a
`kicad_ref`, the part number and description; for every connector, the pinout
with net names; and for every connection between two of them, the interface,
the nets, the voltage domain and which end drives.

It prints a warning for each connection the schematic does not support. Those
warnings are the point as much as the data is — they are how the board file's
`kicad_ref` values were found to name the wrong parts.

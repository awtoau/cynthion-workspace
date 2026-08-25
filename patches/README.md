# Patches against tools this repo does not own

Each patch here is a change to an upstream tool that a build needs, kept as a
tracked file because the built binary lives in `tmp/` and `tmp/` is disposable.
**The patch is the durable part; the binary is not.**

Where a patch belongs upstream, the issue that tracks that is named below.

## `nextpnr-ecp5-dqsi-fanout.patch`

Drops the sole-consumer half of `pack_dqsbuf`, so a DQS-group pad can drive
`DQSBUFM.DQSI` **and** be read as a level in fabric.

Needed by: any `CYNTHION_HYPERRAM_DQS=1` build since `1a89c86`. Without it the
build fails at packing with

    ERROR: DQSBUFM '...' DQSI input must be connected only to a top level input

Why it is correct, and why upstream should take it: #543. In short -- prjtrellis
`PICL0_DQS2/bits.db` has the pad wire hardwired into the DQS block AND into
IOLOGIC AND onto the `JDIA` mux to fabric at **zero configuration bits**;
Diamond's own mapper accepts the structure; and the rule arrived whole in
nextpnr `eb45956d` (2019) with no stated rationale and has never been revisited.
Proven on silicon: the board runs a bitstream built with it and reports
`rwds-ask SEEN` on the DQS path.

Applies to nextpnr at `ee605e2b`, the commit oss-cad-suite ships.

### Rebuilding the binary

`pytrellis` must match `libboost_python314` -- built against the default
free-threaded 3.15t it compiles and then fails to import with
`undefined symbol: _Py_DecRefShared`.

    git -C <nextpnr> worktree add <build-dir> ee605e2b
    git -C <prjtrellis> worktree add <trellis-dir> HEAD

    cd <trellis-dir>/libtrellis
    cmake -B build -DCMAKE_BUILD_TYPE=Release \
        -DPython3_EXECUTABLE=/usr/bin/python3.14 \
        -DCMAKE_INSTALL_PREFIX=<trellis-install>
    cmake --build build -j && cmake --install build

    cd <build-dir>
    git apply <this repo>/patches/nextpnr-ecp5-dqsi-fanout.patch
    cmake -B build -DARCH=ecp5 -DCMAKE_BUILD_TYPE=Release \
        -DBUILD_GUI=OFF -DBUILD_PYTHON=OFF \
        -DTRELLIS_LIBDIR=<trellis-install>/lib64/trellis \
        -DTRELLIS_DATADIR=$HOME/opt/oss-cad-suite/share/trellis \
        -DPython3_EXECUTABLE=/usr/bin/python3.14
    cmake --build build -j

Then point a build at it:

    CYNTHION_HYPERRAM_DQS=1 CYNTHION_NEXTPNR_BIN=<build-dir>/build \
        python3 scripts/soc_run.py --router router1

`soc_run` prints `nextpnr OVERRIDDEN` when it is not the toolchain's, so no
bitstream from a patched router is ever silently attributed to oss-cad-suite.

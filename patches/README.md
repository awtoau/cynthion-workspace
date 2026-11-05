# Patches against tools this repo does not own

Each patch here is a change to an upstream tool that a build needs, kept as a
tracked file because the built binary lives in `tmp/` and `tmp/` is disposable.
**The patch is the durable part; the binary is not.**

Where a patch belongs upstream, the issue that tracks that is named below.

## yosys-opt-clean-const-lookups.patch

- `opt_clean` data race: parallel workers read two `dict`s through non-const `operator[]` / `at()`, which rehash.
- Upstream: [YosysHQ/yosys#6418](https://github.com/YosysHQ/yosys/issues/6418). Not posted. Tests, risks: [`docs/yosys-opt-clean-race.md`](../docs/yosys-opt-clean-race.md).
- Applies to yosys `b8d84f823` (0.70+329): `git apply`.

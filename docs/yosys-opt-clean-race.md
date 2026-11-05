# yosys `opt_clean` memory race: patch, tests, risks

Upstream: [YosysHQ/yosys#6418](https://github.com/YosysHQ/yosys/issues/6418) (open). Tracked here: [#356](https://github.com/awtoau/cynthion-workspace/issues/356). Patch: [`patches/yosys-opt-clean-const-lookups.patch`](../patches/yosys-opt-clean-const-lookups.patch).

## Bug
- `fixup_unused_cells_and_mems()` (`passes/opt/clean/cells_all.cc`) runs on a thread pool.
- Workers call non-const `dict::operator[]` (`mem_analysis.indices[mem_id]`) and `at()` (`traversal.mem2cells.at(mem)`).
- Those go through `do_lookup()`, which rehashes lazily; the dict is "armed" after the single-threaded fill when its entry count is 12, 30, 51, 106, 216, ...
- Concurrent rehash vs reads: MEMID assert (`rtlil.cc:2631`), `double free`, or a hang on a self-looping bucket.
- Our August `OPT_CLEAN` aborts ([#356](https://github.com/awtoau/cynthion-workspace/issues/356)) are this assert. The August silent different-netlist run is not explained by it.

## Patch
- Two `const dict &` bindings before `subpool.run`; `find()` const / iterate instead of `count()` + `[]` / `at()`.
- 12 lines in one function. Applies to `b8d84f823` (0.70+329). Same approach as the diff posted in the [yosys#6418](https://github.com/YosysHQ/yosys/issues/6418) thread; written after reading it, then verified independently (tests below).
- Const `count` / `find` / `at` use `do_lookup_no_rehash` (`kernel/hashlib.h:527`): no mutation.

## Tests

Host `selwyn`, 2026-11-05, clang 22.1.8 Release, `YOSYS_MAX_THREADS=16 YOSYS_WORK_UNITS_PER_THREAD=1`, design = N memories in one module. Runner: [`scripts/yosys_opt_clean_race.py`](../scripts/yosys_opt_clean_race.py).

| memories | unpatched failed / runs | patched failed / runs |
|---|---|---|
| 11, 13 (controls) | 0 / 40 each | 0 / 200 each |
| 12 | 2 / 40 | 0 / 200 |
| 30 | 4 / 40 (assert, `double free`, hang) | 0 / 200 |
| 51 | 7 / 40 | 0 / 200 |
| 106 | 9 / 40 | 0 / 100 |
| 216 | 9 / 40 | 0 / 100 |

- Same build flags otherwise; the stock 0.70+329 and 0.65+57 binaries fail the same way (12 / 30 / 51: 2, 4, 5 and 1, 1, 3 of 20).
- ThreadSanitizer (RelWithDebInfo, 12 and 51 memories, 12 runs each): unpatched 24 / 24 flagged (23 rc=66, 1 hang), reports at `cells_all.cc:266` and `:267` only; patched 0 / 24, no warnings.
- Real design (our SoC `top.il` + CPU core, `synth_ecp5`, 39 MB `top.json`): unpatched at 1 thread, patched at 1 thread, patched at default threads x3 all give one SHA-256. Output is unchanged.

## Risks
- Site 2 (`mem2cells.at`) never showed in TSAN (5 runs). It is the same pattern and the same fix; it is changed on reasoning, not shown independently.
- Other non-const dict access in other parallel sections is not audited. TSAN only covers the paths this design exercises.
- A stress test is probabilistic: unpatched fails 5-22% per run, so 200 clean runs rule out a fix that only lowers the rate, not out every race. Upstream has no way to test this deterministically.
- The general fix (rehash in `do_insert`, or an explicit freeze) touches every `hashlib` user; this patch avoids that.
- Tested on x86-64 Linux, clang, `read_verilog` frontend only. Not tested: `read_slang`, other targets, macOS.
- Our flow keeps `YOSYS_MAX_THREADS=1` regardless (costs nothing: 104.5 s pinned vs 103.4 s default on the SoC). Dropping it needs a release containing a fix.

## Upstream
- Not posted. Another user's repo: needs Dan's explicit go, then a comment on [yosys#6418](https://github.com/YosysHQ/yosys/issues/6418) with the table above, or a PR.

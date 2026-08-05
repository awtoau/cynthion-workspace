## Reproduction must use their tooling, not ours

Every number here was taken with our build system, our platform and our harness.
That proves nothing upstream can check. Before any PR, it has to rebuild and
re-measure under `BUILD.md`.

**This cannot be done on this machine.** `BUILD.md:97` instructs

    pip uninstall -y -r <(pip freeze)

and we run the free-threaded system environment with no venv. Their install
would take it apart.

**They have no container.** `ci/` is `build.sh` (123 B) and `test.sh` (110 B),
one workflow, no Dockerfile, no nix, no devcontainer.

### Proposal

A container built from `BUILD.md` verbatim, with the board passed through:

- their Python, their Yosys/OSS CAD Suite, their Rust, `pip install ".[gateware]"`
- our harness copied in, nothing of our build system
- `--device` for the Cynthion, so measurement happens on real hardware
- pinned base image and recorded tool versions, so a rung is reproducible

Isolation both ways: their install cannot touch this machine, and our
environment cannot flatter their result.

Worth offering upstream as its own small PR — they have nothing like it, and it
is the difference between a number they must trust and one they can run.

### Rules for the submission

- **Terse.** Message and docs both.
- **Cite numbers, measured on real hardware.** No estimates, no simulation
  figures presented as measurements.
- **Ship the harness.** A claim without the code to re-take it is an assertion.
- **Double-check every figure** against `results.json` before it is quoted.

### Verify before submitting

- [ ] Container builds from `BUILD.md` with no step from our tree
- [ ] Their gateware builds unmodified inside it
- [ ] The ladder re-measured inside it, on hardware, and the numbers match
- [ ] Any that do not match are investigated before anything is sent

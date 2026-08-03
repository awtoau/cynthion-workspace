import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "ecp5-test"))

import fabric_arcs  # noqa: E402
import fabric_placement  # noqa: E402
from fabric.fabric_gateware import signature_layout  # noqa: E402
from fabric_sweep import variant  # noqa: E402


class FabricCoverageTests(unittest.TestCase):
    @staticmethod
    def _config(path, tile, arcs):
        lines = [f".tile {tile}:PLC2"]
        lines.extend(f"arc: {dest} {source}" for dest, source in arcs)
        path.write_text("\n".join(lines) + "\n")

    def test_signature_layout_is_reproducible_permutation(self):
        first = signature_layout(31, 0x12345678)
        self.assertEqual(first, signature_layout(31, 0x12345678))
        self.assertEqual(sorted(first[0]), list(range(31)))
        self.assertEqual(len(first[1]), 31)
        self.assertTrue(all(0 <= rotation < 32 for rotation in first[1]))
        self.assertNotEqual(first, signature_layout(31, 0x12345679))
        self.assertEqual(signature_layout(4, 0), (list(range(4)), [0] * 4))

    def test_greedy_order_prioritises_type_then_instance_arcs(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            one = root / "one.config"
            two = root / "two.config"
            three = root / "three.config"
            self._config(one, "R1C1", [("D0", "S0")])
            self._config(two, "R1C2", [("D0", "S0"), ("D1", "S1")])
            self._config(three, "R2C2", [("D0", "S0")])
            known = {"PLC2": {("D0", "S0"), ("D1", "S1")}}
            ordered, steps = fabric_arcs.greedy_order([one, three, two], known)
            self.assertEqual(ordered, [two, one, three])
            self.assertEqual(steps[0]["type_new"], 2)
            self.assertEqual(steps[1]["instance_new"], 1)

    def test_forced_overlap_uses_largest_possible_union(self):
        first = set(range(8))
        second = set(range(2, 10))
        # Two sets of eight in ten sites must intersect by six; their largest
        # possible union is ten, hence a 0.6 minimum Jaccard overlap.
        self.assertEqual(fabric_placement.forced_overlap(first, second, 10), 0.6)

    def test_default_sweep_uses_full_density_routable_fanins(self):
        self.assertEqual([variant(seed)["tree_fanin"] for seed in range(1, 5)],
                         [3, 4, 3, 4])


if __name__ == "__main__":
    unittest.main()

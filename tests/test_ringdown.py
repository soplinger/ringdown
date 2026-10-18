import csv
import math
import os
import sys
import tempfile
import unittest
from fractions import Fraction

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ringdown  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLE = os.path.join(ROOT, "examples", "studio.room")

# A 5 x 4 x 3 m room (V = 60 m3) whose 40 m2 of surface all use a 0.25 absorber.
# Total absorption is 10 sabins in every band, so the hand values are easy to check.
BOX = """\
room Box
size 5 4 3
custom absorber 0.25 0.25 0.25 0.25 0.25 0.25
surface all 40 absorber
"""


def _parse(text):
    room, issues = ringdown.parse_room(text)
    return room, issues


class SabineEyringTests(unittest.TestCase):
    def test_sabine_matches_hand_calculation(self):
        room, _ = _parse(BOX)
        data = ringdown.analyse(room)
        # RT60 = 0.161 * 60 / 10 = 0.966 s
        self.assertAlmostEqual(data["sabine"][2], 0.966, places=6)

    def test_eyring_matches_hand_calculation(self):
        room, _ = _parse(BOX)
        data = ringdown.analyse(room)
        # alpha = 0.25, RT60 = 9.66 / (-40 * ln 0.75) = 0.83947 s
        self.assertAlmostEqual(data["eyring"][2], 0.83947, places=4)
        self.assertLess(data["eyring"][2], data["sabine"][2])

    def test_critical_distance_uses_directivity(self):
        room, _ = _parse("directivity 2\n" + BOX)
        data = ringdown.analyse(room)
        # Dc = 0.141 * sqrt(Q * A) with Q = 2 and A = 10 sabins
        self.assertAlmostEqual(data["critical"][2], 0.141 * math.sqrt(20), places=6)

    def test_treated_scenario_lowers_rt60(self):
        text = """\
room Tiled
size 5 4 3
custom absorber 0.25 0.25 0.25 0.25 0.25 0.25
surface floor 20 concrete
surface rest 20 concrete
treat floor absorber
"""
        room, issues = _parse(text)
        self.assertIsNotNone(room)
        before = ringdown.analyse(room)["sabine"][3]
        after = ringdown.analyse(room, treated=True)["sabine"][3]
        self.assertLess(after, before)


class ParserTests(unittest.TestCase):
    def test_feet_units_convert_exactly(self):
        room, _ = _parse("units ft\nsize 10 10 10\nsurface a 100 concrete\n")
        self.assertEqual(room.volume, Fraction(3048, 10000) ** 3 * 1000)

    def test_errors_carry_line_numbers_and_block_the_room(self):
        text = "room X\nsize 4 4 3\nsurface a 10 concrete\nsurface b 10 carpett\nfrobnicate\n"
        room, issues = _parse(text)
        self.assertIsNone(room)
        by_line = {issue.line: issue for issue in issues}
        self.assertEqual(by_line[4].severity, "error")
        self.assertIn("did you mean 'carpet'", by_line[4].message)
        self.assertIn("unknown directive", by_line[5].message)

    def test_duplicate_surface_label_is_an_error(self):
        text = "size 4 4 3\nsurface a 10 concrete\nsurface a 5 concrete\n"
        room, issues = _parse(text)
        self.assertIsNone(room)
        self.assertEqual([issue.line for issue in issues if issue.severity == "error"], [3])

    def test_custom_coefficient_out_of_range_is_rejected(self):
        text = "size 4 4 3\ncustom bad 1.5 0.1 0.1 0.1 0.1 0.1\nsurface a 10 bad\n"
        room, issues = _parse(text)
        self.assertIsNone(room)
        self.assertTrue(any("between 0 and 1" in issue.message for issue in issues))

    def test_oversized_surfaces_warn_without_blocking(self):
        room, issues = _parse("size 2 2 2\nsurface a 100 concrete\n")
        self.assertIsNotNone(room)
        self.assertEqual([issue.severity for issue in issues], ["warning"])


class PlanningTests(unittest.TestCase):
    def test_target_solver_sizes_named_material(self):
        room, _ = _parse(BOX)
        rows = ringdown.treatment_needed(room, Fraction(1, 2), None)
        # Wanted 0.161 * 60 / 0.5 = 19.32 sabins; 10 already present, so 9.32 short.
        self.assertEqual(rows[2][1], Fraction(932, 100))
        foam = ringdown.Room(room.name, room.size, room.surfaces, {}, dict(room.materials), room.directivity)
        foam.materials["foam"] = tuple(Fraction(9, 10) for _ in range(6))
        rows = ringdown.treatment_needed(foam, Fraction(1, 2), "foam")
        # Gain over concrete at 500 Hz is 0.9 - 0.015 = 0.885, so area = 9.32 / 0.885
        self.assertAlmostEqual(float(rows[2][2]), 9.32 / 0.885, places=6)

    def test_gate_exit_codes(self):
        self.assertEqual(ringdown.main(["report", EXAMPLE, "--gate-mid", "0.01"]), 2)
        self.assertEqual(ringdown.main(["report", EXAMPLE, "--gate-mid", "100"]), 0)

    def test_csv_export_has_a_row_per_band_and_scenario(self):
        with tempfile.TemporaryDirectory() as folder:
            out = os.path.join(folder, "bands.csv")
            self.assertEqual(ringdown.main(["report", EXAMPLE, "--csv", out]), 0)
            with open(out, newline="", encoding="utf-8") as handle:
                rows = list(csv.reader(handle))
        self.assertEqual(rows[0][0], "scenario")
        self.assertEqual(len(rows), 1 + 2 * len(ringdown.BANDS))
        self.assertEqual({row[0] for row in rows[1:]}, {"as_described", "treated"})


class CliTests(unittest.TestCase):
    def test_example_room_lints_clean(self):
        with open(EXAMPLE, encoding="utf-8") as handle:
            room, issues = ringdown.parse_room(handle.read())
        self.assertIsNotNone(room)
        self.assertEqual(issues, [])

    def test_check_returns_one_for_a_bad_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "bad.room")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("frobnicate\n")
            self.assertEqual(ringdown.main(["check", path]), 1)

    def test_materials_command_succeeds(self):
        self.assertEqual(ringdown.main(["materials"]), 0)


if __name__ == "__main__":
    unittest.main()

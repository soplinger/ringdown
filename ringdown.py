#!/usr/bin/env python3
"""Ringdown: offline room acoustics planner.

Reads a plain-text room file and works out octave-band absorption, Sabine and
Eyring reverberation times (RT60), critical distance, the effect of planned
treatments, and the absorber area needed to reach a target RT60. Standard
library only; nothing is fetched from the network.
"""
import argparse
import csv
import difflib
import math
import sys
from dataclasses import dataclass
from fractions import Fraction

BANDS = (125, 250, 500, 1000, 2000, 4000)
SABINE_K = Fraction(161, 1000)  # Sabine constant in s/m (metric)
CRITICAL_K = 0.141  # critical distance = 0.141 * sqrt(Q * A), metres
FEET_TO_METRES = Fraction(3048, 10000)
UNIT_FACTORS = {"m": Fraction(1), "ft": FEET_TO_METRES}
MID_INDEXES = (BANDS.index(500), BANDS.index(1000))
EYRING_THRESHOLD = 0.2  # above this mean absorption Sabine runs long
SHELL_TOLERANCE = Fraction(105, 100)
BASE_MATERIAL = "concrete"
DIRECTIVES = ("units", "room", "size", "directivity", "custom", "surface", "treat")


def _coeffs(text):
    return tuple(Fraction(value) for value in text.split())


# Octave-band absorption coefficients at 125-4000 Hz. These are typical
# illustrative values, not manufacturer data; replace them with the product
# data sheet for a real design (use a `custom` line).
MATERIALS = {
    "concrete": _coeffs("0.01 0.01 0.015 0.02 0.02 0.02"),
    "plaster": _coeffs("0.02 0.02 0.03 0.04 0.05 0.05"),
    "painted_brick": _coeffs("0.01 0.01 0.02 0.02 0.02 0.03"),
    "glass": _coeffs("0.18 0.06 0.04 0.03 0.02 0.02"),
    "wood_panel": _coeffs("0.28 0.22 0.17 0.09 0.10 0.11"),
    "carpet": _coeffs("0.02 0.06 0.14 0.37 0.60 0.65"),
    "acoustic_tile": _coeffs("0.45 0.55 0.70 0.85 0.95 0.90"),
    "curtains": _coeffs("0.14 0.35 0.55 0.72 0.70 0.65"),
}


@dataclass(frozen=True)
class Issue:
    line: int
    severity: str
    message: str


@dataclass
class Surface:
    label: str
    area: Fraction
    material: str
    line: int


@dataclass
class Room:
    name: str
    size: tuple  # (length, width, height) in metres
    surfaces: list
    treats: dict  # label -> (material, line)
    materials: dict  # name -> six coefficients, 125-4000 Hz
    directivity: Fraction = Fraction(1)

    @property
    def volume(self):
        length, width, height = self.size
        return length * width * height

    @property
    def area(self):
        return sum((surface.area for surface in self.surfaces), Fraction(0))


def _unknown_material(name, materials):
    hint = difflib.get_close_matches(name, list(materials), n=1)
    suffix = "; did you mean '%s'?" % hint[0] if hint else ""
    return "unknown material '%s'%s" % (name, suffix)


def parse_room(text):
    """Return (room, issues). room is None whenever an error was found."""
    issues = []
    numbered = []
    for number, raw in enumerate(text.splitlines(), start=1):
        words = raw.split("#", 1)[0].split()
        if words:
            numbered.append((number, words))

    unit = Fraction(1)
    unit_line = None
    for number, words in numbered:
        if words[0] != "units":
            continue
        if unit_line is not None:
            issues.append(Issue(number, "error", "units already declared on line %d" % unit_line))
            continue
        unit_line = number
        if len(words) == 2 and words[1] in UNIT_FACTORS:
            unit = UNIT_FACTORS[words[1]]
        else:
            issues.append(Issue(number, "error", "units must be 'm' or 'ft'"))

    name = "Untitled room"
    size = None
    size_line = None
    directivity = Fraction(1)
    materials = dict(MATERIALS)
    surfaces = []
    labels = {}
    treats = {}

    for number, words in numbered:
        keyword, args = words[0], words[1:]
        if keyword not in DIRECTIVES:
            issues.append(Issue(number, "error", "unknown directive '%s'" % keyword))
            continue
        try:
            if keyword == "units":
                continue
            if keyword == "room":
                if not args:
                    issues.append(Issue(number, "error", "room needs a name"))
                else:
                    name = " ".join(args)
            elif keyword == "size":
                if len(args) != 3:
                    issues.append(Issue(number, "error", "size needs three lengths: size L W H"))
                elif size is not None:
                    issues.append(Issue(number, "error", "size already declared on line %d" % size_line))
                else:
                    dims = tuple(Fraction(value) * unit for value in args)
                    if any(dim <= 0 for dim in dims):
                        issues.append(Issue(number, "error", "room sizes must be greater than zero"))
                    else:
                        size, size_line = dims, number
            elif keyword == "directivity":
                if len(args) != 1 or Fraction(args[0]) <= 0:
                    issues.append(Issue(number, "error", "directivity needs one positive number"))
                else:
                    directivity = Fraction(args[0])
            elif keyword == "custom":
                if len(args) != 7:
                    issues.append(Issue(number, "error", "custom needs a name and six coefficients"))
                elif args[0] in materials:
                    issues.append(Issue(number, "error", "material '%s' is already defined" % args[0]))
                else:
                    coeffs = tuple(Fraction(value) for value in args[1:])
                    if any(c < 0 or c > 1 for c in coeffs):
                        issues.append(Issue(number, "error", "absorption coefficients must be between 0 and 1"))
                    else:
                        materials[args[0]] = coeffs
            elif keyword == "surface":
                if len(args) != 3:
                    issues.append(Issue(number, "error", "surface needs LABEL AREA MATERIAL"))
                else:
                    label, area_text, material = args
                    area = Fraction(area_text) * unit * unit
                    if area <= 0:
                        issues.append(Issue(number, "error", "surface area must be greater than zero"))
                    elif label in labels:
                        issues.append(Issue(number, "error", "surface '%s' already defined on line %d" % (label, labels[label])))
                    else:
                        labels[label] = number
                        surfaces.append(Surface(label, area, material, number))
            elif keyword == "treat":
                if len(args) != 2:
                    issues.append(Issue(number, "error", "treat needs LABEL MATERIAL"))
                elif args[0] in treats:
                    issues.append(Issue(number, "error", "surface '%s' is already treated" % args[0]))
                else:
                    treats[args[0]] = (args[1], number)
        except (ValueError, ZeroDivisionError):
            issues.append(Issue(number, "error", "expected a number in '%s' line" % keyword))

    if size is None:
        issues.append(Issue(1, "error", "no 'size L W H' line"))
    if not surfaces:
        issues.append(Issue(1, "error", "no 'surface' lines"))
    for surface in surfaces:
        if surface.material not in materials:
            issues.append(Issue(surface.line, "error", _unknown_material(surface.material, materials)))
    for label, (material, number) in treats.items():
        if label not in labels:
            issues.append(Issue(number, "error", "treat refers to unknown surface '%s'" % label))
        if material not in materials:
            issues.append(Issue(number, "error", _unknown_material(material, materials)))
    if size is not None and surfaces:
        length, width, height = size
        shell = 2 * (length * width + length * height + width * height)
        covered = sum((surface.area for surface in surfaces), Fraction(0))
        if covered > shell * SHELL_TOLERANCE:
            issues.append(Issue(
                size_line, "warning",
                "surfaces total %.2f m2 but the room shell is %.2f m2" % (float(covered), float(shell)),
            ))

    issues.sort(key=lambda issue: issue.line)
    if any(issue.severity == "error" for issue in issues):
        return None, issues
    room = Room(name, size, surfaces, treats, materials, directivity)
    return room, issues


def band_absorption(room, treated=False):
    """Total absorption (sabins) per octave band, exact fractions."""
    totals = [Fraction(0)] * len(BANDS)
    for surface in room.surfaces:
        material = surface.material
        if treated and surface.label in room.treats:
            material = room.treats[surface.label][0]
        for index, coefficient in enumerate(room.materials[material]):
            totals[index] += surface.area * coefficient
    return totals


def _mid(values):
    picks = [values[index] for index in MID_INDEXES]
    if any(value is None for value in picks):
        return None
    return sum(picks) / len(picks)


def analyse(room, treated=False):
    """Per-band results for the room as described, or with treat lines applied."""
    absorption = band_absorption(room, treated)
    volume = room.volume
    area = room.area
    sabine, eyring, mean_alpha, critical = [], [], [], []
    for total in absorption:
        alpha = total / area
        mean_alpha.append(float(alpha))
        if total > 0:
            sabine.append(float(SABINE_K * volume / total))
            critical.append(CRITICAL_K * math.sqrt(float(room.directivity * total)))
        else:
            sabine.append(None)
            critical.append(None)
        if total > 0 and alpha < 1:
            eyring.append(float(SABINE_K * volume) / (-float(area) * math.log(1 - float(alpha))))
        else:
            eyring.append(None)
    return {
        "volume": float(volume),
        "area": float(area),
        "absorption": [float(total) for total in absorption],
        "mean_alpha": mean_alpha,
        "sabine": sabine,
        "eyring": eyring,
        "critical": critical,
        "mid_sabine": _mid(sabine),
        "mid_eyring": _mid(eyring),
    }


def mid_rt60(data):
    """The figure the gate uses: Eyring when it exists, otherwise Sabine."""
    value = data["mid_eyring"] if data["mid_eyring"] is not None else data["mid_sabine"]
    return float("inf") if value is None else value


def treatment_needed(room, target, material=None):
    """Extra absorption per band to reach a Sabine RT60 target.

    Returns (band, deficit_sabins, area_m2). The area assumes the named material
    is laid over the concrete row of the material table.
    """
    wanted = SABINE_K * room.volume / target
    have = band_absorption(room)
    base = room.materials[BASE_MATERIAL]
    rows = []
    for index, band in enumerate(BANDS):
        deficit = wanted - have[index]
        if deficit <= 0:
            rows.append((band, Fraction(0), None))
            continue
        area = None
        if material is not None:
            gain = room.materials[material][index] - base[index]
            if gain > 0:
                area = deficit / gain
        rows.append((band, deficit, area))
    return rows


def _cell(value, spec):
    return "--" if value is None else format(value, spec)


def _row(label, values, spec):
    return "%-20s" % label + "".join("%9s" % _cell(value, spec) for value in values)


def render_report(room, treated=False):
    data = analyse(room, treated)
    lines = ["%-20s" % "band (Hz)" + "".join("%9d" % band for band in BANDS)]
    lines.append(_row("absorption (m2)", data["absorption"], ".2f"))
    lines.append(_row("mean alpha", data["mean_alpha"], ".3f"))
    lines.append(_row("RT60 Sabine (s)", data["sabine"], ".2f"))
    lines.append(_row("RT60 Eyring (s)", data["eyring"], ".2f"))
    lines.append(_row("critical dist (m)", data["critical"], ".2f"))
    lines.append("mid RT60 (500/1000 Hz): Sabine %s s, Eyring %s s" % (
        _cell(data["mid_sabine"], ".2f"), _cell(data["mid_eyring"], ".2f"),
    ))
    if max(data["mean_alpha"]) > EYRING_THRESHOLD:
        lines.append("note: mean absorption above %.2f, so Sabine runs long; trust Eyring" % EYRING_THRESHOLD)
    return lines


def _scenarios(room):
    scenarios = [("as_described", False)]
    if room.treats:
        scenarios.append(("treated", True))
    return scenarios


def room_header(room, treated=False):
    label = "treated" if treated else "as described"
    return "%s (%s) | volume %.2f m3 | surface %.2f m2 | directivity %g" % (
        room.name, label, float(room.volume), float(room.area), float(room.directivity),
    )


def write_csv(path, room):
    fields = ["scenario", "band_hz", "absorption_m2", "rt60_sabine_s", "rt60_eyring_s", "mean_alpha", "critical_distance_m"]

    def cell(value, digits=4):
        return "" if value is None else round(value, digits)

    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(fields)
        for scenario, treated in _scenarios(room):
            data = analyse(room, treated)
            for index, band in enumerate(BANDS):
                writer.writerow([
                    scenario, band,
                    cell(data["absorption"][index]),
                    cell(data["sabine"][index]),
                    cell(data["eyring"][index]),
                    cell(data["mean_alpha"][index]),
                    cell(data["critical"][index]),
                ])


def _read(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError as error:
        print("ringdown: cannot read %s: %s" % (path, error.strerror), file=sys.stderr)
        return None


def _print_issues(path, issues):
    for issue in issues:
        print("%s:%d: %s: %s" % (path, issue.line, issue.severity, issue.message))


def cmd_materials():
    print("%-16s" % "material" + "".join("%7d" % band for band in BANDS))
    for name, coeffs in MATERIALS.items():
        print("%-16s" % name + "".join("%7s" % format(float(c), ".3f") for c in coeffs))
    print("Typical illustrative values (125-4000 Hz); use a `custom` line for real product data.")
    return 0


def cmd_check(path, issues):
    _print_issues(path, issues)
    errors = sum(1 for issue in issues if issue.severity == "error")
    warnings = len(issues) - errors
    if errors:
        print("%d error(s), %d warning(s)" % (errors, warnings))
        return 1
    print("ok: %s has no errors (%d warning(s))" % (path, warnings))
    return 0


def cmd_report(path, room, issues, args):
    _print_issues(path, issues)
    for _, treated in _scenarios(room):
        if treated:
            print()
        print(room_header(room, treated))
        print("\n".join(render_report(room, treated)))
    if args.csv:
        write_csv(args.csv, room)
        print("wrote %s" % args.csv)
    if args.gate_mid is not None:
        value = mid_rt60(analyse(room, treated=bool(room.treats)))
        if value > args.gate_mid:
            print("GATE FAILED: mid RT60 %.2f s is above the %.2f s limit" % (value, args.gate_mid))
            return 2
        print("GATE passed: mid RT60 %.2f s is within the %.2f s limit" % (value, args.gate_mid))
    return 0


def cmd_target(path, room, issues, args):
    _print_issues(path, issues)
    try:
        target = Fraction(args.rt60)
    except (ValueError, ZeroDivisionError):
        print("ringdown: --rt60 must be a number of seconds", file=sys.stderr)
        return 1
    if target <= 0:
        print("ringdown: --rt60 must be greater than zero", file=sys.stderr)
        return 1
    if args.material is not None and args.material not in room.materials:
        print("ringdown: %s" % _unknown_material(args.material, room.materials), file=sys.stderr)
        return 1
    print("Target Sabine RT60 %.2f s for %s (volume %.2f m3), untreated surfaces" % (
        float(target), room.name, float(room.volume),
    ))
    print("%-10s %-22s %s" % ("band (Hz)", "absorption to add", "area of %s" % args.material if args.material else "area"))
    for band, deficit, area in treatment_needed(room, target, args.material):
        if deficit == 0:
            print("%-10d %-22s %s" % (band, "none (meets target)", "-"))
            continue
        if args.material is None:
            area_text = "give --material to size"
        elif area is None:
            area_text = "no gain over %s at this band" % BASE_MATERIAL
        else:
            area_text = "%.2f m2" % float(area)
        print("%-10d %-22s %s" % (band, "%.2f sabins" % float(deficit), area_text))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="ringdown",
        description="Offline room acoustics planner: RT60, critical distance and treatment sizing from plain-text room files.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="lint a room file with line-numbered findings")
    check.add_argument("room")

    report = sub.add_parser("report", help="octave-band RT60, critical distance and treatment scenarios")
    report.add_argument("room")
    report.add_argument("--csv", metavar="OUT", help="also write every band's results to a CSV file")
    report.add_argument("--gate-mid", type=float, metavar="SECONDS",
                        help="exit 2 if the final mid-band RT60 is above this limit")

    target = sub.add_parser("target", help="absorption needed to reach a Sabine RT60 target")
    target.add_argument("room")
    target.add_argument("--rt60", required=True, metavar="SECONDS")
    target.add_argument("--material", help="absorber to size, from the material table or a custom line")

    sub.add_parser("materials", help="list the built-in absorption coefficients")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command == "materials":
        return cmd_materials()
    text = _read(args.room)
    if text is None:
        return 1
    room, issues = parse_room(text)
    if args.command == "check":
        return cmd_check(args.room, issues)
    if room is None:
        _print_issues(args.room, issues)
        print("ringdown: %s has errors; run 'ringdown check' for details" % args.room, file=sys.stderr)
        return 1
    if args.command == "report":
        return cmd_report(args.room, room, issues, args)
    return cmd_target(args.room, room, issues, args)


if __name__ == "__main__":
    sys.exit(main())

# Ringdown

**Offline room acoustics planner: plain-text room files in, octave-band RT60, critical distance and treatment sizing out.**

Ringdown answers the questions a small studio, classroom or home theatre actually asks: how long does this room ring, how much will the planned carpet and ceiling tiles help, and how many square metres of absorber does a target need? It runs on the Python 3 standard library alone, reads one readable `.room` file, and makes no network calls.

## Features

- Reads a plain-text room file with `room`, `units`, `size`, `directivity`, `surface`, `custom` and `treat` lines.
- Accepts metres or feet, converted exactly with fractions, so volumes and absorption sums carry no rounding drift.
- Uses a built-in octave-band absorption table (125 to 4000 Hz) for eight common materials.
- Defines your own materials with `custom` lines (six coefficients, checked to lie between 0 and 1).
- Computes Sabine and Eyring RT60 for every band, with a note when mean absorption is high enough that Sabine runs long.
- Reports total absorption, mean absorption coefficient and critical distance per band, with a directivity factor.
- Compares the room as described with a treated scenario from `treat` lines, so you can see a plan's effect before buying anything.
- Solves for a target Sabine RT60: extra sabins needed per band, plus the square metres of a named absorber needed to close the gap.
- Lints the file with `file:line: severity: message` findings, including "did you mean" suggestions for misspelt materials.
- Warns when the listed surfaces cover more than the room's shell, without blocking the analysis.
- Exports every band's results for every scenario to CSV.
- Gates CI with exit codes: `1` for errors in the file, `2` when the mid-band RT60 is above your limit.

## Quick start

The repository ships `examples/studio.room`, a 6.0 x 4.0 x 2.8 m control room with a planned carpet floor and acoustic-tile ceiling.

```bash
python3 ringdown.py check examples/studio.room
python3 ringdown.py report examples/studio.room
python3 ringdown.py target examples/studio.room --rt60 0.5 --material acoustic_tile
```

`check` lints the file and exits `0` when it is clean. `report` prints a band table for the room as described, then a second table for the treated scenario. `target` lists the absorption each band is short of for an RT60 of 0.5 s, and the area of acoustic tile that would cover it.

A room file looks like this:

```text
room Studio A
units m
size 6.0 4.0 2.8
surface north_wall 16.8 painted_brick
surface floor 24.0 concrete
treat floor carpet
```

## Commands

| Command | What it does |
| --- | --- |
| `python3 ringdown.py check FILE` | Lint the room file; exit `1` if there are errors |
| `python3 ringdown.py report FILE [--csv OUT] [--gate-mid SECONDS]` | Band table, mid-band RT60 and treated scenario; optional CSV and gate |
| `python3 ringdown.py target FILE --rt60 SECONDS [--material NAME]` | Absorption and absorber area needed to reach a Sabine RT60 target |
| `python3 ringdown.py materials` | List the built-in absorption coefficients |

## Room file reference

| Line | Meaning |
| --- | --- |
| `room NAME` | Name shown in the report (optional) |
| `units m` or `units ft` | Unit for every length and area in the file; declare once |
| `size L W H` | Room dimensions |
| `directivity Q` | Source directivity factor used for critical distance (default `1`) |
| `surface LABEL AREA MATERIAL` | One surface, its area and its material |
| `custom NAME a125 a250 a500 a1k a2k a4k` | A material with your own coefficients |
| `treat LABEL MATERIAL` | Replace a surface's material in the treated scenario |
| `# ...` | Comment, to the end of the line |

## How it calculates

- Sabine: `RT60 = 0.161 V / A`
- Eyring: `RT60 = 0.161 V / (-S ln(1 - alpha))`, where `alpha = A / S`
- Critical distance: `Dc = 0.141 sqrt(Q A)`

Here `V` is volume in m³, `A` is total absorption in m² sabins, and `S` is total surface area in m². The gate uses the mid-band (500 and 1000 Hz average) Eyring figure when it exists, and falls back to Sabine otherwise.

The material coefficients are typical illustrative values, not manufacturer data. For a real design, replace them with a `custom` line that uses the product's data sheet.

## Running it

Requires Python 3.8 or later. There is nothing to install.

```bash
python3 ringdown.py --help
python3 ringdown.py report examples/studio.room --gate-mid 0.8
```

## Testing

The tests use the standard library `unittest` module, so they run without installing anything. Pytest also collects them.

```bash
python3 -m unittest discover -s tests -v
```

The suite checks the Sabine and Eyring results against hand calculations, the critical distance formula, exact feet-to-metre conversion, line-numbered parse errors, the target solver, the treated scenario, the gate and CSV exit paths, and that the shipped example lints clean.

## Limits

Ringdown models the room as a box with absorbing surfaces. It ignores air absorption, which matters above about 2 kHz in large rooms, and it does not model people, seats or diffusers as separate objects. Treat the output as a planning estimate and check the final design with measurement.

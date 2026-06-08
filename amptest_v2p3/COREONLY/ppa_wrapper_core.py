#!/usr/bin/env python3
"""Cadence/Spectre wrapper and PPA analyzer for the neural amplifier project."""

import argparse
import cmath
import csv
import json
import math
import re
import struct
import subprocess
import zlib
from pathlib import Path


UNIT_SCALE = {
    "": 1.0,
    "f": 1e-15,
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "m": 1e-3,
    "k": 1e3,
    "meg": 1e6,
    "g": 1e9,
    "t": 1e12,
}


def load_config(path):
    with path.open() as f:
        cfg = json.load(f)
    cfg["_config_dir"] = str(path.resolve().parent)
    return cfg


def resolve_path(cfg, value):
    if not value:
        return None
    p = Path(value).expanduser()
    if p.is_absolute():
        return p
    return (Path(cfg["_config_dir"]) / p).resolve()


def parse_number(value, default=0.0):
    if value is None:
        return default
    text = str(value).strip()
    if not text:
        return default
    try:
        return float(text)
    except ValueError:
        pass
    m = re.fullmatch(r"([-+]?\d+(?:\.\d*)?(?:[eE][-+]?\d+)?)([a-zA-Z]+)?", text)
    if not m:
        raise ValueError(f"Cannot parse numeric value: {value!r}")
    number = float(m.group(1))
    suffix = (m.group(2) or "").lower()
    if suffix not in UNIT_SCALE:
        raise ValueError(f"Unknown unit suffix in {value!r}")
    return number * UNIT_SCALE[suffix]


def db20(x):
    return 20.0 * math.log10(max(abs(x), 1e-300))


def target_response(freq_hz, spec):
    gain = float(spec["midband_gain_vv"])
    fl = float(spec["low_cut_hz"])
    fh = float(spec["high_cut_hz"])
    order = int(spec.get("rolloff_order_each_side", 4))
    f = max(freq_hz, 1e-300)
    hp_x = (f / fl) ** order
    lp_x = (f / fh) ** order
    hp_mag = hp_x / math.sqrt(1.0 + hp_x * hp_x)
    lp_mag = 1.0 / math.sqrt(1.0 + lp_x * lp_x)
    return complex(gain * hp_mag * lp_mag, 0.0)


def write_spectre_netlists(cfg):
    work_dir = resolve_path(cfg, cfg.get("work_dir")) or Path.cwd() / "ppa_eval_run"
    work_dir.mkdir(parents=True, exist_ok=True)

    dut_netlist = resolve_path(cfg, cfg["dut_netlist"])
    spec = cfg["spec"]
    sim = cfg["sim"]
    pins = cfg.get("dut_pins_order", ["VIN", "VREF", "VDD", "0", "VOUT"])
    inst_nodes = " ".join("0" if p.upper() == "GND" else p for p in pins)
    dut = cfg["dut_subckt"]
    vdd = float(spec["vdd"])
    vref = vdd * float(spec.get("vref_ratio", 0.5))
    vindc = vdd * float(spec.get("vindc_ratio", 0.5))
    ac = sim["ac"]
    tran = sim["tran"]
    tran_in = tran.get("input", {})

    ac_netlist = work_dir / "tb_ac.scs"
    tran_static_netlist = work_dir / "tb_tran_static.scs"
    tran_netlist = work_dir / "tb_tran.scs"
    ocean_script = work_dir / "export.ocn"

    include_lines = []
    for inc in cfg.get("include_files", []):
        include_lines.append(f'include "{resolve_path(cfg, inc)}"')
    for lib in cfg.get("library_sections", []):
        lib_path = resolve_path(cfg, lib["path"])
        section = lib.get("section")
        if section:
            include_lines.append(f'include "{lib_path}" section={section}')
        else:
            include_lines.append(f'include "{lib_path}"')
    for ahdl in cfg.get("ahdl_include_files", []):
        include_lines.append(f'ahdl_include "{resolve_path(cfg, ahdl)}"')

    common = [
        "simulator lang=spectre",
        *include_lines,
        f'include "{dut_netlist}"',
        f"VDD (VDD 0) vsource dc={vdd}",
        f"VREF (VREF 0) vsource dc={vref}",
        f"XDUT ({inst_nodes}) {dut}",
        f"CLOAD (VOUT 0) capacitor c={float(spec['load_cap_f'])}",
        "save VIN VOUT VDD:p",
    ]
    ac_lines = common[:]
    ac_lines.insert(4, f"VIN (VIN 0) vsource dc={vindc} mag={float(spec['input_ac_amplitude'])}")
    ac_lines.append(
        f"ac ac start={float(ac['start_hz'])} stop={float(ac['stop_hz'])} "
        f"dec={int(ac['points_per_dec'])}"
    )
    ac_lines.append("simulatorOptions options rawfmt=psfxl")
    ac_netlist.write_text("\n".join(ac_lines) + "\n")

    if tran_in.get("kind", "sine") == "pwl":
        pwl_file = resolve_path(cfg, tran_in["file"])
        vin_src = f'VIN (VIN 0) vsource type=pwl file="{pwl_file}"'
    else:
        ampl = float(tran_in.get("amplitude_v", spec["input_ac_amplitude"]))
        freq = float(tran_in.get("frequency_hz", 1000.0))
        vin_src = f"VIN (VIN 0) vsource type=sine dc={vindc} ampl={ampl} freq={freq}"
    tran_lines = common[:]
    tran_lines.insert(4, vin_src)
    tran_lines.append(
        f"tran tran stop={float(tran['stop_s'])} maxstep={float(tran['maxstep_s'])} "
        f"strobeperiod={float(tran.get('strobe_s', tran['maxstep_s']))}"
    )
    tran_lines.append("simulatorOptions options rawfmt=psfxl")
    tran_netlist.write_text("\n".join(tran_lines) + "\n")

    static_lines = common[:]
    static_lines.insert(4, f"VIN (VIN 0) vsource dc={vindc}")
    static_lines.append(
        f"tran tran stop={float(tran['stop_s'])} maxstep={float(tran['maxstep_s'])} "
        f"strobeperiod={float(tran.get('strobe_s', tran['maxstep_s']))}"
    )
    static_lines.append("simulatorOptions options rawfmt=psfxl")
    tran_static_netlist.write_text("\n".join(static_lines) + "\n")

    ocean_script.write_text(
        "\n".join(
            [
                f'openResults("{work_dir / "ac_psf"}")',
                "selectResult('ac)",
                f'ocnPrint(?output "{work_dir / "ac.csv"}" ?numberNotation \'scientific ?precision 12 v("VIN") v("VOUT"))',
                f'openResults("{work_dir / "tran_static_psf"}")',
                "selectResult('tran)",
                f'ocnPrint(?output "{work_dir / "tran_static.csv"}" ?numberNotation \'scientific ?precision 12 v("VIN") v("VOUT") getData("VDD:p"))',
                f'openResults("{work_dir / "tran_psf"}")',
                "selectResult('tran)",
                f'ocnPrint(?output "{work_dir / "tran.csv"}" ?numberNotation \'scientific ?precision 12 v("VIN") v("VOUT") getData("VDD:p"))',
                "exit()",
            ]
        )
        + "\n"
    )
    return ac_netlist, tran_static_netlist, tran_netlist, ocean_script


def run_simulation(cfg):
    work_dir = resolve_path(cfg, cfg.get("work_dir")) or Path.cwd() / "ppa_eval_run"
    ac_netlist, tran_static_netlist, tran_netlist, ocean_script = write_spectre_netlists(cfg)
    sim = cfg["sim"]
    if sim.get("run_spectre", True):
        spectre = sim.get("spectre_cmd", "spectre")
        spectre_args = sim.get("spectre_args", [])
        subprocess.run(
            [spectre, str(ac_netlist), "+escchars", "+log", str(work_dir / "spectre_ac.log"), "-format", "psfxl", "-raw", str(work_dir / "ac_psf")] + spectre_args,
            cwd=str(work_dir),
            check=True,
        )
        subprocess.run(
            [spectre, str(tran_static_netlist), "+escchars", "+log", str(work_dir / "spectre_tran_static.log"), "-format", "psfxl", "-raw", str(work_dir / "tran_static_psf")] + spectre_args,
            cwd=str(work_dir),
            check=True,
        )
        subprocess.run(
            [spectre, str(tran_netlist), "+escchars", "+log", str(work_dir / "spectre_tran.log"), "-format", "psfxl", "-raw", str(work_dir / "tran_psf")] + spectre_args,
            cwd=str(work_dir),
            check=True,
        )
    if sim.get("run_ocean_export", True):
        subprocess.run([sim.get("ocean_cmd", "ocean"), "-nograph", "-restore", str(ocean_script)], cwd=str(work_dir), check=True)


def clean_header(name):
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


def read_numeric_csv(path):
    with path.open(newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        lines = [line for line in f if line.strip() and not line.lstrip().startswith(("#", ";"))]
    if not lines:
        return []
    first = lines[0]
    has_header = any(ch.isalpha() for ch in first)
    if has_header and "," not in first:
        rows = []
        for line in lines:
            stripped = line.strip()
            if not stripped or not re.match(r"[-+]?\d", stripped):
                continue
            vals = [parse_number(x, float("nan")) for x in re.split(r"\s+", stripped) if x]
            rows.append({f"c{i}": v for i, v in enumerate(vals)})
        return rows
    if has_header:
        reader = csv.DictReader(lines)
        rows = []
        for row in reader:
            rows.append({clean_header(k): parse_number(v, float("nan")) for k, v in row.items() if k is not None})
        return rows
    rows = []
    for line in lines:
        vals = [parse_number(x, float("nan")) for x in re.split(r"[,\s]+", line.strip()) if x]
        rows.append({f"c{i}": v for i, v in enumerate(vals)})
    return rows


def write_png(path, width, height, pixels):
    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    raw = bytearray()
    for row in pixels:
        raw.append(0)
        for r, g, b in row:
            raw.extend([r, g, b])
    data = b"\x89PNG\r\n\x1a\n"
    data += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    data += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    data += chunk(b"IEND", b"")
    path.write_bytes(data)


def new_canvas(width=1000, height=620, color=(255, 255, 255)):
    return [[color for _ in range(width)] for _ in range(height)]


def draw_pixel(img, x, y, color):
    h = len(img)
    w = len(img[0])
    if 0 <= x < w and 0 <= y < h:
        img[y][x] = color


def draw_line(img, x0, y0, x1, y1, color):
    x0, y0, x1, y1 = int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1))
    dx = abs(x1 - x0)
    sx = 1 if x0 < x1 else -1
    dy = -abs(y1 - y0)
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    while True:
        draw_pixel(img, x0, y0, color)
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def draw_rect(img, x0, y0, x1, y1, color):
    draw_line(img, x0, y0, x1, y0, color)
    draw_line(img, x1, y0, x1, y1, color)
    draw_line(img, x1, y1, x0, y1, color)
    draw_line(img, x0, y1, x0, y0, color)


def fill_rect(img, x0, y0, x1, y1, color):
    x0, y0, x1, y1 = [int(round(v)) for v in (x0, y0, x1, y1)]
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            draw_pixel(img, x, y, color)


def draw_polyline(img, points, color):
    for i in range(1, len(points)):
        draw_line(img, points[i - 1][0], points[i - 1][1], points[i][0], points[i][1], color)


FONT_5X7 = {
    " ": ["00000", "00000", "00000", "00000", "00000", "00000", "00000"],
    "-": ["00000", "00000", "00000", "11110", "00000", "00000", "00000"],
    "+": ["00000", "00100", "00100", "11111", "00100", "00100", "00000"],
    ".": ["00000", "00000", "00000", "00000", "00000", "01100", "01100"],
    ":": ["00000", "01100", "01100", "00000", "01100", "01100", "00000"],
    "/": ["00001", "00010", "00100", "01000", "10000", "00000", "00000"],
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    "3": ["11110", "00001", "00001", "01110", "00001", "00001", "11110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "10000", "11110", "00001", "00001", "11110"],
    "6": ["01110", "10000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00001", "01110"],
    "A": ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
    "B": ["11110", "10001", "10001", "11110", "10001", "10001", "11110"],
    "C": ["01110", "10001", "10000", "10000", "10000", "10001", "01110"],
    "D": ["11110", "10001", "10001", "10001", "10001", "10001", "11110"],
    "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
    "F": ["11111", "10000", "10000", "11110", "10000", "10000", "10000"],
    "G": ["01110", "10001", "10000", "10111", "10001", "10001", "01110"],
    "H": ["10001", "10001", "10001", "11111", "10001", "10001", "10001"],
    "I": ["01110", "00100", "00100", "00100", "00100", "00100", "01110"],
    "L": ["10000", "10000", "10000", "10000", "10000", "10000", "11111"],
    "M": ["10001", "11011", "10101", "10101", "10001", "10001", "10001"],
    "N": ["10001", "11001", "10101", "10011", "10001", "10001", "10001"],
    "O": ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
    "P": ["11110", "10001", "10001", "11110", "10000", "10000", "10000"],
    "R": ["11110", "10001", "10001", "11110", "10100", "10010", "10001"],
    "S": ["01111", "10000", "10000", "01110", "00001", "00001", "11110"],
    "T": ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
    "U": ["10001", "10001", "10001", "10001", "10001", "10001", "01110"],
    "V": ["10001", "10001", "10001", "10001", "10001", "01010", "00100"],
    "W": ["10001", "10001", "10001", "10101", "10101", "10101", "01010"],
    "Y": ["10001", "10001", "01010", "00100", "00100", "00100", "00100"],
    "Z": ["11111", "00001", "00010", "00100", "01000", "10000", "11111"],
}


def draw_text(img, x, y, text, color=(30, 30, 30), scale=2):
    x0 = int(round(x))
    y0 = int(round(y))
    for ch in str(text).upper():
        glyph = FONT_5X7.get(ch, FONT_5X7[" "])
        for row_i, row in enumerate(glyph):
            for col_i, bit in enumerate(row):
                if bit == "1":
                    for yy in range(scale):
                        for xx in range(scale):
                            draw_pixel(img, x0 + col_i * scale + xx, y0 + row_i * scale + yy, color)
        x0 += 6 * scale


def text_width(text, scale=2):
    return len(str(text)) * 6 * scale


def format_axis_value(value):
    if abs(value) >= 10000 or (abs(value) > 0 and abs(value) < 0.01):
        return f"{value:.1e}"
    if abs(value) >= 100:
        return f"{value:.0f}"
    if abs(value) >= 10:
        return f"{value:.1f}"
    return f"{value:.3g}"


def draw_axes(img, box, x_ticks, y_ticks, x_map, y_map, x_label, y_label):
    left, top, right, bottom = box
    axis = (40, 40, 40)
    grid = (225, 225, 225)
    draw_rect(img, left, top, right, bottom, axis)
    for value, label in x_ticks:
        x = x_map(value)
        draw_line(img, x, top, x, bottom, grid)
        draw_line(img, x, bottom, x, bottom + 5, axis)
        draw_text(img, x - text_width(label, 1) / 2, bottom + 10, label, axis, scale=1)
    for value, label in y_ticks:
        y = y_map(value)
        draw_line(img, left, y, right, y, grid)
        draw_line(img, left - 5, y, left, y, axis)
        draw_text(img, left - 10 - text_width(label, 1), y - 4, label, axis, scale=1)
    draw_text(img, (left + right) / 2 - text_width(x_label, 1) / 2, bottom + 30, x_label, axis, scale=1)
    draw_text(img, left, top - 22, y_label, axis, scale=1)


def draw_report_legend(img, box, lines):
    if not lines:
        return
    lines = [str(line) for line in lines if line]
    if not lines:
        return
    width = max(text_width(line, 1) for line in lines) + 18
    height = len(lines) * 14 + 16
    x1 = box[2] - 10
    y1 = box[3] - 10
    x0 = x1 - width
    y0 = y1 - height
    fill_rect(img, x0, y0, x1, y1, (255, 255, 255))
    draw_rect(img, x0, y0, x1, y1, (80, 80, 80))
    for i, line in enumerate(lines):
        draw_text(img, x0 + 9, y0 + 9 + i * 14, line, (35, 35, 35), scale=1)


def ppa_report_legend_lines(result):
    ap = result["area_power"]
    ac = result.get("ac", {})
    tr = result.get("tran", {})
    lines = [
        "PPA REPORT",
        f"AREA {format_axis_value(ap.get('area_total_p') or 0.0)} P",
        f"STATIC {format_axis_value(ap.get('power_static_w') or 0.0)} W",
        f"DYNAMIC {format_axis_value(ap.get('power_dynamic_w') or 0.0)} W",
        f"TOTAL {format_axis_value(ap.get('power_total_w') or 0.0)} W",
    ]
    if ac.get("ac_nrmse_db") is not None:
        lines.append(f"AC NRMSE {format_axis_value(ac.get('ac_nrmse_db'))}")
    if tr.get("tran_ac_nrmse_vs_target_filter") is not None:
        lines.append(f"TRAN NRMSE {format_axis_value(tr.get('tran_ac_nrmse_vs_target_filter'))}")
    if result.get("performance_nrmse_combined") is not None:
        lines.append(f"COMB NRMSE {format_axis_value(result.get('performance_nrmse_combined'))}")
    return lines


def scale_points(xs, ys, x_min, x_max, y_min, y_max, box):
    left, top, right, bottom = box
    out = []
    for x, y in zip(xs, ys):
        if not (math.isfinite(x) and math.isfinite(y)):
            continue
        px = left + (x - x_min) * (right - left) / max(x_max - x_min, 1e-300)
        py = bottom - (y - y_min) * (bottom - top) / max(y_max - y_min, 1e-300)
        out.append((px, py))
    return out


def plot_ac_png(path, ac_data, spec, report_lines=None):
    if not ac_data:
        return
    width, height = 1000, 620
    img = new_canvas(width, height)
    box = (115, 55, 960, 535)
    freqs = [f for f, _ in ac_data if f > 0]
    actual = [db20(abs(h)) for f, h in ac_data if f > 0]
    target = [db20(abs(target_response(f, spec))) for f in freqs]
    err2 = [(a - b) ** 2 for a, b in zip(actual, target)]
    rmse = math.sqrt(sum(err2) / len(err2)) if err2 else None
    norm = max(target) - min(target) if target else 0.0
    similarity = None if rmse is None else 1.0 / (1.0 + rmse / max(norm, 1e-12))
    xs = [math.log10(f) for f in freqs]
    y_min = math.floor((min(actual + target) - 5.0) / 10.0) * 10.0
    y_max = math.ceil((max(actual + target) + 5.0) / 10.0) * 10.0
    x_min = min(xs)
    x_max = max(xs)
    x_map = lambda value: box[0] + (value - x_min) * (box[2] - box[0]) / max(x_max - x_min, 1e-300)
    y_map = lambda value: box[3] - (value - y_min) * (box[3] - box[1]) / max(y_max - y_min, 1e-300)
    x_tick_exps = list(range(int(math.ceil(x_min)), int(math.floor(x_max)) + 1))
    x_ticks = [(float(e), f"1E{e}") for e in x_tick_exps]
    y_step = max(10.0, math.ceil((y_max - y_min) / 8.0 / 10.0) * 10.0)
    y_tick_values = []
    yv = math.ceil(y_min / y_step) * y_step
    while yv <= y_max + 1e-9:
        y_tick_values.append(yv)
        yv += y_step
    y_ticks = [(v, format_axis_value(v)) for v in y_tick_values]
    draw_axes(img, box, x_ticks, y_ticks, x_map, y_map, "FREQ HZ", "GAIN DB")
    draw_polyline(img, scale_points(xs, target, min(xs), max(xs), y_min, y_max, box), (220, 80, 50))
    draw_polyline(img, scale_points(xs, actual, min(xs), max(xs), y_min, y_max, box), (30, 90, 200))
    draw_text(img, box[0] + 10, box[1] + 10, "BLUE ACTUAL", (30, 90, 200), scale=1)
    draw_text(img, box[0] + 10, box[1] + 26, "RED TARGET", (220, 80, 50), scale=1)
    if similarity is not None:
        draw_text(img, box[0] + 10, box[1] + 42, f"SIM {similarity:.3f}", (40, 40, 40), scale=1)
    draw_report_legend(img, box, report_lines)
    write_png(path, width, height, img)


def plot_tran_png(path, t, vin, vout, report_lines=None):
    if len(t) < 2:
        return
    width, height = 1000, 620
    img = new_canvas(width, height)
    box = (115, 55, 960, 535)
    if len(t) > 4000:
        step = int(math.ceil(len(t) / 4000.0))
        t = t[::step]
        vin = vin[::step]
        vout = vout[::step]
    vin_dc = sum(vin) / len(vin)
    vout_dc = sum(vout) / len(vout)
    vin = [x - vin_dc for x in vin]
    vout = [x - vout_dc for x in vout]
    y_min = min(vin + vout)
    y_max = max(vin + vout)
    pad = max((y_max - y_min) * 0.08, 1e-6)
    y_min -= pad
    y_max += pad
    x_min = min(t)
    x_max = max(t)
    x_map = lambda value: box[0] + (value - x_min) * (box[2] - box[0]) / max(x_max - x_min, 1e-300)
    y_map = lambda value: box[3] - (value - y_min) * (box[3] - box[1]) / max(y_max - y_min, 1e-300)
    x_ticks = []
    for i in range(6):
        value = x_min + i * (x_max - x_min) / 5.0
        x_ticks.append((value, format_axis_value(value)))
    y_ticks = []
    for i in range(7):
        value = y_min + i * (y_max - y_min) / 6.0
        y_ticks.append((value, format_axis_value(value)))
    draw_axes(img, box, x_ticks, y_ticks, x_map, y_map, "TIME S", "AC VOLT V")
    draw_polyline(img, scale_points(t, vin, min(t), max(t), y_min, y_max, box), (80, 150, 80))
    draw_polyline(img, scale_points(t, vout, min(t), max(t), y_min, y_max, box), (30, 90, 200))
    draw_text(img, box[0] + 10, box[1] + 10, "GREEN VIN AC", (80, 150, 80), scale=1)
    draw_text(img, box[0] + 10, box[1] + 26, "BLUE VOUT AC", (30, 90, 200), scale=1)
    draw_report_legend(img, box, report_lines)
    write_png(path, width, height, img)


def plot_power_png(path, area_power, report_lines=None):
    values = [
        ("STATIC", area_power.get("power_static_w", area_power.get("power_dc_w")), (80, 150, 80)),
        ("DYNAMIC", area_power.get("power_dynamic_w"), (30, 90, 200)),
        ("TOTAL", area_power.get("power_total_w"), (170, 70, 170)),
    ]
    values = [(name, value, color) for name, value, color in values if value is not None and math.isfinite(value)]
    if not values:
        return
    width, height = 1000, 620
    img = new_canvas(width, height)
    box = (115, 55, 960, 535)
    max_value = max(value for _, value, _ in values)
    y_max = max_value * 1.15 if max_value > 0.0 else 1.0
    x_map = lambda value: box[0] + value * (box[2] - box[0]) / max(len(values), 1)
    y_map = lambda value: box[3] - value * (box[3] - box[1]) / max(y_max, 1e-300)
    x_ticks = [(i + 0.5, name) for i, (name, _, _) in enumerate(values)]
    y_ticks = []
    for i in range(6):
        value = y_max * i / 5.0
        y_ticks.append((value, format_axis_value(value)))
    draw_axes(img, box, x_ticks, y_ticks, x_map, y_map, "POWER PART", "POWER W")
    for i, (name, value, color) in enumerate(values):
        left = x_map(i + 0.18)
        right = x_map(i + 0.82)
        top = y_map(value)
        fill_rect(img, left, top, right, box[3] - 1, color)
        draw_rect(img, left, top, right, box[3], (40, 40, 40))
        draw_text(img, left, max(top - 18, box[1] + 4), format_axis_value(value), (40, 40, 40), scale=1)
        draw_text(img, box[0] + 10, box[1] + 10 + i * 16, f"{name}: {format_axis_value(value)} W", color, scale=1)
    measured = area_power.get("power_measured_total_w")
    if measured is not None:
        draw_text(img, box[0] + 10, box[1] + 64, f"MEASURED: {format_axis_value(measured)} W", (40, 40, 40), scale=1)
    draw_report_legend(img, box, report_lines)
    write_png(path, width, height, img)


def find_col(row, candidates):
    keys = set(row.keys())
    for c in candidates:
        if c in keys:
            return c
    return None


def load_ac(path):
    rows = read_numeric_csv(path)
    if not rows:
        return []
    row0 = rows[0]
    fcol = find_col(row0, ["freq_hz", "frequency_hz", "freq", "frequency", "hz", "c0"])
    magdb_col = find_col(row0, ["mag_db", "gain_db", "vout_db", "db"])
    if magdb_col:
        return [(r[fcol], 10 ** (r[magdb_col] / 20.0)) for r in rows if r.get(fcol, 0) > 0]
    vr = find_col(row0, ["vout_real", "out_real", "real_vout", "c3"])
    vi = find_col(row0, ["vout_imag", "out_imag", "imag_vout", "c4"])
    ir = find_col(row0, ["vin_real", "in_real", "real_vin", "c1"])
    ii = find_col(row0, ["vin_imag", "in_imag", "imag_vin", "c2"])
    if vr and vi and ir and ii:
        out = []
        for r in rows:
            vin = complex(r[ir], r[ii])
            vout = complex(r[vr], r[vi])
            out.append((r[fcol], vout / vin if abs(vin) > 0 else vout / 0.001))
        return [(f, h) for f, h in out if f > 0]
    vout_col = find_col(row0, ["vout_v", "vout", "out", "c2"])
    if not vout_col:
        keys = list(row0.keys())
        if fcol in keys and len(keys) >= 3:
            in_col = keys[1]
            out_col = keys[2]
            return [(r[fcol], r[out_col] / max(abs(r[in_col]), 1e-300)) for r in rows if r.get(fcol, 0) > 0]
        raise ValueError(f"Cannot identify AC columns in {path}")
    return [(r[fcol], r[vout_col] / 0.001) for r in rows if r.get(fcol, 0) > 0]


def load_tran(path):
    rows = read_numeric_csv(path)
    if not rows:
        return [], [], [], None
    row0 = rows[0]
    tcol = find_col(row0, ["time_s", "time", "sec", "seconds", "c0"])
    vin_col = find_col(row0, ["vin_v", "vin", "v_in", "c1"])
    vout_col = find_col(row0, ["vout_v", "vout", "v_out", "c2"])
    idd_col = find_col(row0, ["idd_a", "idd", "i_vdd", "ivdd", "c3"])
    if not (tcol and vin_col and vout_col):
        keys = list(row0.keys())
        if len(keys) >= 3:
            tcol = keys[0]
            vin_col = keys[1]
            vout_col = keys[2]
            idd_col = keys[3] if len(keys) >= 4 else None
        else:
            raise ValueError(f"Cannot identify transient columns in {path}")
    t = [r[tcol] for r in rows]
    vin = [r[vin_col] for r in rows]
    vout = [r[vout_col] for r in rows]
    idd = [r[idd_col] for r in rows] if idd_col else None
    return t, vin, vout, idd


def interp_uniform(t, y, skip_s):
    pairs = [(ti, yi) for ti, yi in zip(t, y) if ti >= skip_s and math.isfinite(ti) and math.isfinite(yi)]
    if len(pairs) < 4:
        return [], []
    t0, t1 = pairs[0][0], pairs[-1][0]
    n = 1
    while n * 2 <= len(pairs):
        n *= 2
    dt = (t1 - t0) / (n - 1)
    out_t = [t0 + i * dt for i in range(n)]
    out_y = []
    j = 0
    for ti in out_t:
        while j + 1 < len(pairs) and pairs[j + 1][0] < ti:
            j += 1
        if j + 1 == len(pairs):
            out_y.append(pairs[j][1])
        else:
            a_t, a_y = pairs[j]
            b_t, b_y = pairs[j + 1]
            frac = 0.0 if b_t == a_t else (ti - a_t) / (b_t - a_t)
            out_y.append(a_y + frac * (b_y - a_y))
    return out_t, out_y


def fft(values, inverse=False):
    n = len(values)
    if n == 1:
        return values[:]
    even = fft(values[0::2], inverse)
    odd = fft(values[1::2], inverse)
    sign = 1 if inverse else -1
    out = [0j] * n
    for k in range(n // 2):
        tw = cmath.exp(sign * 2j * math.pi * k / n) * odd[k]
        out[k] = even[k] + tw
        out[k + n // 2] = even[k] - tw
    if inverse:
        return out
    return out


def ifft(values):
    raw = fft(values, inverse=True)
    return [x / len(values) for x in raw]


def strip_netlist_comment(line):
    for marker in ("//", ";"):
        if marker in line:
            line = line.split(marker, 1)[0]
    return line.strip()


def read_netlist_logical_lines(path):
    lines = []
    if not path or not path.exists():
        return lines
    current = ""
    with path.open(errors="ignore") as f:
        for raw in f:
            line = strip_netlist_comment(raw)
            if not line or line.startswith("*"):
                continue
            if line.startswith("+"):
                current += " " + line[1:].strip()
                continue
            has_backslash_cont = line.endswith("\\")
            if has_backslash_cont:
                line = line[:-1].strip()
            if current:
                current += " " + line
            else:
                current = line
            if not has_backslash_cont:
                lines.append(current)
                current = ""
    if current:
        lines.append(current)
    return lines


def parse_instance_vector(inst):
    text = inst.strip()
    m = re.match(r"^(.*?)(?:<|\[)(\d+)\s*:\s*(\d+)(?:>|\])$", text)
    if not m:
        return text, 1.0
    base = m.group(1)
    left = int(m.group(2))
    right = int(m.group(3))
    return base, float(abs(left - right) + 1)


def normalize_instance_name(name):
    base, _ = parse_instance_vector(str(name).strip())
    return base.lower()


def collect_netlist_multipliers(cfg):
    paths = []
    dut = resolve_path(cfg, cfg.get("dut_netlist"))
    if dut:
        paths.append(dut)
    for inc in cfg.get("include_files", []):
        inc_path = resolve_path(cfg, inc)
        if inc_path:
            paths.append(inc_path)

    multipliers = {}
    for path in paths:
        for line in read_netlist_logical_lines(path):
            tokens = line.split()
            if not tokens:
                continue
            inst = tokens[0].strip()
            if not inst or inst.lower() in ("subckt", "ends", "include", "ahdl_include", "simulator"):
                continue
            inst_base, vector_width = parse_instance_vector(inst)
            m = None
            for match in re.finditer(r"(?i)(?:^|\s)(m|mult|multi|multiplier)\s*=\s*([^\s]+)", line):
                try:
                    m = parse_number(match.group(2), 1.0)
                except ValueError:
                    m = 1.0
            total_m = vector_width * (m if m is not None else 1.0)
            multipliers[inst.lower()] = total_m
            multipliers[inst_base.lower()] = total_m
    return multipliers


def collect_netlist_resistor_areas(cfg):
    paths = []
    dut = resolve_path(cfg, cfg.get("dut_netlist"))
    if dut:
        paths.append(dut)
    for inc in cfg.get("include_files", []):
        inc_path = resolve_path(cfg, inc)
        if inc_path:
            paths.append(inc_path)

    rows = []
    for path in paths:
        for line in read_netlist_logical_lines(path):
            tokens = line.split()
            if not tokens:
                continue
            inst = tokens[0].strip()
            if not inst or not inst[0].lower() == "r":
                continue
            params = {}
            for match in re.finditer(r"(?i)(?:^|\s)([a-z_][a-z0-9_]*)\s*=\s*([^\s]+)", line):
                params[match.group(1).lower()] = match.group(2)
            length = parse_number(params.get("l"), 0.0)
            width = parse_number(params.get("w"), 0.0)
            mult = parse_number(params.get("m") or params.get("mult") or params.get("multi") or params.get("multiplier"), 1.0)
            if length <= 0.0 or width <= 0.0:
                continue
            base_name, vector_width = parse_instance_vector(inst)
            effective_count = vector_width * mult
            area_each = length * width / 1e-12
            rows.append({
                "name": base_name,
                "type": "resistor",
                "count": vector_width,
                "netlist_m": mult,
                "effective_count": effective_count,
                "area_p_each": area_each,
                "area_p_total": effective_count * area_each,
                "included": True,
                "area_source": "netlist_lw",
            })
    return rows


def instance_model_and_params(line):
    tokens = line.split()
    if len(tokens) < 2:
        return None, {}
    model_i = None
    for i in range(1, len(tokens)):
        if "=" in tokens[i]:
            model_i = i - 1
            break
    if model_i is None:
        model_i = len(tokens) - 1
    if model_i <= 0:
        return None, {}
    params = {}
    for tok in tokens[model_i + 1:]:
        if "=" not in tok:
            continue
        key, value = tok.split("=", 1)
        params[key.lower()] = value
    return tokens[model_i], params


def parse_area_param_p(value, default=0.0):
    if value is None:
        return default
    text = str(value).strip()
    if not text:
        return default
    if re.fullmatch(r"[-+]?\d+(?:\.\d*)?(?:[eE][-+]?\d+)?", text):
        return float(text)
    return parse_number(text, default * 1e-12) / 1e-12


def estimate_primitive_area_parts(inst, model, params):
    inst_l = inst.lower()
    model_l = (model or "").lower()
    mult = parse_number(params.get("m") or params.get("mult") or params.get("multi") or params.get("multiplier"), 1.0)
    nf = parse_number(params.get("nf") or params.get("fingers"), 1.0)
    dtype = None
    area_each = 0.0
    effective_mult = mult
    if inst_l.startswith("m") or "fet" in model_l or "nfet" in model_l or "pfet" in model_l:
        w = parse_number(params.get("w"), 0.0)
        l = parse_number(params.get("l"), 0.0)
        dtype = "mos"
        effective_mult = mult * nf
        area_each = w * l / 1e-12 if w > 0.0 and l > 0.0 else 0.0
    elif inst_l.startswith("r") or "res" in model_l:
        w = parse_number(params.get("w"), 0.0)
        l = parse_number(params.get("l"), 0.0)
        dtype = "resistor"
        area_each = w * l / 1e-12 if w > 0.0 and l > 0.0 else 0.0
    elif inst_l.startswith("c") or "cap" in model_l:
        w = parse_number(params.get("w"), 0.0)
        l = parse_number(params.get("l"), 0.0)
        dtype = "capacitor"
        if w > 0.0 and l > 0.0:
            area_each = w * l / 1e-12
        else:
            cval = parse_number(params.get("c"), 0.0)
            area_each = cval / 1e-15 if cval > 0.0 else 0.0
    elif inst_l.startswith("q") or "npn" in model_l or "pnp" in model_l or "bjt" in model_l:
        dtype = "bjt"
        area_each = parse_area_param_p(params.get("area"), 1.0)
    elif inst_l.startswith("d") or "diode" in model_l:
        w = parse_number(params.get("w"), 0.0)
        l = parse_number(params.get("l"), 0.0)
        dtype = "diode"
        area_each = w * l / 1e-12 if w > 0.0 and l > 0.0 else parse_area_param_p(params.get("area"), 1.0)
    if not dtype or area_each <= 0.0:
        return None
    return dtype, area_each, effective_mult


def collect_netlist_primitive_areas(cfg):
    paths = []
    dut = resolve_path(cfg, cfg.get("dut_netlist"))
    if dut:
        paths.append(dut)
    for inc in cfg.get("include_files", []):
        inc_path = resolve_path(cfg, inc)
        if inc_path and inc_path.suffix.lower() != ".va":
            paths.append(inc_path)

    target_subckt = str(cfg.get("dut_subckt", "")).lower()
    rows = []
    for path in paths:
        current_subckt = None
        for line in read_netlist_logical_lines(path):
            tokens = line.split()
            if not tokens:
                continue
            head = tokens[0].lower()
            if head == "subckt" and len(tokens) >= 2:
                current_subckt = tokens[1].lower()
                continue
            if head == "ends":
                current_subckt = None
                continue
            if head in ("include", "ahdl_include", "simulator"):
                continue
            if target_subckt and current_subckt and current_subckt != target_subckt:
                continue
            if target_subckt and current_subckt is None:
                continue
            inst = tokens[0].strip()
            if not inst or inst[0].lower() == "x":
                continue
            model, params = instance_model_and_params(line)
            parts = estimate_primitive_area_parts(inst, model, params)
            if not parts:
                continue
            dtype, area_each, mult = parts
            base_name, vector_width = parse_instance_vector(inst)
            effective_count = vector_width * mult
            rows.append({
                "name": base_name,
                "type": dtype,
                "count": vector_width,
                "netlist_m": mult,
                "effective_count": effective_count,
                "area_p_each": area_each,
                "area_p_total": effective_count * area_each,
                "included": True,
                "area_source": "netlist_primitive",
            })
    return rows


def average_abs_current_power(t, idd, vdd, start_s=None, stop_s=None):
    pairs = []
    for ti, ii in zip(t, idd):
        if start_s is not None and ti < start_s:
            continue
        if stop_s is not None and ti > stop_s:
            continue
        if math.isfinite(ti) and math.isfinite(ii):
            pairs.append((ti, abs(ii)))
    if len(pairs) < 2:
        return None
    integ = 0.0
    for i in range(1, len(pairs)):
        integ += 0.5 * (pairs[i][1] + pairs[i - 1][1]) * (pairs[i][0] - pairs[i - 1][0])
    duration = pairs[-1][0] - pairs[0][0]
    if duration <= 0.0:
        return None
    return vdd * integ / duration


def analyze_idd_power(t, idd, cfg):
    if not idd or len(t) < 2:
        return {}
    spec = cfg["spec"]
    sim_tran = cfg["sim"]["tran"]
    power_cfg = cfg.get("power", {})
    vdd = float(spec["vdd"])

    full_power = average_abs_current_power(t, idd, vdd)
    steady_start = float(power_cfg.get("steady_start_s", sim_tran.get("settle_skip_s", 0.0)))
    steady_power = average_abs_current_power(t, idd, vdd, steady_start, None)

    freq = float(power_cfg.get("period_frequency_hz", 0.0))
    if freq <= 0.0:
        freq = float(sim_tran.get("fft_fundamental_hz", 0.0))
    if freq <= 0.0:
        freq = float(sim_tran.get("input", {}).get("frequency_hz", 0.0))

    period_power = None
    period_start = None
    period_stop = None
    cycles = float(power_cfg.get("period_cycles", 1.0))
    if freq > 0.0 and cycles > 0.0:
        window = cycles / freq
        period_stop = float(power_cfg.get("period_stop_s", t[-1]))
        period_start = power_cfg.get("period_start_s")
        if period_start is None:
            period_start = max(steady_start, period_stop - window)
        else:
            period_start = float(period_start)
            period_stop = period_start + window
        if period_stop > period_start:
            period_power = average_abs_current_power(t, idd, vdd, period_start, period_stop)

    vals = [x for x in (full_power, steady_power, period_power) if x is not None]
    return {
        "power_dynamic_w": full_power,
        "power_steady_w": steady_power,
        "power_period_w": period_power,
        "power_period_frequency_hz": freq if freq > 0.0 else None,
        "power_period_cycles": cycles if period_power is not None else None,
        "power_period_start_s": period_start if period_power is not None else None,
        "power_period_stop_s": period_stop if period_power is not None else None,
        "power_measured_total_w": max(vals) if vals else None,
    }


def analyze_area_power(devices_csv, cfg, idd_data=None, static_idd_data=None):
    spec = cfg["spec"]
    vdd = float(spec["vdd"])
    total_area_p = 0.0
    static_current_a = 0.0
    rows_out = []
    netlist_area_rows = collect_netlist_primitive_areas(cfg)
    netlist_multipliers = collect_netlist_multipliers(cfg)
    area_cfg = cfg.get("area", {})
    resistor_source = str(area_cfg.get("resistor_source", "devices_csv")).strip().lower()
    if devices_csv and devices_csv.exists():
        with devices_csv.open(newline="") as f:
            for row in csv.DictReader(f):
                include = str(row.get("include_in_ppa", "true")).strip().lower() not in ("0", "false", "no")
                name = row.get("name", "")
                count = parse_number(row.get("count"), 1)
                netlist_m = netlist_multipliers.get(normalize_instance_name(name), 1.0)
                effective_count = count * netlist_m
                dtype = clean_header(row.get("type", ""))
                if dtype == "resistor" and resistor_source == "netlist":
                    continue
                area = parse_number(row.get("area_p"), 0.0)
                if not area:
                    w = parse_number(row.get("width"), 0.0)
                    l = parse_number(row.get("length"), 0.0)
                    mult = parse_number(row.get("multiplier"), 1.0)
                    if dtype == "opamp":
                        area = 1000.0
                    elif dtype == "capacitor":
                        area = w * l * mult / 1e-12
                    elif dtype == "resistor":
                        area = parse_number(row.get("seg_length"), 0.0) * parse_number(row.get("seg_width"), 0.0) * parse_number(row.get("segments"), 1.0) / 1e-12
                    elif dtype == "diode":
                        area = w * l * mult / 1e-12
                    elif dtype == "npn":
                        area = 1.0 * mult
                    elif dtype == "pnp":
                        area = 0.4624 * mult
                ft = parse_number(row.get("ft_hz"), 0.0)
                if dtype == "opamp" and ft:
                    static_current_a += effective_count * ft * 7e-12
                if include and not netlist_area_rows:
                    total_area_p += effective_count * area
                if not netlist_area_rows:
                    rows_out.append({
                        "name": name,
                        "type": dtype,
                        "count": count,
                        "netlist_m": netlist_m,
                        "effective_count": effective_count,
                        "area_p_each": area,
                        "area_p_total": effective_count * area if include else 0.0,
                        "included": include,
                        "area_source": "devices_csv",
                    })
    if netlist_area_rows:
        rows_out = netlist_area_rows
        total_area_p = sum(row["area_p_total"] for row in rows_out)
    elif resistor_source == "netlist":
        for row in collect_netlist_resistor_areas(cfg):
            total_area_p += row["area_p_total"]
            rows_out.append(row)
    p_dc = vdd * static_current_a
    measured_power = analyze_idd_power(idd_data[0], idd_data[1], cfg) if idd_data else {}
    static_power_data = analyze_idd_power(static_idd_data[0], static_idd_data[1], cfg) if static_idd_data else {}
    measured_total = measured_power.get("power_measured_total_w")
    measured_dynamic = measured_power.get("power_dynamic_w")
    measured_static = static_power_data.get("power_steady_w") or static_power_data.get("power_measured_total_w")
    p_static = measured_static if measured_static is not None else p_dc
    if measured_total is not None:
        power_total = measured_total
    elif measured_dynamic is not None:
        power_total = measured_dynamic
    else:
        power_total = p_static
    p_dyn_raw = power_total - p_static
    p_dyn = max(p_dyn_raw, 0.0)
    return {
        "area_total_p": total_area_p,
        "static_current_a": static_current_a,
        "power_static_w": p_static,
        "power_dc_w": p_static,
        "power_static_estimate_w": p_dc,
        "power_static_measured_w": measured_static,
        "power_static_source": "static_tran_csv" if measured_static is not None else "devices_csv_estimate",
        **measured_power,
        "power_dynamic_measured_w": measured_dynamic,
        "power_dynamic_raw_w": p_dyn_raw,
        "power_dynamic_w": p_dyn,
        "power_total_w": power_total,
        "power_score_basis_w": power_total,
        "area_source": "netlist_primitive" if netlist_area_rows else "devices_csv",
        "device_rows": rows_out,
    }


def analyze_ac(ac_data, spec):
    if not ac_data:
        return {}
    freqs = [x[0] for x in ac_data]
    gains = [abs(x[1]) for x in ac_data]
    actual_db = [db20(g) for g in gains]
    target_db = [db20(abs(target_response(f, spec))) for f in freqs]
    err2 = [(a - b) ** 2 for a, b in zip(actual_db, target_db)]
    rmse_db = math.sqrt(sum(err2) / len(err2))
    norm = max(target_db) - min(target_db)
    nrmse = rmse_db / max(norm, 1e-12)
    passband = [(f, g) for f, g in zip(freqs, gains) if spec["low_cut_hz"] * 2 <= f <= spec["high_cut_hz"] / 2]
    mid_gain = sum(g for _, g in passband) / len(passband) if passband else max(gains)
    mid_db = db20(mid_gain)
    target_3db = mid_db - 3.0
    lower = find_crossing(freqs, actual_db, target_3db, below_to_above=True)
    upper = find_crossing(freqs, actual_db, target_3db, below_to_above=False)
    low_probe = interp_log(freqs, actual_db, float(spec["attenuation_probe_low_hz"]))
    high_probe = interp_log(freqs, actual_db, float(spec["attenuation_probe_high_hz"]))
    ripple = 0.0
    if passband:
        pass_db = [db20(g) for _, g in passband]
        ripple = max(pass_db) - min(pass_db)
    return {
        "ac_nrmse_db": nrmse,
        "ac_rmse_db": rmse_db,
        "midband_gain_vv": mid_gain,
        "midband_gain_db": mid_db,
        "lower_3db_hz": lower,
        "upper_3db_hz": upper,
        "passband_ripple_db": ripple,
        "attenuation_low_probe_db_rel_mid": None if low_probe is None else low_probe - mid_db,
        "attenuation_high_probe_db_rel_mid": None if high_probe is None else high_probe - mid_db,
    }


def find_crossing(freqs, dbs, level, below_to_above=True):
    for i in range(1, len(freqs)):
        a, b = dbs[i - 1], dbs[i]
        if below_to_above and a <= level <= b:
            return log_interp_cross(freqs[i - 1], freqs[i], a, b, level)
        if not below_to_above and a >= level >= b:
            return log_interp_cross(freqs[i - 1], freqs[i], a, b, level)
    return None


def log_interp_cross(f0, f1, y0, y1, target):
    if y1 == y0:
        return f0
    x0, x1 = math.log10(f0), math.log10(f1)
    return 10 ** (x0 + (target - y0) * (x1 - x0) / (y1 - y0))


def interp_log(freqs, vals, f):
    if f < freqs[0] or f > freqs[-1]:
        return None
    for i in range(1, len(freqs)):
        if freqs[i - 1] <= f <= freqs[i]:
            return log_interp_y(freqs[i - 1], freqs[i], vals[i - 1], vals[i], f)
    return None


def log_interp_y(f0, f1, y0, y1, f):
    if f1 == f0:
        return y0
    return y0 + (math.log10(f) - math.log10(f0)) * (y1 - y0) / (math.log10(f1) - math.log10(f0))


def analyze_tran(t, vin, vout, spec, sim_tran):
    if len(t) < 4:
        return {}
    skip = float(sim_tran.get("settle_skip_s", 0.0))
    tu, inu = interp_uniform(t, vin, skip)
    _, outu = interp_uniform(t, vout, skip)
    if len(tu) < 8:
        return {}
    n = len(tu)
    dt = (tu[-1] - tu[0]) / (n - 1)
    vin_dc_removed = sum(inu) / n
    vout_dc_removed = sum(outu) / n
    in_ac = [x - vin_dc_removed for x in inu]
    out_ac = [x - vout_dc_removed for x in outu]
    spectrum_in = fft([complex(x, 0.0) for x in in_ac])
    target_spec = []
    for k, val in enumerate(spectrum_in):
        freq = k / (n * dt) if k <= n // 2 else -(n - k) / (n * dt)
        target_spec.append(val * target_response(abs(freq), spec))
    ideal = [x.real for x in ifft(target_spec)]
    rmse = math.sqrt(sum((a - b) ** 2 for a, b in zip(out_ac, ideal)) / n)
    denom = math.sqrt(sum(x * x for x in ideal) / n)
    nrmse = rmse / max(denom, 1e-300)
    fft_out = fft([complex(x, 0.0) for x in out_ac])
    fund_hz = float(sim_tran.get("fft_fundamental_hz", 0.0))
    if fund_hz <= 0:
        fund_bin = max(range(1, n // 2), key=lambda k: abs(fft_out[k]))
        fund_hz = fund_bin / (n * dt)
    else:
        fund_bin = max(1, min(n // 2 - 1, round(fund_hz * n * dt)))
    fund_mag = abs(fft_out[fund_bin])
    harm2 = 0.0
    for h in range(2, 6):
        k = fund_bin * h
        if k < n // 2:
            harm2 += abs(fft_out[k]) ** 2
    thd = math.sqrt(harm2) / max(fund_mag, 1e-300)
    return {
        "tran_nrmse_vs_target_filter": nrmse,
        "tran_ac_nrmse_vs_target_filter": nrmse,
        "tran_rmse_v": rmse,
        "tran_ac_rmse_v": rmse,
        "vin_dc_removed_v": vin_dc_removed,
        "vout_dc_removed_v": vout_dc_removed,
        "vout_mean_v": sum(outu) / n,
        "vout_peak_to_peak_v": max(out_ac) - min(out_ac),
        "vout_ac_peak_to_peak_v": max(out_ac) - min(out_ac),
        "fft_fundamental_hz": fund_hz,
        "thd_ratio_h2_to_h5": thd,
        "thd_db": db20(thd),
    }


def analyze(cfg):
    inputs = cfg.get("input_files", {})
    ac_csv = resolve_path(cfg, inputs.get("ac_csv"))
    tran_csv = resolve_path(cfg, inputs.get("tran_csv"))
    static_tran_csv = resolve_path(cfg, inputs.get("static_tran_csv"))
    devices_csv = resolve_path(cfg, inputs.get("devices_csv"))

    ac_data = load_ac(ac_csv) if ac_csv and ac_csv.exists() else []
    t, vin, vout, idd = load_tran(tran_csv) if tran_csv and tran_csv.exists() else ([], [], [], None)
    idd_data = (t, idd) if idd else None
    ts, _, _, idd_static = load_tran(static_tran_csv) if static_tran_csv and static_tran_csv.exists() else ([], [], [], None)
    static_idd_data = (ts, idd_static) if idd_static else None

    result = {
        "design_name": cfg.get("design_name", "design"),
        "area_power": analyze_area_power(devices_csv, cfg, idd_data, static_idd_data),
        "ac": analyze_ac(ac_data, cfg["spec"]),
        "tran": analyze_tran(t, vin, vout, cfg["spec"], cfg["sim"]["tran"]),
    }
    ac_n = result["ac"].get("ac_nrmse_db")
    tr_n = result["tran"].get("tran_nrmse_vs_target_filter")
    vals = [x for x in (ac_n, tr_n) if x is not None]
    result["performance_nrmse_combined"] = sum(vals) / len(vals) if vals else None
    out_path = (resolve_path(cfg, cfg.get("work_dir")) or Path.cwd()) / "ppa_metrics.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    write_ppa_summary(out_path.parent / "ppa_summary.log", result)
    write_report(out_path.parent / "ppa_report.log", result)
    report_lines = ppa_report_legend_lines(result)
    plot_ac_png(out_path.parent / "ac_response.png", ac_data, cfg["spec"], report_lines)
    plot_tran_png(out_path.parent / "transient_response.png", t, vin, vout, report_lines)
    plot_power_png(out_path.parent / "power_breakdown.png", result["area_power"], report_lines)
    return result


def fmt_value(value, unit=""):
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.6g}{unit}"
    return f"{value}{unit}"


def write_ppa_summary(path, result):
    ap = result["area_power"]
    lines = [
        f"design: {result['design_name']}",
        f"area_total_p: {fmt_value(ap.get('area_total_p'))}",
        f"power_static_w: {fmt_value(ap.get('power_static_w'))}",
        f"power_static_source: {fmt_value(ap.get('power_static_source'))}",
        f"power_static_measured_w: {fmt_value(ap.get('power_static_measured_w'))}",
        f"power_static_estimate_w: {fmt_value(ap.get('power_static_estimate_w'))}",
        f"power_total_w: {fmt_value(ap.get('power_total_w'))}",
        f"power_dynamic_raw_w: {fmt_value(ap.get('power_dynamic_raw_w'))}",
        f"power_dynamic_w: {fmt_value(ap.get('power_dynamic_w'))}",
        f"power_steady_w: {fmt_value(ap.get('power_steady_w'))}",
        f"power_period_w: {fmt_value(ap.get('power_period_w'))}",
        f"power_score_basis_w: {fmt_value(ap.get('power_score_basis_w'))}",
        f"performance_nrmse_combined: {fmt_value(result.get('performance_nrmse_combined'))}",
    ]
    path.write_text("\n".join(lines) + "\n")


def write_report(path, result):
    ap = result["area_power"]
    ac = result["ac"]
    tr = result["tran"]
    lines = [
        f"design: {result['design_name']}",
        "",
        "[Area / Power]",
        f"area_total_p: {fmt_value(ap.get('area_total_p'))}",
        f"area_source: {fmt_value(ap.get('area_source'))}",
        f"static_current_a: {fmt_value(ap.get('static_current_a'), ' A')}",
        f"power_static_w: {fmt_value(ap.get('power_static_w'), ' W')}",
        f"power_static_source: {fmt_value(ap.get('power_static_source'))}",
        f"power_static_measured_w: {fmt_value(ap.get('power_static_measured_w'), ' W')}",
        f"power_static_estimate_w: {fmt_value(ap.get('power_static_estimate_w'), ' W')}",
        f"power_dc_w: {fmt_value(ap.get('power_dc_w'), ' W')}",
        f"power_dynamic_raw_w: {fmt_value(ap.get('power_dynamic_raw_w'), ' W')}",
        f"power_dynamic_w: {fmt_value(ap.get('power_dynamic_w'), ' W')}",
        f"power_dynamic_measured_w: {fmt_value(ap.get('power_dynamic_measured_w'), ' W')}",
        f"power_steady_w: {fmt_value(ap.get('power_steady_w'), ' W')}",
        f"power_period_w: {fmt_value(ap.get('power_period_w'), ' W')}",
        f"power_period_frequency_hz: {fmt_value(ap.get('power_period_frequency_hz'), ' Hz')}",
        f"power_period_cycles: {fmt_value(ap.get('power_period_cycles'))}",
        f"power_period_start_s: {fmt_value(ap.get('power_period_start_s'), ' s')}",
        f"power_period_stop_s: {fmt_value(ap.get('power_period_stop_s'), ' s')}",
        f"power_total_w: {fmt_value(ap.get('power_total_w'), ' W')}",
        f"power_score_basis_w: {fmt_value(ap.get('power_score_basis_w'), ' W')}",
        "",
        "[Area Breakdown]",
    ]
    for row in ap.get("device_rows", []):
        lines.append(
            "{name}: type={type}, source={source}, count={count}, netlist_m={netlist_m}, area_each_p={area_each}, area_total_p={area_total}".format(
                name=row.get("name"),
                type=row.get("type"),
                source=row.get("area_source", "unknown"),
                count=fmt_value(row.get("count")),
                netlist_m=fmt_value(row.get("netlist_m")),
                area_each=fmt_value(row.get("area_p_each")),
                area_total=fmt_value(row.get("area_p_total")),
            )
        )
    lines += [
        "",
        "[AC Performance]",
        f"ac_nrmse_db: {fmt_value(ac.get('ac_nrmse_db'))}",
        f"ac_rmse_db: {fmt_value(ac.get('ac_rmse_db'), ' dB')}",
        f"midband_gain_vv: {fmt_value(ac.get('midband_gain_vv'), ' V/V')}",
        f"midband_gain_db: {fmt_value(ac.get('midband_gain_db'), ' dB')}",
        f"lower_3db_hz: {fmt_value(ac.get('lower_3db_hz'), ' Hz')}",
        f"upper_3db_hz: {fmt_value(ac.get('upper_3db_hz'), ' Hz')}",
        f"passband_ripple_db: {fmt_value(ac.get('passband_ripple_db'), ' dB')}",
        f"attenuation_low_probe_db_rel_mid: {fmt_value(ac.get('attenuation_low_probe_db_rel_mid'), ' dB')}",
        f"attenuation_high_probe_db_rel_mid: {fmt_value(ac.get('attenuation_high_probe_db_rel_mid'), ' dB')}",
        "",
        "[Transient AC / FFT Performance]",
        f"tran_ac_nrmse_vs_target_filter: {fmt_value(tr.get('tran_ac_nrmse_vs_target_filter'))}",
        f"tran_ac_rmse_v: {fmt_value(tr.get('tran_ac_rmse_v'), ' V')}",
        f"vin_dc_removed_v: {fmt_value(tr.get('vin_dc_removed_v'), ' V')}",
        f"vout_dc_removed_v: {fmt_value(tr.get('vout_dc_removed_v'), ' V')}",
        f"vout_ac_peak_to_peak_v: {fmt_value(tr.get('vout_ac_peak_to_peak_v'), ' V')}",
        f"fft_fundamental_hz: {fmt_value(tr.get('fft_fundamental_hz'), ' Hz')}",
        f"thd_ratio_h2_to_h5: {fmt_value(tr.get('thd_ratio_h2_to_h5'))}",
        f"thd_db: {fmt_value(tr.get('thd_db'), ' dB')}",
        "",
        f"performance_nrmse_combined: {fmt_value(result.get('performance_nrmse_combined'))}",
    ]
    path.write_text("\n".join(lines) + "\n")


def print_summary(result):
    ap = result["area_power"]
    ac = result["ac"]
    tr = result["tran"]
    print(f"design: {result['design_name']}")
    print(f"area_total_p: {ap['area_total_p']:.6g}")
    print(f"static_current_a: {ap['static_current_a']:.6g}")
    print(f"power_static_w: {ap['power_static_w']:.6g}")
    print(f"power_dc_w: {ap['power_dc_w']:.6g}")
    print(f"power_dynamic_w: {ap['power_dynamic_w']}")
    print(f"power_dynamic_measured_w: {ap.get('power_dynamic_measured_w')}")
    print(f"power_steady_w: {ap.get('power_steady_w')}")
    print(f"power_period_w: {ap.get('power_period_w')}")
    print(f"power_total_w: {ap.get('power_total_w'):.6g}")
    print(f"power_score_basis_w: {ap['power_score_basis_w']:.6g}")
    if ac:
        print(f"ac_nrmse_db: {ac['ac_nrmse_db']:.6g}")
        print(f"midband_gain_db: {ac['midband_gain_db']:.3f}")
        print(f"lower_3db_hz: {ac['lower_3db_hz']}")
        print(f"upper_3db_hz: {ac['upper_3db_hz']}")
        print(f"atten_low_rel_mid_db: {ac['attenuation_low_probe_db_rel_mid']}")
        print(f"atten_high_rel_mid_db: {ac['attenuation_high_probe_db_rel_mid']}")
    if tr:
        print(f"tran_nrmse: {tr['tran_nrmse_vs_target_filter']:.6g}")
        print(f"vout_peak_to_peak_v: {tr['vout_peak_to_peak_v']:.6g}")
        print(f"thd_db: {tr['thd_db']:.3f}")
    print(f"performance_nrmse_combined: {result['performance_nrmse_combined']}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["gen-netlists", "run", "analyze", "all"])
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.command == "gen-netlists":
        paths = write_spectre_netlists(cfg)
        for p in paths:
            print(p)
    elif args.command == "run":
        run_simulation(cfg)
    elif args.command == "analyze":
        print_summary(analyze(cfg))
    elif args.command == "all":
        run_simulation(cfg)
        print_summary(analyze(cfg))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Converter-attached TOP test, with PPA measured for CORE only."""

import argparse
import csv
import importlib.util
import json
import math
import re
import subprocess
from pathlib import Path


HERE = Path(__file__).resolve().parent
COREONLY = HERE.parent / "COREONLY"
CORE_LIB = COREONLY / "ppa_wrapper_core.py"


def load_core_lib():
    spec = importlib.util.spec_from_file_location("core_ppa", str(CORE_LIB))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


core = load_core_lib()


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


def clean_key(name):
    return core.clean_header(str(name))


def read_rows(path):
    return core.read_numeric_csv(path)


def pick(row, names, default=None):
    for name in names:
        key = clean_key(name)
        if key in row and math.isfinite(row[key]):
            return row[key]
    return default


def has_any(row, names):
    for name in names:
        if clean_key(name) in row:
            return True
    return False


def load_tran(path):
    rows = read_rows(path)
    t, vin, vout = [], [], []
    vdd_raw, vdd_core, idd_core = [], [], []
    vac_p, vac_n, vac_diff = [], [], []
    for row in rows:
        ti = pick(row, ["time_s", "time", "t", "c0"])
        vi = pick(row, ["vin_v", "vin", "c1"])
        vo = pick(row, ["vout_v", "vout", "c2"])
        vacd = pick(row, ["vac_diff_v", "vac_diff", "vac_p_minus_vac_n"])
        vacp = pick(row, ["vac_p_v", "vac_p", "c6"])
        vacn = pick(row, ["vac_n_v", "vac_n", "c7"])
        if vacd is None and vacp is not None and vacn is not None:
            vacd = vacp - vacn
        has_named_supply = has_any(row, ["vdd_raw_v", "vdd_raw", "raw_vdd", "vdd_core_v", "vdd_core", "core_vdd"])
        if has_named_supply or "c5" in row:
            vr = pick(row, ["vdd_raw_v", "vdd_raw", "raw_vdd", "c3"])
            vd = pick(row, ["vdd_core_v", "vdd_core", "core_vdd", "c4"])
            ii = pick(row, ["idd_core_a", "idd_core", "core_idd", "c5"])
        else:
            vr = None
            vd = pick(row, ["vdd_core_v", "vdd_core", "core_vdd", "c3"])
            ii = pick(row, ["idd_core_a", "idd_core", "core_idd", "c4"])
        if ti is None or vi is None or vo is None:
            continue
        t.append(ti)
        vin.append(vi)
        vout.append(vo)
        if vr is not None:
            vdd_raw.append(vr)
        if vd is not None:
            vdd_core.append(vd)
        if ii is not None:
            idd_core.append(ii)
        if vacp is not None:
            vac_p.append(vacp)
        if vacn is not None:
            vac_n.append(vacn)
        if vacd is not None:
            vac_diff.append(vacd)
    return t, vin, vout, vdd_raw, vdd_core, idd_core, vac_p, vac_n, vac_diff


def finite_min_max(values, include_zero=False):
    vals = [v for v in values if math.isfinite(v)]
    if not vals:
        return None
    y_min = min(vals)
    y_max = max(vals)
    if include_zero:
        y_min = min(0.0, y_min)
        y_max = max(0.0, y_max)
    pad = max((y_max - y_min) * 0.08, 1e-6)
    if y_max == y_min:
        pad = max(abs(y_max) * 0.02, 1e-3)
    return y_min - pad, y_max + pad


def downsample_traces(t, traces, max_points=4000):
    n = min([len(t)] + [len(vals) for _, vals, _, _ in traces])
    if n < 2:
        return [], []
    t = t[:n]
    traces = [(name, vals[:n], color, ylabel) for name, vals, color, ylabel in traces]
    if n > max_points:
        step = int(math.ceil(n / float(max_points)))
        t = t[::step]
        traces = [(name, vals[::step], color, ylabel) for name, vals, color, ylabel in traces]
    return t, traces


def plot_split_traces_png(path, t, traces, report_lines=None):
    path = Path(path)
    traces = [(name, vals, color, ylabel) for name, vals, color, ylabel in traces if vals]
    if not t or not traces:
        return
    t, traces = downsample_traces(t, traces)
    if not t or not traces:
        return
    width = 1000
    panel_h = 255
    top_margin = 45
    gap = 42
    bottom_margin = 70
    height = top_margin + len(traces) * panel_h + (len(traces) - 1) * gap + bottom_margin
    img = core.new_canvas(width, height)
    x_min, x_max = min(t), max(t)
    x_ticks = [(x_min + i * (x_max - x_min) / 5.0, core.format_axis_value(x_min + i * (x_max - x_min) / 5.0)) for i in range(6)]
    for idx, (name, vals, color, ylabel) in enumerate(traces):
        top = top_margin + idx * (panel_h + gap)
        box = (115, top, 960, top + panel_h)
        yrange = finite_min_max(vals, include_zero=False)
        if yrange is None:
            continue
        y_min, y_max = yrange
        x_map = lambda value, b=box: b[0] + (value - x_min) * (b[2] - b[0]) / max(x_max - x_min, 1e-300)
        y_map = lambda value, b=box, lo=y_min, hi=y_max: b[3] - (value - lo) * (b[3] - b[1]) / max(hi - lo, 1e-300)
        y_ticks = [(y_min + i * (y_max - y_min) / 4.0, core.format_axis_value(y_min + i * (y_max - y_min) / 4.0)) for i in range(5)]
        core.draw_axes(img, box, x_ticks, y_ticks, x_map, y_map, "TIME S" if idx == len(traces) - 1 else "", ylabel)
        core.draw_polyline(img, core.scale_points(t, vals, x_min, x_max, y_min, y_max, box), color)
        core.draw_text(img, box[0] + 10, box[1] + 10, name, color, scale=1)
        if idx == len(traces) - 1:
            core.draw_report_legend(img, box, report_lines)
    core.write_png(path, width, height, img)


def plot_converter_tran_png(path, t, vin, vout, report_lines=None):
    if len(t) < 2:
        return
    vin_dc = sum(vin) / len(vin) if vin else 0.0
    vout_dc = sum(vout) / len(vout) if vout else 0.0
    plot_split_traces_png(
        path,
        t,
        [
            ("GREEN VIN AC", [x - vin_dc for x in vin], (80, 150, 80), "VIN AC V"),
            ("BLUE VOUT AC", [x - vout_dc for x in vout], (30, 90, 200), "VOUT AC V"),
        ],
        report_lines,
    )


def plot_converter_ac_input_png(path, t, vac_p, vac_n, vac_diff, report_lines=None):
    plot_converter_ac_input_spectrum_png(path, t, vac_diff, report_lines)


def plot_converter_ac_input_tran_png(path, t, vac_p, vac_n, vac_diff, report_lines=None):
    traces = []
    if vac_p:
        traces.append(("VAC P", vac_p, (80, 150, 80), "VAC P V"))
    if vac_n:
        traces.append(("VAC N", vac_n, (170, 70, 170), "VAC N V"))
    if vac_diff:
        traces.append(("VAC DIFF P-N", vac_diff, (210, 95, 35), "VAC DIFF V"))
    plot_split_traces_png(path, t, traces, report_lines)


def plot_converter_ac_input_spectrum_png(path, t, vac_diff, report_lines=None):
    if len(t) < 4 or len(vac_diff) < 4:
        return
    n = min(len(t), len(vac_diff))
    pairs = [(t[i], vac_diff[i]) for i in range(n) if math.isfinite(t[i]) and math.isfinite(vac_diff[i])]
    if len(pairs) < 4:
        return
    pairs.sort(key=lambda x: x[0])
    nfft = 1
    while nfft * 2 <= min(len(pairs), 4096):
        nfft *= 2
    if nfft < 4:
        return
    t0, t1 = pairs[0][0], pairs[-1][0]
    if t1 <= t0:
        return
    dt = (t1 - t0) / (nfft - 1)
    sampled = []
    j = 0
    for i in range(nfft):
        ti = t0 + i * dt
        while j + 1 < len(pairs) and pairs[j + 1][0] < ti:
            j += 1
        if j + 1 == len(pairs):
            sampled.append(pairs[j][1])
        else:
            at, av = pairs[j]
            bt, bv = pairs[j + 1]
            frac = 0.0 if bt == at else (ti - at) / (bt - at)
            sampled.append(av + frac * (bv - av))
    mean = sum(sampled) / len(sampled)
    windowed = []
    for i, value in enumerate(sampled):
        win = 0.5 - 0.5 * math.cos(2.0 * math.pi * i / max(nfft - 1, 1))
        windowed.append(complex((value - mean) * win, 0.0))
    spectrum = core.fft(windowed)
    sample_rate = 1.0 / dt
    freqs = []
    mags = []
    for k in range(1, nfft // 2):
        freq = k * sample_rate / nfft
        mag = 2.0 * abs(spectrum[k]) / nfft
        if freq > 0 and math.isfinite(mag):
            freqs.append(freq)
            mags.append(mag)
    if not freqs:
        return
    max_mag = max(mags) if mags else 0.0
    floor = max(max_mag * 1e-6, 1e-15)
    mags_db = [20.0 * math.log10(max(m, floor)) for m in mags]
    width, height = 1000, 620
    img = core.new_canvas(width, height)
    box = (115, 55, 960, 535)
    xs = [math.log10(f) for f in freqs]
    x_min, x_max = min(xs), max(xs)
    y_min = math.floor((min(mags_db) - 5.0) / 10.0) * 10.0
    y_max = math.ceil((max(mags_db) + 5.0) / 10.0) * 10.0
    if y_max <= y_min:
        y_max = y_min + 10.0
    x_map = lambda value: box[0] + (value - x_min) * (box[2] - box[0]) / max(x_max - x_min, 1e-300)
    y_map = lambda value: box[3] - (value - y_min) * (box[3] - box[1]) / max(y_max - y_min, 1e-300)
    x_ticks = [(float(e), f"1E{e}") for e in range(int(math.ceil(x_min)), int(math.floor(x_max)) + 1)]
    if not x_ticks:
        x_ticks = [(x_min, core.format_axis_value(freqs[0])), (x_max, core.format_axis_value(freqs[-1]))]
    y_step = max(10.0, math.ceil((y_max - y_min) / 6.0 / 10.0) * 10.0)
    y_ticks = []
    yv = math.ceil(y_min / y_step) * y_step
    while yv <= y_max + 1e-9:
        y_ticks.append((yv, core.format_axis_value(yv)))
        yv += y_step
    core.draw_axes(img, box, x_ticks, y_ticks, x_map, y_map, "FREQ HZ", "VAC DIFF DBV")
    core.draw_polyline(img, core.scale_points(xs, mags_db, x_min, x_max, y_min, y_max, box), (210, 95, 35))
    core.draw_text(img, box[0] + 10, box[1] + 10, "VAC DIFF P-N SPECTRUM", (210, 95, 35), scale=1)
    peak_i = max(range(len(mags)), key=lambda i: mags[i])
    core.draw_text(img, box[0] + 10, box[1] + 26, f"PEAK {core.format_axis_value(freqs[peak_i])} HZ", (40, 40, 40), scale=1)
    core.draw_report_legend(img, box, report_lines)
    core.write_png(path, width, height, img)


def plot_supply_png(path, t, vac_diff, vdd_raw, vdd_core, report_lines=None):
    traces = []
    if vac_diff:
        traces.append(("VAC DIFF P-N", vac_diff, (80, 150, 80), "VAC DIFF V"))
    if vdd_raw:
        traces.append(("VDD RAW", vdd_raw, (210, 95, 35), "VDD RAW V"))
    if vdd_core:
        traces.append(("VDD CORE", vdd_core, (30, 90, 200), "VDD CORE V"))
    plot_split_traces_png(path, t, traces, report_lines)


def average_product(t, xs, ys, start_s=None, stop_s=None):
    pairs = []
    for ti, x, y in zip(t, xs, ys):
        if start_s is not None and ti < start_s:
            continue
        if stop_s is not None and ti > stop_s:
            continue
        if math.isfinite(ti) and math.isfinite(x) and math.isfinite(y):
            pairs.append((ti, abs(x * y)))
    if len(pairs) < 2:
        return None
    integ = 0.0
    for i in range(1, len(pairs)):
        integ += 0.5 * (pairs[i][1] + pairs[i - 1][1]) * (pairs[i][0] - pairs[i - 1][0])
    duration = pairs[-1][0] - pairs[0][0]
    return None if duration <= 0.0 else integ / duration


def average_value(t, xs, start_s=None, stop_s=None):
    pairs = []
    for ti, x in zip(t, xs):
        if start_s is not None and ti < start_s:
            continue
        if stop_s is not None and ti > stop_s:
            continue
        if math.isfinite(ti) and math.isfinite(x):
            pairs.append((ti, x))
    if len(pairs) < 2:
        return None
    integ = 0.0
    for i in range(1, len(pairs)):
        integ += 0.5 * (pairs[i][1] + pairs[i - 1][1]) * (pairs[i][0] - pairs[i - 1][0])
    duration = pairs[-1][0] - pairs[0][0]
    return None if duration <= 0.0 else integ / duration


def synthesize_converter_diff_input(t, cfg):
    conv = cfg.get("converter_input", {})
    kind = str(conv.get("kind", "sine")).strip().lower()
    if not t:
        return []
    if kind == "dc":
        return [float(conv.get("dc_v", 0.0)) for _ in t]
    if kind == "sine":
        dc = float(conv.get("dc_v", 0.0))
        amp = float(conv.get("amplitude_v", 3.0))
        freq = float(conv.get("frequency_hz", 100000.0))
        return [dc + amp * math.sin(2.0 * math.pi * freq * ti) for ti in t]
    return []


def converter_include_lines(cfg):
    lines = []
    for inc in cfg.get("include_files", []):
        lines.append(f'include "{resolve_path(cfg, inc)}"')
    for lib in cfg.get("library_sections", []):
        lib_path = resolve_path(cfg, lib["path"])
        section = lib.get("section")
        if section:
            lines.append(f'include "{lib_path}" section={section}')
        else:
            lines.append(f'include "{lib_path}"')
    for ahdl in cfg.get("ahdl_include_files", []):
        lines.append(f'ahdl_include "{resolve_path(cfg, ahdl)}"')
    return lines


def quote_path_from_include(line):
    m = re.search(r'(?:include|ahdl_include)\s+"([^"]+)"', line, re.IGNORECASE)
    return m.group(1) if m else None


def netlist_paths_recursive(path, seen=None):
    path = Path(path)
    seen = seen or set()
    try:
        resolved = path.resolve()
    except OSError:
        return []
    if resolved in seen or not resolved.exists() or resolved.suffix.lower() == ".va":
        return []
    seen.add(resolved)
    paths = [resolved]
    for line in core.read_netlist_logical_lines(resolved):
        inc = quote_path_from_include(line)
        if not inc:
            continue
        inc_path = Path(inc)
        if not inc_path.is_absolute():
            inc_path = resolved.parent / inc_path
        paths.extend(netlist_paths_recursive(inc_path, seen))
    return paths


def parse_subckt_blocks(paths):
    subckts = {}
    for path in paths:
        current_name = None
        current_body = []
        for line in core.read_netlist_logical_lines(path):
            tokens = line.split()
            if not tokens:
                continue
            head = tokens[0].lower()
            if head == "subckt" and len(tokens) >= 2:
                current_name = tokens[1]
                current_body = []
            elif head == "ends":
                if current_name:
                    subckts[current_name.lower()] = current_body
                current_name = None
                current_body = []
            elif current_name:
                current_body.append(line)
    return subckts


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


def estimate_primitive_area_p(inst, model, params):
    inst_l = inst.lower()
    model_l = (model or "").lower()
    mult = core.parse_number(params.get("m") or params.get("mult") or params.get("multi") or params.get("multiplier"), 1.0)
    nf = core.parse_number(params.get("nf") or params.get("fingers"), 1.0)
    if inst_l.startswith(("m", "x")) and ("fet" in model_l or "nfet" in model_l or "pfet" in model_l):
        w = core.parse_number(params.get("w"), 0.0)
        l = core.parse_number(params.get("l"), 0.0)
        return w * l * mult * nf / 1e-12 if w > 0.0 and l > 0.0 else 0.0
    if inst_l.startswith("r") or "res" in model_l:
        w = core.parse_number(params.get("w"), 0.0)
        l = core.parse_number(params.get("l"), 0.0)
        return w * l * mult / 1e-12 if w > 0.0 and l > 0.0 else 0.0
    if inst_l.startswith("c") or "cap" in model_l:
        w = core.parse_number(params.get("w"), 0.0)
        l = core.parse_number(params.get("l"), 0.0)
        if w > 0.0 and l > 0.0:
            return w * l * mult / 1e-12
        cval = core.parse_number(params.get("c"), 0.0)
        return cval / 1e-15 if cval > 0.0 else 0.0
    if "diode" in model_l or inst_l.startswith("d"):
        w = core.parse_number(params.get("w"), 0.0)
        l = core.parse_number(params.get("l"), 0.0)
        if w > 0.0 and l > 0.0:
            return w * l * mult / 1e-12
        return core.parse_area_param_p(params.get("area"), 1.0) * mult
    if inst_l.startswith("q") or "npn" in model_l or "pnp" in model_l or "bjt" in model_l:
        return core.parse_area_param_p(params.get("area"), 1.0) * mult
    return 0.0


def collect_core_netlist_area(top_netlist):
    paths = netlist_paths_recursive(top_netlist)
    if not paths:
        return None
    subckts = parse_subckt_blocks(paths)
    if "core" not in subckts:
        return None
    rows = []

    def walk(subckt_name, prefix, stack):
        body = subckts.get(subckt_name.lower())
        if body is None or subckt_name.lower() in stack:
            return
        stack = stack | {subckt_name.lower()}
        for line in body:
            tokens = line.split()
            if not tokens:
                continue
            inst = tokens[0]
            if inst.lower() in ("subckt", "ends", "include", "ahdl_include", "simulator"):
                continue
            model, params = instance_model_and_params(line)
            if not model:
                continue
            if model.lower() in subckts:
                walk(model, f"{prefix}{inst}/", stack)
                continue
            area = estimate_primitive_area_p(inst, model, params)
            if area > 0.0:
                rows.append({
                    "name": f"{prefix}{inst}",
                    "type": model,
                    "count": 1.0,
                    "netlist_m": 1.0,
                    "effective_count": 1.0,
                    "area_p_each": area,
                    "area_p_total": area,
                    "included": True,
                    "area_source": "core_netlist",
                })

    walk("CORE", "", set())
    if not rows:
        return None
    return {
        "area_total_p": sum(row["area_p_total"] for row in rows),
        "device_rows": rows,
        "area_source": "core_netlist",
    }


def percentile_value(values, pct):
    vals = sorted(v for v in values if math.isfinite(v))
    if not vals:
        return None
    idx = int(round((len(vals) - 1) * pct / 100.0))
    return vals[max(0, min(idx, len(vals) - 1))]


def measured_core_power_split(t, vdd_core, idd_core, steady_start, static_hint=None):
    powers = []
    for ti, vdd, cur in zip(t, vdd_core, idd_core):
        if ti < steady_start:
            continue
        if math.isfinite(ti) and math.isfinite(vdd) and math.isfinite(cur):
            powers.append(abs(vdd * cur))
    if not powers:
        return None, None, None
    total = sum(powers) / len(powers)
    if static_hint is not None and static_hint > 0.0:
        static = min(static_hint, total)
    else:
        static = percentile_value(powers, 5.0) or 0.0
    dynamic = max(total - static, 0.0)
    return static, dynamic, total


def normalize_pin_name(name):
    return str(name).strip().upper()


def mapped_nodes(pin_order, mapping, required, block_name):
    nodes = []
    for pin in pin_order:
        key = normalize_pin_name(pin)
        if key in mapping:
            node = mapping[key]
        else:
            node = key
        node = normalize_pin_name(node)
        if node not in required:
            allowed = " ".join(sorted(required))
            raise ValueError(f"{block_name} pin {pin!r} maps to {node!r}, expected one of: {allowed}")
        nodes.append(node)
    return nodes


def write_pin_mapping_wrapper(cfg, work_dir):
    pin_cfg = cfg.get("pin_mapping_wrapper", {})
    if not pin_cfg.get("enabled", False):
        return None
    raw_netlist = resolve_path(cfg, pin_cfg["raw_netlist"])
    core_cfg = pin_cfg["core"]
    conv_cfg = pin_cfg["converter"]
    core_required = {"VIN", "VREF", "VDD", "GND", "VOUT"}
    conv_required = {"VAC_P", "VAC_N", "GND", "VDD"}
    core_nodes = mapped_nodes(core_cfg["pin_order"], core_cfg.get("pin_map", {}), core_required, "CORE")
    conv_nodes = mapped_nodes(conv_cfg["pin_order"], conv_cfg.get("pin_map", {}), conv_required, "CONVERTER")
    wrapper = work_dir / "generated_pinmap_top.scs"
    raw_stmt = "ahdl_include" if raw_netlist.suffix.lower() == ".va" else "include"
    wrapper.write_text(
        "\n".join(
            [
                "simulator lang=spectre",
                f'{raw_stmt} "{raw_netlist}"',
                "",
                "subckt CORE VIN VREF VDD GND VOUT",
                f"    XCORE_DUT ({' '.join(core_nodes)}) {core_cfg['subckt']}",
                "ends CORE",
                "",
                "subckt CONVERTER VAC_P VAC_N GND VDD",
                f"    XCONVERTER_DUT ({' '.join(conv_nodes)}) {conv_cfg['subckt']}",
                "ends CONVERTER",
                "",
                "subckt TOP VIN VREF VAC_P VAC_N GND VOUT",
                "    XCONVERTER (VAC_P VAC_N GND VDD_RAW) CONVERTER",
                "    VSUPPLY_LINK (VDD_RAW VDD_CORE) vsource dc=0",
                "    XCORE (VIN VREF VDD_CORE GND VOUT) CORE",
                "ends TOP",
            ]
        )
        + "\n"
    )
    return wrapper


def write_converter_netlist(cfg):
    work_dir = resolve_path(cfg, cfg.get("work_dir")) or HERE / "run"
    work_dir.mkdir(parents=True, exist_ok=True)
    generated_wrapper = write_pin_mapping_wrapper(cfg, work_dir)
    top_netlist = generated_wrapper or resolve_path(cfg, cfg["top_netlist"])
    top_subckt = cfg.get("top_subckt", "TOP")
    spec = cfg["spec"]
    sim = cfg["sim"]["tran"]
    sim_ac = cfg["sim"].get("ac", {})
    conv = cfg.get("converter_input", {})
    vref = float(spec.get("vref_v", float(spec["vdd"]) * float(spec.get("vref_ratio", 0.5))))
    vindc = float(spec.get("vindc_v", vref))
    vin_amp = float(sim.get("input", {}).get("amplitude_v", spec.get("input_ac_amplitude", 0.001)))
    vin_freq = float(sim.get("input", {}).get("frequency_hz", 1000.0))
    vin_ac_mag = float(spec.get("input_ac_amplitude", 0.001))
    conv_kind = str(conv.get("kind", "sine")).strip().lower()
    vac_dc = float(conv.get("dc_v", 0.0))
    vac_amp = float(conv.get("amplitude_v", 3.0))
    vac_freq = float(conv.get("frequency_hz", 100000.0))
    if conv_kind == "pwl":
        pwl_file = resolve_path(cfg, conv.get("file"))
        vac_src = f'VAC (VAC_P VAC_N) vsource type=pwl file="{pwl_file}"'
    elif conv_kind == "dc":
        vac_src = f"VAC (VAC_P VAC_N) vsource dc={vac_dc}"
    else:
        vac_src = f"VAC (VAC_P VAC_N) vsource type=sine dc={vac_dc} ampl={vac_amp} freq={vac_freq}"
    tb_ac = work_dir / "tb_converter_ac.scs"
    tb_static = work_dir / "tb_converter_tran_static.scs"
    tb_tran = work_dir / "tb_converter_tran.scs"
    ocean = work_dir / "export_converter.ocn"
    include_stmt = "ahdl_include" if top_netlist.suffix.lower() == ".va" else "include"
    include_lines = converter_include_lines(cfg)
    init_cfg = cfg.get("initial_conditions", {})
    init_lines = []
    tran_options = []
    if init_cfg.get("enabled", True):
        vdd_raw_ic = float(init_cfg.get("vdd_raw_v", spec["vdd"]))
        vdd_core_ic = float(init_cfg.get("vdd_core_v", spec["vdd"]))
        vout_ic = float(init_cfg.get("vout_v", vref))
        init_lines.append(f"ic XTOP.VDD_RAW={vdd_raw_ic} XTOP.VDD_CORE={vdd_core_ic} VOUT={vout_ic}")
    if init_cfg.get("skipdc", False):
        tran_options.append("skipdc=yes")
    tran_options_text = (" " + " ".join(tran_options)) if tran_options else ""
    common_tail = [
        f"XTOP (VIN VREF VAC_P VAC_N 0 VOUT) {top_subckt}",
        f"CLOAD (VOUT 0) capacitor c={float(spec['load_cap_f'])}",
        "save VIN VOUT VAC_P VAC_N XTOP.VDD_RAW XTOP.VDD_CORE XTOP.XCORE:VDD",
        *init_lines,
    ]
    tb_ac.write_text(
        "\n".join(
            [
                "simulator lang=spectre",
                *include_lines,
                f'{include_stmt} "{top_netlist}"',
                f"VIN (VIN 0) vsource dc={vindc} mag={vin_ac_mag}",
                f"VREF (VREF 0) vsource dc={vref}",
                f"VAC (VAC_P VAC_N) vsource dc={vac_dc}",
                *common_tail,
                f"ac ac start={float(sim_ac.get('start_hz', 0.1))} stop={float(sim_ac.get('stop_hz', 1e7))} dec={int(sim_ac.get('points_per_dec', 50))}",
                "simulatorOptions options rawfmt=psfxl",
            ]
        )
        + "\n"
    )
    tb_tran.write_text(
        "\n".join(
            [
                "simulator lang=spectre",
                *include_lines,
                f'{include_stmt} "{top_netlist}"',
                f"VIN (VIN 0) vsource type=sine dc={vindc} ampl={vin_amp} freq={vin_freq}",
                f"VREF (VREF 0) vsource dc={vref}",
                vac_src,
                *common_tail,
                f"tran tran stop={float(sim['stop_s'])} maxstep={float(sim['maxstep_s'])} strobeperiod={float(sim.get('strobe_s', sim['maxstep_s']))}{tran_options_text}",
                "simulatorOptions options rawfmt=psfxl",
            ]
        )
        + "\n"
    )
    tb_static.write_text(
        "\n".join(
            [
                "simulator lang=spectre",
                *include_lines,
                f'{include_stmt} "{top_netlist}"',
                f"VIN (VIN 0) vsource dc={vindc}",
                f"VREF (VREF 0) vsource dc={vref}",
                vac_src,
                *common_tail,
                f"tran tran stop={float(sim['stop_s'])} maxstep={float(sim['maxstep_s'])} strobeperiod={float(sim.get('strobe_s', sim['maxstep_s']))}{tran_options_text}",
                "simulatorOptions options rawfmt=psfxl",
            ]
        )
        + "\n"
    )
    ac_csv_path = work_dir / "ac.csv"
    static_csv_path = work_dir / "tran_core_static.csv"
    tran_csv_path = work_dir / "tran_core.csv"
    ocean.write_text(
        "\n".join(
            [
                f'openResults("{work_dir / "ac_psf"}")',
                "selectResult('ac)",
                f'ocnPrint(?output "{ac_csv_path}" ?numberNotation \'scientific ?precision 12 v("VIN") v("VOUT"))',
                f'openResults("{work_dir / "tran_static_psf"}")',
                "selectResult('tran)",
                f'ocnPrint(?output "{static_csv_path}" ?numberNotation \'scientific ?precision 12 getData("VIN") getData("VOUT") getData("XTOP.VDD_RAW") getData("XTOP.VDD_CORE") getData("XTOP.XCORE:VDD") getData("VAC_P") getData("VAC_N"))',
                f'openResults("{work_dir / "tran_psf"}")',
                "selectResult('tran)",
                f'ocnPrint(?output "{tran_csv_path}" ?numberNotation \'scientific ?precision 12 getData("VIN") getData("VOUT") getData("XTOP.VDD_RAW") getData("XTOP.VDD_CORE") getData("XTOP.XCORE:VDD") getData("VAC_P") getData("VAC_N"))',
                "exit()",
            ]
        )
        + "\n"
    )
    return tb_ac, tb_static, tb_tran, ocean


def run_simulation(cfg):
    work_dir = resolve_path(cfg, cfg.get("work_dir")) or HERE / "run"
    tb_ac, tb_static, tb_tran, ocean = write_converter_netlist(cfg)
    sim = cfg["sim"]
    if sim.get("run_spectre", True):
        subprocess.run(
            [sim.get("spectre_cmd", "spectre"), str(tb_ac), "+escchars", "+log", str(work_dir / "spectre_converter_ac.log"), "-format", "psfxl", "-raw", str(work_dir / "ac_psf")]
            + sim.get("spectre_args", []),
            cwd=str(work_dir),
            check=True,
        )
        subprocess.run(
            [sim.get("spectre_cmd", "spectre"), str(tb_static), "+escchars", "+log", str(work_dir / "spectre_converter_tran_static.log"), "-format", "psfxl", "-raw", str(work_dir / "tran_static_psf")]
            + sim.get("spectre_args", []),
            cwd=str(work_dir),
            check=True,
        )
        subprocess.run(
            [sim.get("spectre_cmd", "spectre"), str(tb_tran), "+escchars", "+log", str(work_dir / "spectre_converter_tran.log"), "-format", "psfxl", "-raw", str(work_dir / "tran_psf")]
            + sim.get("spectre_args", []),
            cwd=str(work_dir),
            check=True,
        )
    if sim.get("run_ocean_export", True):
        subprocess.run([sim.get("ocean_cmd", "ocean"), "-nograph", "-restore", str(ocean)], cwd=str(work_dir), check=True)


def analyze(cfg):
    work_dir = resolve_path(cfg, cfg.get("work_dir")) or HERE / "run"
    ac_csv = resolve_path(cfg, cfg.get("input_files", {}).get("ac_csv")) or work_dir / "ac.csv"
    static_tran_csv = resolve_path(cfg, cfg.get("input_files", {}).get("static_tran_csv")) or work_dir / "tran_core_static.csv"
    tran_csv = resolve_path(cfg, cfg.get("input_files", {}).get("tran_csv")) or work_dir / "tran_core.csv"
    devices_csv = resolve_path(cfg, cfg.get("input_files", {}).get("devices_core_csv"))
    ac_data = core.load_ac(ac_csv) if ac_csv.exists() else []
    ts, _, _, vdd_raw_static, vdd_core_static, idd_core_static, _, _, _ = load_tran(static_tran_csv) if static_tran_csv.exists() else ([], [], [], [], [], [], [], [], [])
    t, vin, vout, vdd_raw, vdd_core, idd_core, vac_p, vac_n, vac_diff = load_tran(tran_csv)
    if not vac_diff:
        vac_diff = synthesize_converter_diff_input(t, cfg)
    generated_wrapper = write_pin_mapping_wrapper(cfg, work_dir)
    analysis_netlist = generated_wrapper or resolve_path(cfg, cfg.get("top_netlist"))
    area_cfg = dict(cfg)
    area_cfg["dut_netlist"] = str(analysis_netlist)
    area_power = core.analyze_area_power(devices_csv, area_cfg, None)
    netlist_area = collect_core_netlist_area(analysis_netlist)
    if netlist_area is not None:
        area_power["area_total_p"] = netlist_area["area_total_p"]
        area_power["device_rows"] = netlist_area["device_rows"]
        area_power["area_source"] = netlist_area["area_source"]
    steady_start = float(cfg.get("power", {}).get("steady_start_s", cfg["sim"]["tran"].get("settle_skip_s", 0.0)))
    measured = average_product(t, vdd_core, idd_core, steady_start, None) if vdd_core and idd_core else None
    measured_static = average_product(ts, vdd_core_static, idd_core_static, steady_start, None) if vdd_core_static and idd_core_static else None
    avg_raw = average_value(t, vdd_raw, steady_start, None) if vdd_raw else None
    avg_vdd = average_value(t, vdd_core, steady_start, None) if vdd_core else float(cfg["spec"]["vdd"])
    avg_raw_static = average_value(ts, vdd_raw_static, steady_start, None) if vdd_raw_static else None
    avg_vdd_static = average_value(ts, vdd_core_static, steady_start, None) if vdd_core_static else None
    p_static_estimate = (avg_vdd or float(cfg["spec"]["vdd"])) * area_power.get("static_current_a", 0.0)
    p_static = measured_static if measured_static is not None else p_static_estimate
    p_total = measured if measured is not None else p_static
    p_dynamic_raw = p_total - p_static
    p_dynamic = max(p_dynamic_raw, 0.0)
    area_power.update({
        "power_static_w": p_static,
        "power_dc_w": p_static,
        "power_static_source": "static_tran_csv" if measured_static is not None else "devices_core_csv_estimate",
        "power_static_measured_w": measured_static,
        "power_static_estimate_w": p_static_estimate,
        "power_dynamic_measured_w": p_dynamic,
        "power_dynamic_raw_w": p_dynamic_raw,
        "power_dynamic_w": p_dynamic,
        "power_measured_total_w": measured,
        "power_total_w": p_total,
        "power_score_basis_w": p_total,
        "converter_vdd_raw_average_v": avg_raw,
        "core_vdd_average_v": avg_vdd,
        "converter_vdd_raw_static_average_v": avg_raw_static,
        "core_vdd_static_average_v": avg_vdd_static,
    })
    ac = core.analyze_ac(ac_data, cfg["spec"])
    tran = core.analyze_tran(t, vin, vout, cfg["spec"], cfg["sim"]["tran"])
    ac_nrmse = ac.get("ac_nrmse_db")
    tran_nrmse = tran.get("tran_ac_nrmse_vs_target_filter")
    nrmse_vals = [x for x in (ac_nrmse, tran_nrmse) if x is not None]
    combined_nrmse = sum(nrmse_vals) / len(nrmse_vals) if nrmse_vals else None
    result = {
        "design_name": cfg.get("design_name", "converter_top"),
        "mode": "CONVERTER_TOP_CORE_PPA_ONLY",
        "area_power": area_power,
        "ac": ac,
        "tran": tran,
        "performance_nrmse_combined": combined_nrmse,
        "similarity": None if combined_nrmse is None else 1.0 / (1.0 + combined_nrmse),
    }
    out = work_dir / "converter_core_ppa_metrics.json"
    work_dir.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    (work_dir / "ppa_metrics.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    lines = core.ppa_report_legend_lines(result)
    if avg_raw is not None:
        lines.append(f"VDD RAW {core.format_axis_value(avg_raw)} V")
    if avg_vdd is not None:
        lines.append(f"VDD CORE {core.format_axis_value(avg_vdd)} V")
    core.plot_ac_png(work_dir / "converter_ac_response.png", ac_data, cfg["spec"], lines)
    plot_converter_tran_png(work_dir / "converter_transient_response.png", t, vin, vout, lines)
    plot_converter_ac_input_png(work_dir / "converter_input_spectrum.png", t, vac_p, vac_n, vac_diff, lines)
    plot_converter_ac_input_tran_png(work_dir / "converter_ac_input_transient.png", t, vac_p, vac_n, vac_diff, lines)
    plot_supply_png(work_dir / "converter_supply_response.png", t, vac_diff, vdd_raw, vdd_core, lines)
    core.plot_power_png(work_dir / "converter_core_power_breakdown.png", area_power, lines)
    write_report(work_dir / "converter_core_ppa_report.log", result)
    write_report(work_dir / "ppa_report.log", result)
    write_summary(work_dir / "ppa_summary.log", result)
    return result


def write_summary(path, result):
    ap = result["area_power"]
    ac = result.get("ac", {})
    tr = result.get("tran", {})
    lines = [
        f"design: {result['design_name']}",
        f"mode: {result['mode']}",
        f"area_total_p: {core.fmt_value(ap.get('area_total_p'))}",
        f"power_static_w: {core.fmt_value(ap.get('power_static_w'))}",
        f"power_static_source: {core.fmt_value(ap.get('power_static_source'))}",
        f"power_static_measured_w: {core.fmt_value(ap.get('power_static_measured_w'))}",
        f"power_static_estimate_w: {core.fmt_value(ap.get('power_static_estimate_w'))}",
        f"power_dynamic_raw_w: {core.fmt_value(ap.get('power_dynamic_raw_w'))}",
        f"power_dynamic_w: {core.fmt_value(ap.get('power_dynamic_w'))}",
        f"power_total_w: {core.fmt_value(ap.get('power_total_w'))}",
        f"core_vdd_average_v: {core.fmt_value(ap.get('core_vdd_average_v'))}",
        f"core_vdd_static_average_v: {core.fmt_value(ap.get('core_vdd_static_average_v'))}",
        f"ac_nrmse_db: {core.fmt_value(ac.get('ac_nrmse_db'))}",
        f"tran_ac_nrmse_vs_target_filter: {core.fmt_value(tr.get('tran_ac_nrmse_vs_target_filter'))}",
        f"performance_nrmse_combined: {core.fmt_value(result.get('performance_nrmse_combined'))}",
        f"similarity: {core.fmt_value(result.get('similarity'))}",
    ]
    path.write_text("\n".join(lines) + "\n")


def write_report(path, result):
    ap = result["area_power"]
    ac = result.get("ac", {})
    tr = result["tran"]
    lines = [
        f"design: {result['design_name']}",
        f"mode: {result['mode']}",
        "",
        "[CORE-only Area / Power]",
        f"area_total_p: {core.fmt_value(ap.get('area_total_p'))}",
        f"area_source: {ap.get('area_source', 'devices_csv')}",
        f"static_current_a: {core.fmt_value(ap.get('static_current_a'), ' A')}",
        f"core_vdd_average_v: {core.fmt_value(ap.get('core_vdd_average_v'), ' V')}",
        f"core_vdd_static_average_v: {core.fmt_value(ap.get('core_vdd_static_average_v'), ' V')}",
        f"power_static_w: {core.fmt_value(ap.get('power_static_w'), ' W')}",
        f"power_static_source: {core.fmt_value(ap.get('power_static_source'))}",
        f"power_static_measured_w: {core.fmt_value(ap.get('power_static_measured_w'), ' W')}",
        f"power_static_estimate_w: {core.fmt_value(ap.get('power_static_estimate_w'), ' W')}",
        f"power_dynamic_raw_w: {core.fmt_value(ap.get('power_dynamic_raw_w'), ' W')}",
        f"power_dynamic_w: {core.fmt_value(ap.get('power_dynamic_w'), ' W')}",
        f"power_total_w: {core.fmt_value(ap.get('power_total_w'), ' W')}",
        "",
        "[TOP AC response]",
        f"ac_nrmse_db: {core.fmt_value(ac.get('ac_nrmse_db'))}",
        f"ac_rmse_db: {core.fmt_value(ac.get('ac_rmse_db'), ' dB')}",
        f"midband_gain_vv: {core.fmt_value(ac.get('midband_gain_vv'))}",
        f"midband_gain_db: {core.fmt_value(ac.get('midband_gain_db'), ' dB')}",
        f"lower_3db_hz: {core.fmt_value(ac.get('lower_3db_hz'), ' Hz')}",
        f"upper_3db_hz: {core.fmt_value(ac.get('upper_3db_hz'), ' Hz')}",
        f"passband_ripple_db: {core.fmt_value(ac.get('passband_ripple_db'), ' dB')}",
        "",
        "[TOP transient similarity]",
        f"tran_ac_nrmse_vs_target_filter: {core.fmt_value(tr.get('tran_ac_nrmse_vs_target_filter'))}",
        f"tran_ac_rmse_v: {core.fmt_value(tr.get('tran_ac_rmse_v'), ' V')}",
        f"vout_ac_peak_to_peak_v: {core.fmt_value(tr.get('vout_ac_peak_to_peak_v'), ' V')}",
        f"performance_nrmse_combined: {core.fmt_value(result.get('performance_nrmse_combined'))}",
        f"similarity: {core.fmt_value(result.get('similarity'))}",
    ]
    path.write_text("\n".join(lines) + "\n")


def print_summary(result):
    ap = result["area_power"]
    print(f"design: {result['design_name']}")
    print(f"mode: {result['mode']}")
    print(f"area_total_p: {ap.get('area_total_p'):.6g}")
    print(f"power_static_w: {ap.get('power_static_w'):.6g}")
    print(f"power_dynamic_w: {ap.get('power_dynamic_w'):.6g}")
    print(f"power_total_w: {ap.get('power_total_w'):.6g}")
    print(f"ac_nrmse_db: {result.get('ac', {}).get('ac_nrmse_db')}")
    print(f"tran_ac_nrmse_vs_target_filter: {result.get('tran', {}).get('tran_ac_nrmse_vs_target_filter')}")
    print(f"performance_nrmse_combined: {result.get('performance_nrmse_combined')}")
    print(f"similarity: {result.get('similarity')}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("cmd", choices=["gen-netlist", "run", "analyze", "all"])
    parser.add_argument("--config", default="config_converter.json")
    args = parser.parse_args()
    cfg = load_config(Path(args.config))
    if args.cmd in ("gen-netlist", "all"):
        write_converter_netlist(cfg)
    if args.cmd in ("run", "all"):
        run_simulation(cfg)
    if args.cmd in ("analyze", "all"):
        print_summary(analyze(cfg))


if __name__ == "__main__":
    main()

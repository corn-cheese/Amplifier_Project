#!/usr/bin/env python3
"""Generate the converter block diagram PDF/SVG without external packages."""

from pathlib import Path


OUT_DIR = Path(__file__).resolve().parent
PDF_PATH = OUT_DIR / "converter_block_diagram.pdf"
SVG_PATH = OUT_DIR / "converter_block_diagram.svg"


W, H = 842, 595


def esc_pdf(text):
    return str(text).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def pdf_text(x, y, text, size=10, color=(0.10, 0.12, 0.16), font="F1"):
    r, g, b = color
    return f"{r:.3f} {g:.3f} {b:.3f} rg /{font} {size} Tf 1 0 0 1 {x:.1f} {y:.1f} Tm ({esc_pdf(text)}) Tj\n"


def pdf_rect(x, y, w, h, stroke=(0.15, 0.18, 0.23), fill=None, lw=1.2):
    out = f"{lw:.2f} w\n"
    if fill:
        r, g, b = fill
        out += f"{r:.3f} {g:.3f} {b:.3f} rg {x:.1f} {y:.1f} {w:.1f} {h:.1f} re f\n"
    r, g, b = stroke
    out += f"{r:.3f} {g:.3f} {b:.3f} RG {x:.1f} {y:.1f} {w:.1f} {h:.1f} re S\n"
    return out


def pdf_line(x1, y1, x2, y2, color=(0.15, 0.18, 0.23), lw=1.2):
    r, g, b = color
    return f"{lw:.2f} w {r:.3f} {g:.3f} {b:.3f} RG {x1:.1f} {y1:.1f} m {x2:.1f} {y2:.1f} l S\n"


def pdf_arrow(x1, y1, x2, y2, label=None):
    out = pdf_line(x1, y1, x2, y2, lw=1.4)
    if x2 >= x1:
        out += f"0.15 0.18 0.23 rg {x2:.1f} {y2:.1f} m {x2 - 8:.1f} {y2 + 4:.1f} l {x2 - 8:.1f} {y2 - 4:.1f} l f\n"
    else:
        out += f"0.15 0.18 0.23 rg {x2:.1f} {y2:.1f} m {x2 + 8:.1f} {y2 + 4:.1f} l {x2 + 8:.1f} {y2 - 4:.1f} l f\n"
    if label:
        out += pdf_text((x1 + x2) / 2 - len(label) * 2.3, y1 + 8, label, 9, color=(0.27, 0.30, 0.36))
    return out


def block(x, y, w, h, title, subtitle, ports_left=(), ports_right=(), fill=(0.96, 0.98, 1.0)):
    out = pdf_rect(x, y, w, h, fill=fill, lw=1.4)
    out += pdf_text(x + 14, y + h - 26, title, 17, font="F2")
    if subtitle:
        out += pdf_text(x + 14, y + h - 45, subtitle, 9, color=(0.35, 0.38, 0.44))
    step_l = h / (len(ports_left) + 1) if ports_left else h
    for i, name in enumerate(ports_left, 1):
        py = y + h - step_l * i
        out += pdf_line(x - 12, py, x, py, lw=1.1)
        out += pdf_text(x + 8, py - 3, name, 8, color=(0.25, 0.28, 0.33))
    step_r = h / (len(ports_right) + 1) if ports_right else h
    for i, name in enumerate(ports_right, 1):
        py = y + h - step_r * i
        out += pdf_line(x + w, py, x + w + 12, py, lw=1.1)
        out += pdf_text(x + w - 8 - len(name) * 4.5, py - 3, name, 8, color=(0.25, 0.28, 0.33))
    return out


def make_pdf():
    stream = ""
    stream += pdf_text(36, 552, "AC-DC Converter Attached Neural Amplifier: Required Block Diagram", 18, font="F2")
    stream += pdf_text(36, 532, "Use exactly these three subckt symbols and port signatures. TOP wraps CONVERTER and CORE only.", 10)
    stream += pdf_text(36, 516, "Font intent: Pretendard style. PDF uses standard Helvetica fallback for portability.", 8, color=(0.45, 0.48, 0.55))

    top_x, top_y, top_w, top_h = 42, 112, 758, 372
    stream += pdf_rect(top_x, top_y, top_w, top_h, stroke=(0.08, 0.09, 0.12), fill=(0.99, 0.99, 1.0), lw=1.8)
    stream += pdf_text(top_x + 12, top_y + top_h - 22, "TOP  (VIN VREF VAC_P VAC_N GND VOUT)", 12, font="F2")

    conv = (122, 276, 150, 104)
    core = (566, 238, 156, 142)
    stream += block(*conv, "CONVERTER", "AC to raw supply", ("VAC_P", "VAC_N", "GND"), ("VDD_RAW",), fill=(0.93, 0.97, 1.0))
    stream += block(*core, "CORE", "neural amplifier only", ("VIN", "VREF", "VDD", "GND"), ("VOUT",), fill=(1.00, 0.97, 0.94))

    stream += pdf_arrow(42, 350, 110, 350, "VAC_P")
    stream += pdf_arrow(42, 324, 110, 324, "VAC_N")
    stream += pdf_arrow(272, 328, 566, 328, "VDD_RAW TO VDD_CORE")
    stream += pdf_arrow(42, 260, 554, 300, "VIN")
    stream += pdf_arrow(42, 236, 554, 272, "VREF")
    stream += pdf_arrow(722, 309, 800, 309, "VOUT")
    stream += pdf_line(86, 184, 752, 184, color=(0.32, 0.35, 0.40), lw=1.0)
    stream += pdf_text(92, 192, "GND common reference to CONVERTER, CORE, and TOP", 9, color=(0.32, 0.35, 0.40))
    for x in (122, 566):
        stream += pdf_line(x - 12, 184, x - 12, 292 if x != 586 else 266, color=(0.32, 0.35, 0.40), lw=0.9)

    table_x, table_y = 444, 30
    stream += pdf_rect(table_x, table_y, 356, 70, fill=(1.0, 1.0, 1.0), lw=1.0)
    stream += pdf_text(table_x + 10, table_y + 52, "Required subckt signatures", 11, font="F2")
    rows = [
        "CORE      VIN VREF VDD GND VOUT",
        "CONVERTER VAC_P VAC_N GND VDD",
        "TOP       VIN VREF VAC_P VAC_N GND VOUT",
    ]
    for i, row in enumerate(rows):
        stream += pdf_text(table_x + 10, table_y + 36 - i * 10, row, 7.5)

    note_x, note_y = 42, 30
    stream += pdf_rect(note_x, note_y, 370, 70, fill=(1.0, 1.0, 1.0), lw=1.0)
    stream += pdf_text(note_x + 10, note_y + 52, "Measurement rule", 11, font="F2")
    stream += pdf_text(note_x + 10, note_y + 36, "Similarity: TOP VOUT transient vs target response", 8.5)
    stream += pdf_text(note_x + 10, note_y + 24, "Power/Area: CORE only, using XCORE and VDD_CORE", 8.5)
    stream += pdf_text(note_x + 10, note_y + 12, "Total CORE power = static + dynamic", 8.5)

    stream_bytes = stream.encode("latin-1")
    objects = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    objects.append(
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 842 595] /Resources << /Font << /F1 4 0 R /F2 5 0 R >> >> /Contents 6 0 R >>"
    )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")
    objects.append(b"<< /Length " + str(len(stream_bytes)).encode("ascii") + b" >>\nstream\n" + stream_bytes + b"endstream")

    data = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += f"{i} 0 obj\n".encode("ascii") + obj + b"\nendobj\n"
    xref = len(data)
    data += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii")
    for off in offsets[1:]:
        data += f"{off:010d} 00000 n \n".encode("ascii")
    data += f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii")
    PDF_PATH.write_bytes(data)


def make_svg():
    svg = """<svg xmlns="http://www.w3.org/2000/svg" width="842" height="595" viewBox="0 0 842 595">
<style>
  text { font-family: Pretendard, Helvetica, Arial, sans-serif; fill: #1a1f2a; }
  .small { font-size: 12px; fill: #555b66; }
  .port { font-size: 11px; fill: #3c4350; }
  .title { font-size: 22px; font-weight: 700; }
  .block-title { font-size: 20px; font-weight: 700; }
  .box { stroke: #252b36; stroke-width: 1.6; rx: 8; }
  .line { stroke: #252b36; stroke-width: 1.8; fill: none; marker-end: url(#arrow); }
</style>
<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="#252b36"/></marker></defs>
<rect width="842" height="595" fill="#fff"/>
<text x="36" y="43" class="title">AC-DC Converter Attached Neural Amplifier: Required Block Diagram</text>
<text x="36" y="64" class="small">Use exactly these three subckt symbols and port signatures. TOP wraps CONVERTER and CORE only.</text>
<rect x="42" y="112" width="758" height="372" fill="#fcfcff" stroke="#151821" stroke-width="2"/>
<text x="54" y="137" font-size="15" font-weight="700">TOP  (VIN VREF VAC_P VAC_N GND VOUT)</text>
<rect x="122" y="215" width="150" height="104" class="box" fill="#eef7ff"/>
<text x="136" y="244" class="block-title">CONVERTER</text><text x="136" y="264" class="small">AC to raw supply</text>
<rect x="566" y="195" width="156" height="142" class="box" fill="#fff6ef"/>
<text x="580" y="224" class="block-title">CORE</text><text x="580" y="244" class="small">neural amplifier only</text>
<line x1="42" y1="245" x2="110" y2="245" class="line"/><text x="56" y="237" class="port">VAC_P</text>
<line x1="42" y1="271" x2="110" y2="271" class="line"/><text x="56" y="263" class="port">VAC_N</text>
<line x1="272" y1="267" x2="566" y2="267" class="line"/><text x="356" y="259" class="port">VDD_RAW TO VDD_CORE</text>
<line x1="42" y1="340" x2="554" y2="300" class="line"/><text x="58" y="331" class="port">VIN</text>
<line x1="42" y1="374" x2="554" y2="272" class="line"/><text x="58" y="365" class="port">VREF</text>
<line x1="722" y1="266" x2="800" y2="266" class="line"/><text x="745" y="258" class="port">VOUT</text>
<line x1="86" y1="411" x2="752" y2="411" stroke="#555b66" stroke-width="1.2"/><text x="92" y="400" class="small">GND common reference to CONVERTER, CORE, and TOP</text>
<rect x="42" y="495" width="370" height="70" fill="#fff" stroke="#555"/>
<text x="52" y="518" font-size="14" font-weight="700">Measurement rule</text>
<text x="52" y="538" class="small">Similarity: TOP VOUT transient vs target response</text>
<text x="52" y="552" class="small">Power/Area: CORE only, using XCORE and VDD_CORE</text>
<rect x="444" y="495" width="356" height="70" fill="#fff" stroke="#555"/>
<text x="454" y="518" font-size="14" font-weight="700">Required subckt signatures</text>
<text x="454" y="536" class="small">CORE      VIN VREF VDD GND VOUT</text>
<text x="454" y="550" class="small">CONVERTER VAC_P VAC_N GND VDD</text>
<text x="454" y="564" class="small">TOP       VIN VREF VAC_P VAC_N GND VOUT</text>
</svg>
"""
    SVG_PATH.write_text(svg)


def main():
    make_pdf()
    make_svg()
    print(PDF_PATH)
    print(SVG_PATH)


if __name__ == "__main__":
    main()

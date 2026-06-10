import pathlib
import zipfile
import xml.etree.ElementTree as ET

PPTX = pathlib.Path(
    r"D:\Codex\Ampifier_project\outputs\019eafe1-e32a-7b91-9978-bc328db2367d\presentations\ai-agent-agentic-workflow-langgraph-amplifier\output\ai-agent-agentic-workflow-langgraph-amplifier-added-circuit-problem.pptx"
)

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}

for prefix, uri in NS.items():
    ET.register_namespace(prefix, uri)

NEW_SLIDE_TEXTS = [
    "전자회로 설계 문제",
    "Sources: neural_signal_amplifier_project.md, runner_config.json",
    "05",
    "목표는 신경 신호용 증폭기를 설계해",
    "스펙과 PPA를 동시에 만족시키는 것이었다.",
    "주어진 소자만으로 1 mV 입력을 약 100 V/V로 키우고, 10 Hz-20 kHz 대역과 10 pF load에서 AC·transient 응답을 맞춰야 했다.",
    "작은 신호 증폭",
    "VIN=2.5V+1mV",
    "gain 40 dB",
    "VOUT~100 mV",
    "→",
    "대역/부하 제약",
    "10 Hz-20 kHz",
    "-80 dB/dec",
    "CL=10 pF",
    "→",
    "회로 선택 변수",
    "topology",
    "R/C sizing",
    "OPAMP f_t/bias",
    "→",
    "합격 판정",
    "AC response",
    "transient",
    "power/area",
    "회로 문제: 증폭·대역·안정성·PPA trade-off를 동시에 만족하는 topology와 parameter를 찾아야 했다.",
]

with zipfile.ZipFile(PPTX, "r") as zin:
    entries = [(info, zin.read(info.filename)) for info in zin.infolist()]

updated_entries = []
for info, data in entries:
    if info.filename == "ppt/slides/slide11.xml":
        root = ET.fromstring(data)
        text_nodes = root.findall(".//a:t", NS)
        if len(text_nodes) != len(NEW_SLIDE_TEXTS):
            raise RuntimeError(
                f"slide11 text-node mismatch: {len(text_nodes)} != {len(NEW_SLIDE_TEXTS)}"
            )
        for node, value in zip(text_nodes, NEW_SLIDE_TEXTS):
            node.text = value
        data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    updated_entries.append((info, data))

tmp = PPTX.with_suffix(".tmp.pptx")
with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zout:
    seen = set()
    for info, data in updated_entries:
        if info.filename in seen:
            continue
        seen.add(info.filename)
        zout.writestr(info, data)

tmp.replace(PPTX)
print(PPTX)

# CONVERTER TOP 테스트

이 폴더는 AC-DC converter가 CORE supply를 생성하는 회로를 COREONLY 테스트와 분리해서 평가하기 위한 template입니다.

블럭 연결 그림은 `converter_block_diagram.pdf`를 보면 됩니다. 같은 내용의 편집/확인용 SVG 원본은 `converter_block_diagram.svg`입니다.

## 고정 심볼과 포트

제출 회로는 아래 3개 subckt 이름만 사용합니다. 포트 이름과 순서는 반드시 그대로 유지합니다.

```text
CORE      VIN VREF VDD GND VOUT
CONVERTER VAC_P VAC_N GND VDD
TOP       VIN VREF VAC_P VAC_N GND VOUT
```

`TOP`은 `CONVERTER`와 `CORE`만 감싸는 wrapper입니다. converter가 만든 supply를 `VDD_RAW`로 관측하고, 이 supply가 `VDD_CORE`를 통해 CORE의 `VDD`로 들어갑니다.

내부 회로는 자유롭게 구현해도 됩니다. 단, `TOP` 안에서 CORE instance 이름과 supply net 이름은 측정과 그래프 표시를 위해 다음 규칙을 지켜야 합니다.

```text
CORE instance name: XCORE
converter output net: VDD_RAW
CORE supply net:    VDD_CORE
```

예시:

```spectre
subckt TOP VIN VREF VAC_P VAC_N GND VOUT
    XCONVERTER (VAC_P VAC_N GND VDD_RAW) CONVERTER
    VSUPPLY_LINK (VDD_RAW VDD_CORE) vsource dc=0
    XCORE (VIN VREF VDD_CORE GND VOUT) CORE
ends TOP
```

`VSUPPLY_LINK`는 CLAMP가 아니라 `VDD_RAW`와 `VDD_CORE`를 둘 다 파형으로 보기 위한 0 V ideal link입니다. 실제 CORE power 계산은 계속 `VDD_CORE`와 `XCORE` 기준으로만 수행합니다.

## Pin 순서와 wrapper 작성법

`COREONLY` 테스트는 `config.json`의 `dut_pins_order`로 DUT pin 순서를 바꿀 수 있습니다. 하지만 `CONVERTER` 테스트는 `config_converter.json`에서 pin 순서를 바꾸지 않습니다.

이유는 CONVERTER 테스트의 측정 대상이 단일 DUT가 아니라 `TOP` wrapper이기 때문입니다. testbench는 항상 아래 순서로 `TOP`을 부릅니다.

```spectre
XTOP (VIN VREF VAC_P VAC_N 0 VOUT) TOP
```

따라서 제출 netlist에는 반드시 아래 subckt가 있어야 합니다.

```spectre
subckt TOP VIN VREF VAC_P VAC_N GND VOUT
    ...
ends TOP
```

Virtuoso에서 생성된 amplifier/converter subckt의 pin 순서가 template과 달라도 괜찮습니다. 단, 그 순서 차이는 `TOP` 안에서 instance를 만들 때 직접 맞춰야 합니다. Spectre는 instance의 node를 subckt 선언 순서대로 연결하므로, wrapper에서 순서만 정확히 맞추면 됩니다.

예를 들어 Virtuoso가 아래처럼 netlist를 만들었다고 가정합니다.

```spectre
subckt MY_AMP GND VDD INP OUT REF
    ...
ends MY_AMP

subckt MY_CONV OUTP GND INN INP
    ...
ends MY_CONV
```

이 경우 `CORE`와 `CONVERTER` wrapper를 이렇게 작성합니다.

```spectre
subckt CORE VIN VREF VDD GND VOUT
    XAMP (GND VDD VIN VOUT VREF) MY_AMP
ends CORE

subckt CONVERTER VAC_P VAC_N GND VDD
    XCONV (VDD GND VAC_N VAC_P) MY_CONV
ends CONVERTER
```

그리고 `TOP`은 아래 구조를 유지합니다.

```spectre
subckt TOP VIN VREF VAC_P VAC_N GND VOUT
    XCONVERTER (VAC_P VAC_N GND VDD_RAW) CONVERTER
    VSUPPLY_LINK (VDD_RAW VDD_CORE) vsource dc=0
    XCORE (VIN VREF VDD_CORE GND VOUT) CORE
ends TOP
```

정리하면, Virtuoso가 뽑은 회로 전체를 `TOP` 안에 그대로 복사해서 붙이는 방식이 아닙니다. 생성된 amplifier subckt와 converter subckt는 이름을 유지하거나 바꿔서 포함하고, 고정 이름 `CORE`, `CONVERTER`, `TOP` wrapper를 별도로 만들어 pin mapping을 맞춥니다. 참고용 파일은 `student_submission_template.scs`입니다.

직접 wrapper를 작성하기 어렵다면 `config_converter.json`의 `pin_mapping_wrapper`를 사용할 수 있습니다. 이 기능을 켜면 `run/generated_pinmap_top.scs`를 자동 생성하고, testbench는 이 wrapper를 사용합니다.

```json
"pin_mapping_wrapper": {
  "enabled": true,
  "raw_netlist": "student_raw_netlist.scs",
  "core": {
    "subckt": "MY_AMP",
    "pin_order": ["GND", "VDD", "INP", "OUT", "REF"],
    "pin_map": {
      "INP": "VIN",
      "REF": "VREF",
      "OUT": "VOUT"
    }
  },
  "converter": {
    "subckt": "MY_CONV",
    "pin_order": ["OUTP", "GND", "INN", "INP"],
    "pin_map": {
      "OUTP": "VDD",
      "INN": "VAC_N",
      "INP": "VAC_P"
    }
  }
}
```

의미:

- `raw_netlist`: Virtuoso가 뽑은 원본 netlist 경로
- `core.subckt`: 원본 netlist 안의 amplifier subckt 이름
- `core.pin_order`: 원본 amplifier subckt 선언에 적힌 pin 순서
- `core.pin_map`: 원본 pin 이름을 고정 CORE 신호명으로 매핑
- `converter.subckt`: 원본 netlist 안의 converter subckt 이름
- `converter.pin_order`: 원본 converter subckt 선언에 적힌 pin 순서
- `converter.pin_map`: 원본 pin 이름을 고정 CONVERTER 신호명으로 매핑

`pin_order`에 이미 `VIN`, `VREF`, `VDD`, `GND`, `VOUT` 또는 `VAC_P`, `VAC_N`, `GND`, `VDD` 같은 고정 이름을 쓴 경우에는 `pin_map`에 다시 적지 않아도 됩니다.

## SKY130 model/include 설정

sky130 library 소자를 사용한 netlist라면 Spectre testbench가 PDK model file을 먼저 include해야 합니다. `CONVERTER` 테스트도 `COREONLY`와 동일하게 `config_converter.json`에서 include를 설정합니다.

기본 예시는 아래와 같습니다.

```json
"include_files": [],
"library_sections": [
  {
    "path": "/home/eda/edk_cadence/sky130_release_0.1.0/models/sky130.lib.spice",
    "section": "tt"
  }
],
"ahdl_include_files": []
```

생성되는 testbench에는 TOP netlist보다 먼저 다음 줄이 들어갑니다.

```spectre
include "/home/eda/edk_cadence/sky130_release_0.1.0/models/sky130.lib.spice" section=tt
include "converter_example_top.scs"
```

만약 실습 환경의 sky130 경로가 다르면 `library_sections[0].path`만 실제 경로로 수정합니다. 추가로 include해야 하는 `.scs` 파일이 있으면 `include_files`에 넣고, Verilog-A 파일은 `ahdl_include_files`에 넣습니다.

`converter_example_top.scs` 작성이 틀려서 생기는 pin mapping 문제와, sky130 model을 못 읽는 include 문제는 별개의 문제입니다. pin mapping은 wrapper에서 해결하고, model 인식 문제는 `config_converter.json`의 include 설정으로 해결합니다.

## Converter 입력 전압/주파수 설정

사람마다 AC-DC converter에 넣는 입력 전압과 주파수 조건이 다를 수 있으므로, testbench netlist를 직접 고치지 말고 `config_converter.json`의 `converter_input`만 수정합니다.

기본 설정:

```json
"converter_input": {
  "kind": "sine",
  "dc_v": 0.0,
  "amplitude_v": 3.0,
  "frequency_hz": 100000.0,
  "file": ""
}
```

각 항목 의미:

- `kind`: converter 입력 source 종류
- `dc_v`: differential input의 DC offset
- `amplitude_v`: sine 입력의 peak amplitude. `VAC_P - VAC_N` 기준입니다.
- `frequency_hz`: sine 입력 주파수
- `file`: `kind`가 `pwl`일 때 사용할 PWL 파일 경로

지원 입력 모드:

```json
"converter_input": {
  "kind": "sine",
  "dc_v": 0.0,
  "amplitude_v": 2.5,
  "frequency_hz": 130000.0,
  "file": ""
}
```

```json
"converter_input": {
  "kind": "dc",
  "dc_v": 3.3,
  "amplitude_v": 0.0,
  "frequency_hz": 0.0,
  "file": ""
}
```

```json
"converter_input": {
  "kind": "pwl",
  "dc_v": 0.0,
  "amplitude_v": 0.0,
  "frequency_hz": 0.0,
  "file": "input_converter.pwl"
}
```

생성되는 Spectre source는 TOP의 converter 입력 포트에 다음처럼 연결됩니다.

```spectre
VAC (VAC_P VAC_N) vsource ...
XTOP (VIN VREF VAC_P VAC_N 0 VOUT) TOP
```

따라서 `CONVERTER` subckt 내부에서는 `VAC_P`와 `VAC_N` 사이의 differential voltage를 정류/승압/레귤레이션에 자유롭게 사용하면 됩니다.

## 초기조건으로 startup 시간 줄이기

converter가 storage capacitor나 regulator를 포함하면 0 V에서 정상 bias까지 올라오는 시간이 길 수 있습니다. 그래서 testbench는 `config_converter.json`의 `initial_conditions`를 읽어 Spectre `ic` statement를 자동으로 넣습니다.

기본 설정:

```json
"initial_conditions": {
  "enabled": true,
  "vdd_raw_v": 5.0,
  "vdd_core_v": 5.0,
  "vout_v": 2.5,
  "skipdc": false
}
```

각 항목 의미:

- `enabled`: `true`이면 testbench에 초기조건을 넣습니다.
- `vdd_raw_v`: `TOP` 내부 converter 출력 node `VDD_RAW` 초기 전압
- `vdd_core_v`: `TOP` 내부 `VDD_CORE` node 초기 전압
- `vout_v`: `VOUT` 초기 전압. 보통 `VREF`와 같은 common-mode 값으로 둡니다.
- `skipdc`: `true`이면 transient에 `skipdc=yes`를 추가합니다.

생성되는 netlist 예:

```spectre
ic XTOP.VDD_RAW=5.0 XTOP.VDD_CORE=5.0 VOUT=2.5
tran tran ... skipdc=yes
```

권장 기본값은 `enabled=true`, `skipdc=false`입니다. 이 경우 DC operating point를 계산하면서 지정 node가 원하는 bias 근처에서 시작하도록 도와줍니다. 회로가 DC operating point에서 자꾸 실패하거나 startup만 빠르게 보고 싶으면 `skipdc=true`를 시도할 수 있습니다.

주의: `ic`는 평가 편의를 위한 초기조건입니다. 회로가 실제로 converter만으로 `VDD_CORE`를 유지하지 못하면, steady 구간의 `VDD_CORE` 평균과 CORE power/similarity에서 그대로 드러납니다.

## 측정 원칙

TOP 전체를 transient simulation으로 돌립니다. 하지만 PPA 중 power와 area는 CORE만 측정합니다.

- 면적: 실제 `CORE` hierarchy의 `.scs` netlist에서 MOS/R/C/BJT/diode 면적을 우선 추정합니다. netlist에서 primitive 면적을 추정할 수 없으면 `devices_core.csv`를 fallback으로 사용합니다.
- static power: `VIN`을 DC common-mode로 고정한 static transient run에서 steady 구간 평균 `mean(abs(VDD_CORE * I_CORE))`를 사용합니다.
- total power: 기존 transient 입력을 넣은 active transient run에서 steady 구간 평균 `mean(abs(VDD_CORE * I_CORE))`를 사용합니다.
- dynamic power: `power_dynamic_w = max(power_total_w - power_static_w, 0)`로 계산합니다.
- `power_dynamic_raw_w = power_total_w - power_static_w`도 리포트에 기록합니다. 이 값이 0보다 작거나 같으면 active run 평균 전력이 static run보다 크지 않았다는 뜻이라 `power_dynamic_w`는 0으로 표시됩니다.
- AC response: COREONLY와 동일하게 `VIN`에 small-signal AC 입력을 넣고 `VOUT / VIN` 전달함수를 dB 보데 플롯으로 비교합니다.
- transient response: `VIN` transient 입력에 대한 `VOUT`을 target filter 예상 출력과 비교합니다.
- similarity: AC 보데 NRMSE와 transient 출력 NRMSE의 평균을 `1 / (1 + combined_NRMSE)`로 변환

CONVERTER의 면적/파워는 이 스크립트의 PPA 점수에 포함하지 않습니다.

CONVERTER static run에서도 converter 입력 `VAC_P - VAC_N` 조건은 active run과 동일하게 유지합니다. 즉 converter가 supply를 만드는 상태는 그대로 두고, CORE 입력 `VIN`만 DC로 고정해서 CORE input activity 때문에 늘어난 전력만 dynamic으로 분리합니다. `devices_core.csv`에서 추정한 static current 기반 값은 `power_static_estimate_w`로 기록되며, static transient CSV가 없을 때만 fallback으로 사용합니다.

`.ac` simulation에서는 converter 입력 `VAC_P - VAC_N`에 AC 성분을 넣지 않습니다. supply는 `initial_conditions`로 지정한 `VDD_RAW`, `VDD_CORE`, `VOUT` bias 근처에서 잡고, amplifier 입력인 `VIN`만 AC source로 사용합니다. 따라서 `.ac` 결과는 converter 입력 주파수 응답이 아니라 converter가 붙은 TOP에서 CORE가 보는 `VIN -> VOUT` 필터 응답입니다.

그래프는 COREONLY 테스트와 같은 PPA report legend를 우측 하단에 표시합니다. CONVERTER 테스트에서는 추가로 supply 확인용 그래프도 생성됩니다.

- `converter_ac_response.png`: `VIN -> VOUT` AC 보데 플롯입니다. 주파수축, dB scale이며 target response와 실제 response를 함께 표시합니다.
- `converter_transient_response.png`: `VIN AC`, `VOUT AC`를 서로 다른 패널로 분리한 transient 파형
- `converter_input_spectrum.png`: converter differential 입력 `VAC_P - VAC_N`의 주파수 스펙트럼을 표시합니다.
- `converter_ac_input_transient.png`: converter 입력 `VAC_P`, `VAC_N`, `VAC_P - VAC_N`을 시간축 서브그래프로 분리해서 표시합니다.
- `converter_supply_response.png`: converter differential 입력 `VAC_P - VAC_N`, converter 출력 `VDD_RAW`, CORE supply `VDD_CORE`를 서로 다른 서브그래프로 분리해서 표시합니다.
- `converter_core_power_breakdown.png`: CORE-only static/dynamic/total power

## 실행

netlist만 생성:

```sh
python3 ppa_converter.py gen-netlist --config ./config_converter.json
```

이미 `run/ac.csv`, `run/tran_core_static.csv`, `run/tran_core.csv`가 있으면 분석만 실행:

```sh
python3 ppa_converter.py analyze --config ./config_converter.json
```

Spectre/OCEAN까지 전체 실행:

```sh
python3 ppa_converter.py all --config ./config_converter.json
```

## 입력 CSV 형식

외부에서 AC CSV를 직접 넣을 경우 COREONLY와 같은 형식을 사용합니다. `ac.csv`는 주파수와 `VIN`, `VOUT` complex 값을 담아야 하며, OCEAN export는 자동으로 이 형식을 생성합니다.

외부에서 transient CSV를 직접 넣을 경우 아래 컬럼을 사용합니다.

```csv
time_s,vin_v,vout_v,vdd_raw_v,vdd_core_v,idd_core_a,vac_p_v,vac_n_v
```

`vac_p_v - vac_n_v`가 converter differential 입력 파형으로 표시됩니다. `vdd_raw_v`는 converter 출력 확인 그래프에 사용됩니다. `vdd_core_v`와 `idd_core_a`가 있어야 CORE-only power를 계산할 수 있습니다.

static power 분리를 위해 같은 형식의 `tran_core_static.csv`도 필요합니다. 이 파일은 `VIN`만 DC이고 converter 입력/supply 조건은 active transient와 같아야 합니다.

## 출력

`run/` 아래에 생성됩니다.

- `ppa_metrics.json`
- `ppa_report.log`
- `ppa_summary.log`
- `converter_core_ppa_metrics.json`
- `converter_core_ppa_report.log`
- `converter_ac_response.png`
- `converter_transient_response.png`
- `converter_input_spectrum.png`
- `converter_ac_input_transient.png`
- `converter_supply_response.png`
- `converter_core_power_breakdown.png`
- `tb_converter_ac.scs`
- `tb_converter_tran_static.scs`
- `tb_converter_tran.scs`
- `export_converter.ocn`

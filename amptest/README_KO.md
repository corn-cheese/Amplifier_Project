# 신경신호 증폭기 PPA 평가 스크립트 사용 설명서

이 폴더는 Cadence Spectre 시뮬레이션을 실행하고, 결과를 Python으로 분석해서 PPA 지표와 plot을 생성하는 도구입니다.

평가 대상 DUT는 다음 pin 순서를 갖는 black-box subckt로 가정합니다.

```text
VIN VREF VDD GND VOUT
```

기본 testbench 조건은 다음과 같습니다.

- `VDD = 5 V`
- `VREF = 0.5 * VDD = 2.5 V`
- `VIN DC = 0.5 * VDD = 2.5 V`
- `VIN AC amplitude = 1 mV`
- `CLOAD = 10 pF`
- AC sweep: `0.1 Hz` to `10 MHz`
- transient 입력: 기본 `1 kHz`, `1 mV` sine

## 1. 파일 구성

주요 파일은 다음과 같습니다.

- `ppa_wrapper.py`: Python 2/3 launcher. `python ppa_wrapper.py ...`로 실행해도 내부에서 Python 3를 찾아 실행
- `ppa_wrapper_core.py`: Spectre netlist 생성, Spectre/OCEAN 실행, CSV 분석, PNG/log 생성 실제 구현
- `config.example.json`: 실제 회로 평가용 설정 파일 template
- `devices.example.csv`: area/power 계산용 소자 목록 template
- `examples/veriloga_dummy/`: 검증용 Verilog-A 더미 회로와 실행 결과

검증용 예제 결과는 아래 경로에 들어 있습니다.

- `examples/veriloga_dummy/run/ppa_report.log`
- `examples/veriloga_dummy/run/ppa_metrics.json`
- `examples/veriloga_dummy/run/ac_response.png`
- `examples/veriloga_dummy/run/transient_response.png`
- `examples/veriloga_dummy/run/spectre_ac.log`
- `examples/veriloga_dummy/run/spectre_tran.log`

## 2. 실제 회로 평가 방법

먼저 `config.example.json`을 복사해서 자기 회로용 설정 파일을 만듭니다.

```sh
cp ppa_eval/config.example.json ppa_eval/my_amp_config.json
```

그 다음 `my_amp_config.json`에서 아래 항목을 수정합니다.

```json
{
  "design_name": "my_neural_amp",
  "work_dir": "run_my_amp",
  "dut_netlist": "path/to/my_amp.scs",
  "dut_subckt": "my_amp_subckt_name",
  "dut_pins_order": ["VIN", "VREF", "VDD", "GND", "VOUT"]
}
```

`dut_netlist`는 DUT subckt가 정의된 Spectre netlist입니다. 예를 들어 다음과 같은 형태여야 합니다.

```spectre
subckt my_amp_subckt_name VIN VREF VDD GND VOUT
...
ends my_amp_subckt_name
```

## 3. Library path를 config 파일 안에서 잡는 방법

PDK model, extra include, Verilog-A model 경로는 모두 `config.json` 안에서 직접 수정할 수 있습니다.

일반 Spectre include 파일을 추가하려면:

```json
"include_files": [
  "relative/or/absolute/path/to/extra_include.scs"
]
```

SKY130 model library처럼 section이 필요한 경우:

```json
"library_sections": [
  {
    "path": "/home/pdk/edk_cadence/sky130_release_0.1.0/models/sky130.lib.spice",
    "section": "tt"
  }
]
```

Verilog-A 파일을 testbench에서 직접 include해야 하는 경우:

```json
"ahdl_include_files": [
  "relative/or/absolute/path/to/model.va"
]
```

경로가 상대경로이면 `config.json`이 있는 폴더 기준으로 해석됩니다. 절대경로를 쓰면 그대로 사용합니다.

## 4. Area / Power 입력 파일

`devices.csv`에는 PPA 계산에 포함할 소자 목록을 적습니다. 예시는 `devices.example.csv`를 참고하면 됩니다.

지원하는 type:

- `opamp`: area = `1000 p`, `ft_hz`가 있으면 `Istatic = ft_hz * 7e-12`
- `capacitor`: `width * length * multiplier`
- `resistor`: `seg_length * seg_width * segments`
- `diode`: `width * length * multiplier`
- `npn`: area = `1 p * multiplier`
- `pnp`: area = `0.4624 p * multiplier`

가산점 블록처럼 PPA에서 제외할 항목은 `include_in_ppa`를 `false`로 둡니다.

netlist instance에 `m=`이 있으면 자동으로 추가 반영됩니다. 이때 `devices.csv`의 `name`과 netlist instance 이름이 같아야 합니다.

예를 들어 netlist에 다음 instance가 있고:

```spectre
XAMP VIN VREF VDD GND VOUT my_amp_cell m=4
```

`devices.csv`에 다음 행이 있으면:

```csv
XAMP,opamp,1,,,,,,,100meg,,true
```

면적은 OPAMP 1개가 아니라 `1 * m=4`로 계산되어 `4000 p`가 반영됩니다. OPAMP static current도 같은 방식으로 4배 반영됩니다.

`m=`, `mult=`, `multi=`, `multiplier=` 형식을 인식합니다. `devices.csv`의 `multiplier` 컬럼은 소자 자체의 geometry multiplier이고, netlist의 `m=`은 instance 복제 수로 별도 곱해집니다.

netlist instance가 vector/bus 형태이면 vector width도 자동 반영됩니다.

```spectre
XAMP<3:0> VIN VREF VDD GND VOUT my_amp_cell m=2
```

이 경우 `XAMP<3:0>`의 width는 4이고, `m=2`까지 곱해서 총 `8`개로 계산합니다. `devices.csv`의 `name`은 `XAMP` 또는 `XAMP<3:0>` 둘 다 인식됩니다. 대괄호 형태인 `XAMP[3:0]`도 같은 방식으로 처리합니다.

## 5. 실행 명령

권장 실행 명령은 `python3`입니다. 서버에서 `python`이 Python 2를 가리켜도 `ppa_wrapper.py`가 내부에서 `python3`를 찾아 다시 실행하도록 해두었습니다.

Spectre/OCEAN까지 모두 실행하고 분석하려면:

```sh
python3 ppa_eval/ppa_wrapper.py all --config ppa_eval/my_amp_config.json
```

testbench netlist만 생성하려면:

```sh
python3 ppa_eval/ppa_wrapper.py gen-netlists --config ppa_eval/my_amp_config.json
```

이미 `ac.csv`, `tran.csv`를 외부에서 받은 상태라 분석만 하려면:

```sh
python3 ppa_eval/ppa_wrapper.py analyze --config ppa_eval/my_amp_config.json
```

## 6. 출력 파일

`work_dir` 아래에 다음 파일이 생성됩니다.

- `tb_ac.scs`: AC simulation testbench
- `tb_tran.scs`: transient simulation testbench
- `export.ocn`: PSF 결과를 CSV로 export하는 OCEAN script
- `ac.csv`: AC 결과
- `tran.csv`: transient 결과
- `ppa_metrics.json`: 전체 metric JSON
- `ppa_report.log`: 사람이 읽기 쉬운 요약 log
- `ac_response.png`: target response와 실제 AC response plot
- `transient_response.png`: VIN/VOUT transient plot
- `spectre_ac.log`: Spectre AC log
- `spectre_tran.log`: Spectre transient log

## 7. 계산되는 주요 metric

AC:

- midband gain
- lower/upper `-3 dB` frequency
- passband ripple
- target response 대비 normalized RMSE
- 1 Hz, 200 kHz probe point에서 midband 대비 감쇄량

Transient / FFT:

- DC 평균값을 제거한 AC 성분 기준 target filter output 대비 transient NRMSE
- DC 평균값을 제거한 VOUT AC peak-to-peak
- 제거된 VIN/VOUT DC 평균값
- FFT 기반 THD, 2차부터 5차 harmonic 기준

Power:

- `Pdc = VDD * Istatic`
- `tran.csv`에 `IDD`가 있으면 `Pavg = mean(VDD * IDD)`
- 최종 power basis는 DC power와 dynamic power 중 큰 값

## 8. 검증용 Verilog-A 예제 실행

아래 명령으로 더미 Verilog-A 증폭기를 실제 Spectre/OCEAN으로 검증할 수 있습니다.

```sh
python3 ppa_eval/ppa_wrapper.py all --config ppa_eval/examples/veriloga_dummy/config.json
```

현재 검증 결과에서 Spectre log는 다음과 같이 완료되었습니다.

```text
spectre_ac.log:   spectre completes with 0 errors, 0 warnings
spectre_tran.log: spectre completes with 0 errors, 0 warnings
```

## 9. 주의사항

- Cadence license가 없거나 사용 중이면 `+lqtimeout` 시간을 늘리면 됩니다.
- `config.json`의 `sim.spectre_args`에 Spectre 추가 argument를 넣을 수 있습니다.
- OCEAN export는 현재 환경의 PSF signal 이름 `VIN`, `VOUT`, `VDD:p` 기준으로 생성됩니다.
- 실제 회로의 pin 이름이 다르면 DUT subckt wrapper를 만들어 `VIN VREF VDD GND VOUT`으로 맞추는 방식이 가장 안전합니다.

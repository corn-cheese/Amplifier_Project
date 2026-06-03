# Final Project: Single-Ended Neural Signal Amplifier Design

**마감:** 월요일 기준 오후 4:30  
**배점:** 0  
**제출물 유형:** 파일 업로드  
**파일 형식:** `.zip`  
**이용 가능 기간:** 4월 29일 오전 12:00 – 6월 10일 오후 4:30

---

## 0. 변경사항

- **(26.06.03)** 면적이 잘못 계산되고 있던 문제를 수정하였습니다. 폴더 안에 `runfile`이 업데이트될 예정입니다. `config.json`과 `.scs` 파일만 다시 수정해서 돌려주세요.
- **(26.05.31)** Clamp와 AC-DC converter까지 구현하는 경우, AMP 심볼과 분리해서 만든 다음 한 번 더 감싸서 테스트해주세요. 해당 블록들은 면적/파워 소모에 카운트하지 않습니다. 이 블록들에서 OPAMP를 사용하더라도 BJT 그룹으로 못 들어가는 것은 아닙니다.
- **(26.05.31)** OPAMP가 사용하는 power가 bias point 때문에 `VREF`를 쓰면 무시되는 경향이 있어서, `f_t` 조건의 숫자를 `7 × 10^-9`로 변경합니다. 기본 방향은 OPAMP는 쉽지만 PPA는 낮게, BJT는 어렵지만 PPA는 그만큼 높게 설정되도록 하는 것입니다.
- **(26.05.31)** OPAMP가 면적에서 무조건 유리하다는 의견이 많아서, 그룹을 나누어 면적 점수가 들어가도록 하겠습니다. BJT 그룹으로 채점하고, 그 다음 순위 OPAMP로 진행합니다.
- **(26.05.27)** 설계가 끝난 블록은 포트가 `VIN / VREF / VDD / GND / VOUT` 이렇게 5개가 되도록 만들어주세요. 월요일에 심볼을 만들어서 테스트하는 방법을 알려줄 예정입니다.
- **(26.05.27)** `VDD`, `VREF`, `VDCIN` 이외의 전압은 전부 R divider로 설정할 것.
- **(26.05.24)** OP AMP 조건을 추가합니다.
- **(26.05.24)** 동작 전압 조건이 너무 빡빡할 것 같아서 좀 더 완화합니다.
- **(26.05.19)** 저항 모델 시뮬레이션이 안 되는 현상이 발견되어 사용 소자를 변경합니다: `res_high_po_5p73`.

---

## 1. 프로젝트 목표

본 프로젝트의 목표는 주어진 소자만을 사용하여 **single-ended 신경신호 증폭기**를 설계하는 것입니다.

설계 대상은 작은 생체/신경 신호를 안정적으로 증폭하면서, 지정된 주파수 응답과 PPA 조건을 만족하는 **analog front-end amplifier**입니다.

입력 신호는 DC common-mode voltage 2.5 V 위에 작은 AC 신호가 실린 형태로 인가됩니다. 따라서 본 설계는 단순한 gain stage가 아니라, 다음 요소를 함께 고려하는 회로 설계 문제입니다.

- 작은 신호 증폭
- 목표 대역폭 구현
- Out-of-band noise 및 signal 억제
- 10 pF load 조건에서의 안정성
- Power / area 효율 최적화
- AC response 및 transient response 유사도 확보

---

## 2. 목표 사양

| 항목 | 목표 사양 |
|---|---|
| 구조 | Single-ended amplifier |
| Supply voltage | < 15 V |
| Reference voltage | 0.5 × VDD |
| VINDC | 0.5 × VDD |
| Input AC amplitude | 1 mV |
| Midband gain | 40 dB, 즉 약 100 V/V |
| Target bandwidth | 10 Hz – 20 kHz |
| Roll-off | 저주파 및 고주파 양쪽 pole 기준으로 약 80 dB/decade 감쇠 |
| Load capacitor | 10 pF |
| 평가 대상 | AC response 유사도 + transient response 유사도 |

Passband에서 1 mV 입력이 인가될 경우, 이상적으로는 약 100 mV 수준의 출력 신호가 2.5 V common-mode 기준으로 나타나야 합니다.

---

## 3. 사용 가능 소자

설계에는 아래에 명시된 소자만 사용할 수 있습니다.

| 소자 | 사용 가능 cell / model | 면적 계산 기준 |
|---|---|---|
| OPAMP | `ahdLib`의 `opamp` | npn transistor 면적의 1000배 |
| Capacitor | `sky130_fd_pr_main`의 `cap_vpp_11p5x11p7_m1m4_noshield` | width × length × multiplier |
| Resistor | `sky130_fd_pr_main`의 `res_high_po_5p73` | segment length × segment width × segments |
| Diode | `sky130_fd_pr_main`의 `diode_pd2nw_05v5` | width × length × multiplier |
| NPN BJT | `sky130_fd_pr_main`의 `npn_05v5` | area = 1p |
| PNP BJT | `sky130_fd_pr_main`의 `pnp_05v5` | area = 0.4624p |

OPAMP를 사용하는 경우, OPAMP 하나의 면적은 `npn_05v5` 면적의 1000배로 계산합니다.

예를 들어 `npn_05v5`의 면적을 1p로 정의하면, OPAMP 하나의 면적은 다음과 같이 계산합니다.

```text
A_OPAMP = 1000p
```

---

## 4. OPAMP 전류 및 ft 계산 기준

OPAMP의 static current는 선택한 `f_t`에 따라 다음 식으로 계산합니다.

```text
I_static = f_t × 7 × 10^-9
```

여기서 `f_t`는 Hz 단위로 입력합니다.

예를 들어,

```text
f_t = 1 GHz
```

이면,

```text
I_static = 1 × 10^9 × 7 × 10^-9 = 7 A
```

따라서 높은 `f_t`를 선택하면 bandwidth 및 transient response 측면에서는 유리할 수 있으나, static current와 power penalty가 증가합니다.

이외 정리된 조건표는 다음과 같습니다.

| ahdlib parameter | 제약조건 |
|---|---|
| ibias 또는 static current | 학생이 선택 |
| freq_unitygain (`f_t`) | ibias / (7 × 10^-9) |
| slew_rate | < 0.3 × `f_t` |
| gain | 60 dB 이하 내 Gain × Bandwidth 조건을 만족하는 아무 값 |
| rout | 10 Ω |
| rin | 1 MΩ |
| vin_offset | 1 mV |
| vsoft | 0.2 V |
| iin_max | ibias와 동일 |

---

## 5. 전압 및 입력 신호 조건

사용 가능한 기준 전압은 다음으로 제한합니다.

```text
VDD = 5 V
GND = 0 V
Middle reference = 2.5 V
```

입력 조건은 다음과 같습니다.

```text
VIN = 2.5 V + v_sig
```

AC simulation에서는 다음과 같은 작은 신호를 인가합니다.

```text
v_sig = 1 mV
```

Transient testbench에서는 별도로 제공되는 입력 파형에 대해 출력 응답을 비교합니다.

---

## 6. 면적 계산 기준

면적은 layout extraction 결과가 아니라, 주어진 산술식에 따라 단순 계산합니다.

전체 면적은 사용된 모든 소자의 면적 합으로 계산합니다.

```text
Area_total = Σ Area_device
```

OPAMP, capacitor, resistor, diode, BJT의 면적은 앞서 정의된 계산 기준을 따릅니다.

---

## 7. Power 계산 기준

Power는 다음 두 기준 중 설계자에게 불리한 값을 기준으로 평가합니다.

### 7.1 DC static current 기반 power

```text
P_DC = VDD × I_static
```

### 7.2 Periodic switching energy 적분 기반 power

```text
P_avg = (1/T) ∫_0^T VDD · IDD(t) dt
```

즉, static current가 큰 설계는 DC power 측면에서 불리하며, transient 동작 중 switching energy가 큰 설계는 dynamic power 측면에서 불리하게 평가됩니다.

---

## 8. Performance 평가 기준

Performance는 목표 `H(s)`와 실제 설계 결과의 유사도를 기준으로 평가합니다.

유사도는 **normalized RMSE**를 기준으로 판단하며, normalized RMSE가 낮을수록 목표 응답과 더 유사한 것으로 간주합니다.

### 8.1 AC response 평가

AC response에서는 다음 항목을 중심으로 평가합니다.

- Midband gain이 40 dB에 얼마나 가까운가
- Target bandwidth인 10 Hz – 20 kHz를 얼마나 잘 만족하는가
- 저주파 및 고주파 영역에서 약 80 dB/decade 감쇠 특성을 얼마나 잘 구현하는가
- Passband ripple 또는 gain flatness가 적절한가
- 10 pF load capacitor 조건에서 안정적으로 동작하는가

### 8.2 Transient response 평가

Transient response에서는 주어진 transient input waveform에 대해 출력 파형의 유사도를 평가합니다.

주요 평가 항목은 다음과 같습니다.

- 출력 amplitude 정확도
- Waveform shape 유사도
- Delay 및 settling behavior
- Overshoot, ringing, distortion 여부
- 2.5 V common-mode 기준에서의 정상 동작 여부

---

## 9. 점수 배점

총점은 기본 30점입니다.

| 항목 | 배점 |
|---|---:|
| Area | 8점 |
| Power | 8점 |
| Performance | 8점 |
| Presentation | 6점 |
| 총점 | 30점 |

Area, Power, Performance는 각각 PPA 비교를 통해 순위를 정합니다.

- Area는 낮을수록 유리합니다. 단, performance가 어느 정도 동작하는 경우에만 비교합니다.
- Power는 낮을수록 유리합니다. 단, performance가 어느 정도 동작하는 경우에만 비교합니다.
- Performance는 normalized RMSE가 낮을수록 유리합니다.

각 항목별 점수는 1등부터 12등까지 0.5점 간격으로 감소합니다.

| 순위 | 점수 |
|---:|---:|
| 1등 | 8.0 |
| 2등 | 7.5 |
| 3등 | 7.0 |
| 4등 | 6.5 |
| 5등 | 6.0 |
| 6등 | 5.5 |
| 7등 | 5.0 |
| 8등 | 4.5 |
| 9등 | 4.0 |
| 10등 | 3.5 |
| 11등 | 3.0 |
| 12등 | 2.5 |

Presentation 점수는 peer-review 점수를 기반으로 분포 변환하여 6점 만점으로 반영합니다.

---

## 10. 제출 내용

각 팀은 다음 내용을 `.zip` 파일로 압축하여 제출해야 합니다.

- 회로 schematic 및 testbench netlist
- 사용 소자 목록 및 면적 계산표 `.csv`
- Static current 및 power 계산 결과
  - 발표자료 안에 포함
- AC response plot `.png`
- Transient response plot `.png`
- 목표 `H(s)` 대비 설계 결과 분석
  - 발표자료 안에 포함
- PPA trade-off 설명
  - 발표자료 안에 포함
- `조번호_발표자료.pdf`

발표에서는 단순히 결과만 제시하는 것이 아니라, 다음 내용을 명확히 설명해야 합니다.

- 왜 해당 구조를 선택했는가
- Gain, bandwidth, power, area 사이의 trade-off를 어떻게 조절했는가
- 목표 `H(s)`와 비교했을 때 어떤 부분이 잘 맞고, 어떤 부분에서 차이가 발생하는가
- 10 pF load 조건에서 안정적으로 동작하는가
- Performance 향상을 위해 어떤 설계 선택을 했는가

---

## 11. 발표 진행 방식

프로젝트 발표는 월요일 / 수요일로 나누어 진행합니다.

- 총 6개 조가 발표합니다.
- 각 조의 발표 시간은 5분입니다.
- 발표 순서는 사전에 정하지 않고, 수업 시간에 랜덤으로 배정합니다.
- 모든 조는 본인 조가 언제 발표하더라도 바로 발표할 수 있도록 준비해야 합니다.
- 발표는 각 조가 사전에 제출한 PDF 발표자료를 기준으로 진행합니다.
- 발표 당일에는 제출된 PDF 파일을 사용하므로, 발표자료 제출 이후의 현장 수정본은 반영하지 않습니다.

### 발표 관련 유의사항

| 항목 | 내용 |
|---|---|
| 발표 시간 | 조별 5분 |
| 발표 일정 | 월요일 / 수요일 분할 진행 |
| 발표 순서 | 당일 랜덤 배정 |
| 발표자료 | 사전 제출한 PDF 파일 사용 |
| 발표 내용 | 설계 구조, 목표 사양 만족 여부, AC/transient response, area/power/performance trade-off 중심 설명 |
| 발표 점수 | Peer-review 점수를 기반으로 6점 만점으로 환산 |

---

## 12. 추가 옵션 및 가산점

기본 amplifier 설계 외에 아래의 추가 블록을 구현한 경우, 각 항목에 대해 가산점을 부여합니다.

| 추가 옵션 | 요구 조건 | 가산점 |
|---|---|---:|
| Power 관련 AC-DC converter | Ideal source로 5 Vp-p, 10 kHz AC 입력을 인가했을 때 5 V DC voltage를 출력 | +1점 |
| I/O protection 회로 | 입력 또는 출력 노드에 대해 0–5 V clamp 기능 구현 | +1점 |

추가 옵션은 최대 2점까지 인정합니다.

```text
최종 점수 = 기본 점수 30점 + 추가 옵션 가산점 최대 2점
```

따라서 최종 점수는 최대 32점까지 가능합니다.

단, 추가 옵션 블록은 amplifier의 PPA 평가에는 포함하지 않습니다. 즉, AC-DC converter 및 I/O protection 회로에 사용된 소자 면적과 power는 기본 amplifier의 Area / Power 순위 산정에 반영하지 않습니다.

다만, 가산점을 받기 위해서는 해당 블록이 실제로 동작함을 simulation 결과로 제시해야 합니다.

### 12.1 AC-DC converter 가산점 조건

AC-DC converter를 구현한 팀은 다음 조건을 만족해야 합니다.

- 입력: ideal AC voltage source
- 입력 크기: 5 Vp-p
- 입력 주파수: 10 kHz
- 목표 출력: 5 V DC voltage
- 출력 DC level이 안정적으로 유지되는 것을 transient simulation으로 확인
- 발표자료에 입력 파형, 출력 파형, 간단한 동작 원리 포함

### 12.2 I/O protection 회로 가산점 조건

I/O protection 회로를 구현한 팀은 다음 조건을 만족해야 합니다.

- 입력 또는 출력 노드에 대해 0–5 V clamp 기능을 제공
- 0 V 이하 또는 5 V 이상으로 과도한 전압이 인가될 때 clamp 동작을 확인
- Protection 회로가 정상 동작 범위의 amplifier 신호를 과도하게 왜곡하지 않아야 함
- 발표자료에 clamp 동작 simulation 결과 포함

---

## 13. 설계 방향

본 프로젝트는 “정답 회로”를 찾는 과제가 아니라, 제한된 소자와 전력/면적 조건에서 목표 신경신호 증폭기 사양을 가장 효율적으로 만족하는 설계를 찾는 과제입니다.

좋은 설계는 다음 조건을 균형 있게 만족해야 합니다.

- 작은 1 mV 입력 신호를 안정적으로 증폭
- 40 dB gain 확보
- 10 Hz – 20 kHz 대역 유지
- Out-of-band signal 및 noise 억제
- 10 pF load에서 안정 동작
- 낮은 power
- 작은 area
- 명확한 설계 근거와 발표 설명
- 추가 옵션 구현 시, amplifier 본 설계와 독립적으로 동작 검증 자료 제시

최종적으로 중요한 것은 단순히 가장 높은 gain을 얻는 것이 아니라, 목표 `H(s)`, transient response, power, area를 종합적으로 고려한 균형 잡힌 analog front-end 설계를 제시하는 것입니다.

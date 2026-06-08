# AMPTEST V3

CORE 단독 테스트와 AC-DC converter + CORE TOP 테스트를 분리한 버전입니다.

V3의 power 측정은 static/active transient를 분리합니다.

- static power: `VIN`을 DC로 고정한 static transient run의 steady 평균 전력
- total power: 기존 입력을 넣은 active transient run의 steady 평균 전력
- dynamic power: `max(total - static, 0)`

```text
COREONLY/   core-only amplifier 테스트
CONVERTER/ converter가 CORE supply를 생성하는 TOP 테스트
```

실행 결과는 각 하위 폴더의 `run/` 아래에 새로 생성됩니다. 배포 폴더에는 이전 run 결과와 Spectre dump를 포함하지 않습니다.

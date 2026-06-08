# 테스트 폴더 구분

앞으로 아날로그 PPA 테스트는 아래 두 폴더를 기준으로 사용합니다.

```text
COREONLY/   기존 CORE 단독 증폭기 테스트
CONVERTER/ AC-DC converter + CORE를 붙인 TOP 테스트
```

## COREONLY

지금까지 작업한 core-only 테스트 파일을 모아둔 폴더입니다.

```sh
cd COREONLY
python3 ppa_wrapper.py analyze --config ./config.json
```

## CONVERTER

AC-DC converter가 CORE supply를 생성하는 TOP 회로 테스트 폴더입니다.

```sh
cd CONVERTER
python3 ppa_converter.py gen-netlist --config ./config_converter.json
python3 ppa_converter.py all --config ./config_converter.json
```

CONVERTER flow에서 TOP 전체 transient를 보지만, PPA power/area는 CORE만 계산합니다.

고정 subckt와 포트는 `CONVERTER/README_KO.md`를 기준으로 합니다.

# Project Instructions

이 파일은 이 저장소에서 작업하는 Codex와 팀원이 따라야 할 공통 규칙입니다.

## 프로젝트 범위

- 현재 새 프로젝트에서 완료된 분석은 `code/02_population_estimation`입니다.
- `code/01_eda`와 `code/07_dashboard`는 새 프로젝트 기준으로 다시 구현할 공간입니다.
- 이전 접근성, 선호도, 취약권역, 대시보드, OCI 코드는 `legacy/`에만 보존합니다.
- 새 코드는 `legacy/`의 파일을 입력이나 기준 결과로 사용하지 않습니다.

## 고정 폴더 구조

```text
data/
  raw/
    population/
    culture_facilities/
    mnc_merchants/
    spatial/
    network/
  processed/
    spatial/
    estimated_population/
    estimated_target_population/
    external_population/
  dashboard/
  metadata/
code/
  01_eda/
  02_population_estimation/
  07_dashboard/
output/
  image/
  report/
  doc/
legacy/
```

새 폴더를 만들기 전에 위 폴더 중 적절한 위치가 있는지 먼저 확인합니다. 새로운 분석 단계가 확정되지 않았다면 번호 폴더를 임의로 추가하지 않습니다.

## 데이터 저장 규칙

- `data/raw`: 원본 데이터입니다. 파일 내용과 파일명을 임의로 수정하지 않습니다.
- `data/processed`: 코드로 재생성할 수 있는 분석용 데이터만 저장합니다.
- `data/dashboard`: 대시보드가 직접 읽는 최소한의 경량 데이터만 저장합니다.
- `output`: 사용자가 명시적으로 요청한 이미지, 보고서, 문서만 저장합니다.
- `legacy`: 과거 프로젝트 보존용입니다. 새 분석에서 참조하거나 수정하지 않습니다.
- 원본 데이터와 대용량 처리 결과는 Git에 추가하지 않습니다.
- 임시 파일은 프로젝트 루트에 남기지 않습니다.

## 코드 작성 규칙

- 기존 사용자의 읽기 쉬운 코딩 스타일과 변수명 흐름을 유지합니다.
- 노트북은 분석 순서대로 번호를 붙입니다.
- 절대경로를 하드코딩하지 않고 `pathlib`과 프로젝트 루트 기준 상대경로를 사용합니다.
- 코드와 마크다운을 분석 순서대로 배치합니다.
- 각 주요 단계에는 한국어 마크다운 소제목을 둡니다.
- 주요 전처리와 분석 결과는 노트북 출력으로 확인하고, 짧은 요약형 표현을 사용합니다.
- 필요한 중간 품질검사를 유지합니다: 행 수, 중복, 결측, 총량, 키 정합성, CRS, 공간 범위.
- 불필요한 보조 테이블과 이미지를 생성하지 않습니다.
- 그래프는 확인에 필요할 때 노트북에 출력하고, 요청받기 전에는 파일로 저장하지 않습니다.
- 기존 결과를 덮어쓸 때는 입력 경로와 출력 경로가 올바른지 먼저 확인합니다.

## 분석 작업 절차

1. `README.md`와 `data/metadata/data_metadata.md`를 확인합니다.
2. 입력 데이터의 존재 여부, 컬럼, 행 수와 좌표계를 점검합니다.
3. 앞 단계의 처리 결과를 읽어 순서대로 분석합니다.
4. 전처리 직후 결측·중복·총량을 확인합니다.
5. 결과 저장 후 다시 읽어 행 수와 핵심 합계를 검증합니다.
6. 변경한 노트북 또는 코드만 실행해 오류가 없는지 확인합니다.
7. 예상하지 못한 입력 누락이나 기존 결과와의 차이가 발견되면 작업을 중단하고 사용자에게 알립니다.

## 인구추정 실행 순서

1. `01_prepare_base_grid.ipynb`
2. `02_estimate_total_population_seoul.ipynb`
3. `03_estimate_target_population_seoul.ipynb`
4. `04_estimate_sex_age_population_seoul.ipynb`
5. `05_estimate_disabled_population_seoul.ipynb`
6. `06_estimate_population_external_area.ipynb`

## Git 규칙

- 코드, 문서, 폴더 구조용 `.gitkeep`만 커밋합니다.
- `data`의 실제 원본·처리 파일, `legacy`, 임시 파일, 인증 파일은 커밋하지 않습니다.
- 작업 전 `git status`로 기존 변경을 확인하고 다른 팀원의 변경을 되돌리지 않습니다.
- 커밋 전 변경한 코드의 실행 결과와 `git diff --check`를 확인합니다.
- 구조 변경이나 다수 파일 삭제는 PR 설명에 이유와 이동 위치를 적습니다.

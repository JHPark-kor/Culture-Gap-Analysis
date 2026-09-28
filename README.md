# Oracle MNC Project

문화누리카드 정책 분석을 위한 공간 데이터 프로젝트입니다. 현재 새 구조에서는 격자별 인구와 문화누리 대상인구 추정 단계까지 재구현했습니다.

## Project Structure

```text
oracle_mnc_project/
├─ data/
│  ├─ raw/
│  │  ├─ population/
│  │  ├─ culture_facilities/
│  │  ├─ mnc_merchants/
│  │  ├─ spatial/
│  │  └─ network/
│  ├─ processed/
│  │  ├─ spatial/
│  │  ├─ estimated_population/
│  │  ├─ estimated_target_population/
│  │  ├─ external_population/
│  │  └─ accessibility/
│  ├─ dashboard/
│  └─ metadata/
│     └─ accessibility/
├─ code/
│  ├─ 01_eda/
│  ├─ 02_population_estimation/
│  ├─ 03_accessibility/
│  └─ 07_dashboard/
├─ output/
│  ├─ image/
│  ├─ report/
│  │  └─ accessibility/
│  └─ doc/
└─ legacy/
```

## Current Status

- `code/02_population_estimation`: 재구현 및 실행 검증 완료
- `code/03_accessibility`: 거리·경사·전일 중앙배차를 반영한 고정 접근성 파이프라인 및 실행 검증 완료
- `code/01_eda`: 새 프로젝트 기준으로 추후 재구현
- `code/07_dashboard`: 새 프로젝트 기준으로 추후 재구현
- `legacy`: 이전 접근성·선호도·대시보드·OCI 분석 자료의 로컬 보관 위치

## Population Estimation

노트북은 아래 순서로 실행합니다.

1. `01_prepare_base_grid.ipynb`
2. `02_estimate_total_population_seoul.ipynb`
3. `03_estimate_target_population_seoul.ipynb`
4. `04_estimate_sex_age_population_seoul.ipynb`
5. `05_estimate_disabled_population_seoul.ipynb`
6. `06_estimate_population_external_area.ipynb`

주요 산출물은 `data/processed`의 주제별 폴더에 저장됩니다. 노트북은 중간 품질검사와 결과 요약을 화면에 출력하며 시각자료를 자동 저장하지 않습니다.

## Accessibility Analysis

접근성 파이프라인은 서울 100m 격자 60,528개에서 공연 및 스포츠관람 시설까지의 도보·대중교통 경로를 계산합니다. 거리, DEM 기반 절대경사각, 환승 횟수와 전일 배차간격 중앙값 기반 기대대기시간을 동일 가중치로 결합합니다.

- 코드와 실행 순서: `code/03_accessibility/README.md`
- 원본·전처리 자료: `data/raw/spatial/accessibility/`, `data/processed/accessibility/`
- 데이터 인벤토리와 검증 기록: `data/metadata/accessibility/`
- 최종 격자·분야 접근성 표: `output/report/accessibility/`

전시는 현재 자료 확보 전 단계이므로 결과에서 제외했습니다.

## Data Policy

- 원본 및 처리 데이터는 원칙적으로 Git에 올리지 않습니다.
- 고정 접근성 분석의 소용량 원본·핵심 전처리 자료와 최종 표는 `data/**/accessibility/`, `output/report/accessibility/`에서 예외적으로 관리합니다.
- 수백 MB~GB 규모의 OSM, GTFS 정차 원본과 격자-시설 경로 Parquet는 Git에서 제외하고 `data/metadata/accessibility/data_inventory.md`에 기록합니다.
- 데이터 폴더 구조와 `data/metadata` 문서만 Git에서 관리합니다.
- 발표용 이미지·보고서·문서는 필요할 때만 `output`에 저장합니다.
- `legacy`는 이전 프로젝트 자료 보존용이며 현재 분석 코드에서 참조하지 않습니다.
- `.env`, OCI Wallet, 비밀번호와 인증 파일은 Git에 커밋하지 않습니다.

## Environment

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

데이터 구성은 `data/metadata/data_metadata.md`에서 확인할 수 있습니다.

팀 작업 규칙은 `CONTRIBUTING.md`, Codex 작업 규칙은 `AGENTS.md`를 따릅니다.

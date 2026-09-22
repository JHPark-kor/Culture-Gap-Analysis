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
│  │  └─ external_population/
│  ├─ dashboard/
│  └─ metadata/
├─ code/
│  ├─ 01_eda/
│  ├─ 02_population_estimation/
│  └─ 07_dashboard/
├─ output/
│  ├─ image/
│  ├─ report/
│  └─ doc/
└─ legacy/
```

## Current Status

- `code/02_population_estimation`: 재구현 및 실행 검증 완료
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

## Data Policy

- 원본 및 처리 데이터는 Git에 올리지 않습니다.
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

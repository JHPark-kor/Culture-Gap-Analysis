# EDA·관람의향 분석

최종 보고서의 1~2절을 원자료에서 재현한다. 지역별 고령인구 추정과 취약점수 분석은 포함하지 않는다.

## 실행 순서
1. `01_culture_nuri_usage_eda.ipynb`
2. `02_income_participation_intention_eda.ipynb`
3. `03_prepare_intention_model_data.ipynb`
4. `04_intention_and_income_gap_owen.ipynb`
5. `05_bootstrap_hypothesis_test.ipynb`
6. `06_build_readme_figures.ipynb`

## 원자료 위치
```text
data/raw/mnc_merchants/card_usage/mnc_seoul_usage_issuance_2021_2025.xlsx
data/raw/culture_survey/national_culture_arts_activity/2023_*.csv
data/raw/culture_survey/national_culture_arts_activity/2024_*.csv
data/raw/culture_survey/national_culture_arts_activity/2025_*.csv
```

## 결과 위치
- 처리 데이터: `data/processed/eda/`
- README용 그래프: `output/image/readme/`

EDA 표본은 전 연령 30,325명, 장르별 모형 표본은 만 20세 이상 28,779명이다. `저소득 집단`은 조사연도·가구원 수·가구소득 구간을 이용한 분석용 근사집단이며 실제 문화누리카드 자격 판정과 다르다.

4번은 총 16,384개 부분모형을 적합한다. 5번 보고서 기준은 부트스트랩 100회다. 두 분해 결과는 인과효과가 아니다.

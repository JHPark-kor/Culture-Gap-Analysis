# Data Metadata


## Raw Data

| Path | Description |
| --- | --- |
| `data/raw/population/` | 총인구, 성·연령별 인구, 기초생활수급자, 차상위계층, 장애인·고령자 등 인구 원자료 |
| `data/raw/culture_facilities/` | 일반 문화·체육·관광시설 및 공급량 관련 원자료 |
| `data/raw/mnc_merchants/` | 문화누리카드 가맹점, 카드 이용실적, 후보지 원자료 |
| `data/raw/spatial/` | 100m·500m 격자, 행정구역 경계, 수치지도, DEM, 공간 참조코드 |
| `data/raw/network/` | 버스정류소와 지하철역 등 교통 네트워크 원자료 |

## Processed Data

| Path | Main Outputs |
| --- | --- |
| `data/processed/spatial/` | 서울시 100m 격자·행정동 기본 테이블 |
| `data/processed/estimated_population/` | 서울시 500m 총인구와 100m 추정인구 |
| `data/processed/estimated_target_population/` | 서울시 문화누리 대상인구 및 성·연령·장애별 추정 결과 |
| `data/processed/external_population/` | 인천·경기 외부 25km 지역의 총인구와 문화누리 대상인구 추정 결과 |

## Dashboard Data

`data/dashboard/`는 향후 새 대시보드에서 직접 읽을 경량 데이터만 저장합니다. 분석 중간 산출물을 복사하지 않고, 대시보드 구축 코드가 필요한 열과 공간 객체만 별도로 생성하도록 설계합니다.

## Notes

- 선호도 조사와 기존 접근성·취약권역 산출물은 현재 프로젝트 범위에서 제외했습니다.
- 제외된 이전 자료는 프로젝트 루트의 `legacy/`에 보존합니다.
- 새 분석 코드는 `legacy/`의 파일을 입력으로 사용하지 않습니다.
- 처리 데이터는 `code/02_population_estimation`의 노트북 실행 순서에 따라 생성합니다.

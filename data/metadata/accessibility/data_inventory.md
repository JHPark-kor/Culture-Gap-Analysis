# 고정 접근성 분석 데이터 인벤토리

이 문서는 고정 접근성 분석에서 사용한 자료를 Git 추적 대상과 로컬 전용 대용량 자료로 구분한다. 전시는 현재 분석에서 제외했다.

## Git에 포함한 자료

| 구분 | 경로 | 내용 |
| --- | --- | --- |
| 원본 시설 | `data/raw/spatial/accessibility/facilities/` | 공연시설 원본·서울 추출본, 대형 스포츠관람시설 위경도 |
| 원본 지형 | `data/raw/spatial/accessibility/dem/` | 서울 DEM 4개 ZIP 타일 |
| 서울 경계 | `data/raw/spatial/accessibility/boundary/seoul_gu.json` | GTFS 서울 운행 필터링 경계 |
| 시설 전처리 | `data/processed/accessibility/facilities/` | 동일 주소·좌표 공연시설 통합 결과, 원본-통합 매핑, 공연·스포츠 시설 마스터 |
| 취약노인 인구 | `data/processed/accessibility/population/grid_senior_population_score.csv` | 격자별 기존 취약노인수; 접근성 단계에서 재추정하지 않음 |
| 네트워크 스냅 | `data/processed/accessibility/network_snap/` | 취약노인수가 있는 21,263개 격자와 361개 시설의 보행망 최근접 노드 |
| 배차 전처리 | `data/processed/accessibility/transit/` | 노선 및 노선 방향 패턴별 전일 배차간격 중앙값 |
| 최종 결과 | `output/report/accessibility/` | 격자당 한 행의 전체 시설 기준 충족 시설 수, beta=2·3·4 접근성, beta=3 합계의 min-max 0~100점과 1-min-max 취약도 |
| 처리 기록 | `data/metadata/accessibility/` | 단계별 생성·검증 요약과 고정 교통부담 산식 |
| 코드 | `code/03_accessibility/` | 전처리, 네트워크 경로, 접근성 계산, 독립 검증 코드 |

`grid_walk_node_snap.csv`는 기존 `grid_pop_access.csv`에서 `GRID_CD`, 행정구역, 중심점 좌표만 읽고 새 `grid_senior_population_score.csv`의 `취약노인수`를 결합한 뒤 `취약노인수 > 0`인 격자만 보행망에 스냅한 결과다. 기존 파일의 과거 접근성 열은 사용하거나 복사하지 않았다.

## Git에서 제외한 대용량 자료

| 파일 또는 폴더 | 대략적 크기 | 제외 이유 |
| --- | ---: | --- |
| `data/raw/spatial/accessibility/network/seoul_walk.graphml` | 199 MB | 서울 전역 OSM 보행망 원본 |
| `outputs/fixed_accessibility_inputs/terrain/seoul_walk_dem4_slope_elderly.graphml` | 359 MB | DEM·경사·노인 보행시간이 결합된 파생 그래프 |
| `data/raw/transport/seoul_gtfs/stop_times.txt` | 1.64 GB | GTFS 대용량 정차 원본 |
| `transit_paths/transit_grid_facility_paths_90min_walk15min.parquet` | 약 502 MB | 격자-시설 대중교통 후보 경로 |
| `final_accessibility/grid_facility_best_paths_mode_limits.parquet` | 약 314 MB | 격자-시설별 최종 선택 경로 |
| `walk_paths/walk_grid_facility_paths_20min.parquet` | 약 1.5 MB | 격자-시설 도보 경로 중간 산출물 |
| 그 밖의 `outputs/fixed_accessibility_inputs/` Parquet·GraphML | 수 MB~수십 MB | 코드로 재생성 가능한 중간 산출물 |

위 자료는 일반 Git에 넣지 않는다. 공유가 필요하면 Git LFS 또는 팀 공유 저장소를 사용하고, 저장소에는 동일한 상대경로로 배치한다.

## 실행 순서

1. `deduplicate_seoul_show_facilities.py`
2. `build_seoul_terrain_graph.py`
3. `build_seoul_gtfs_cost_inputs.py`
4. `build_accessibility_network_snaps.py`
5. `build_walk_accessibility_paths.py`
6. `build_static_transit_network.py`
7. `build_transit_walk_connectors.py`
8. `build_transit_accessibility_paths.py`
9. `validate_transit_accessibility_paths.py`
10. `build_best_paths_and_accessibility.py`
11. `validate_final_accessibility.py`

상세 산식과 단계별 결과는 `code/03_accessibility/README.md`를 본다. 기본 경로가 없는 컴퓨터에서는 각 스크립트의 `--help`에 표시되는 입력 옵션으로 로컬 원본 위치를 지정한다.

## 최종 결과 검증 기준

- 전체 입력 격자: 60,528개
- 분석 격자: `취약노인수 > 0`인 21,263개
- 내부 계산 분야: 공연, 스포츠관람
- 최종 격자 행: 21,263개(`GRID_CD`당 1행, `facility_category=전체`)
- 공연시설: 동일 주소·좌표 기준 357개
- 스포츠관람시설: 4개
- 전시시설: 자료 확보 전까지 제외
- 배차 기준: 시간대 구분 없는 전일 출발간격 중앙값
- 도보 경로 상한: 20분
- 대중교통 경로 상한: 총보행 15분, 기대대기 포함 전체 90분
- 최종 수단 선택: 도보 20분 경로가 있으면 도보 우선, 없으면 대중교통
- 접근성 민감도: beta=2, 3, 4
- 전체 시설 종합점수: `Σ exp(-3G)`를 분석 격자의 관측 최솟값·최댓값으로 min-max 정규화; 접근 불가 시설 기여도 0

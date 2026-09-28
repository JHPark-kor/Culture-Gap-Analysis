# 고정 접근성 분석 데이터 인벤토리

이 문서는 고정 접근성 분석에서 사용한 자료를 Git 추적 대상과 로컬 전용 대용량 자료로 구분한다. 전시는 현재 분석에서 제외했다.

## Git에 포함한 자료

| 구분 | 경로 | 내용 |
| --- | --- | --- |
| 원본 시설 | `data/raw/spatial/accessibility/facilities/` | 공연시설 원본·서울 추출본, 대형 스포츠관람시설 위경도 |
| 원본 지형 | `data/raw/spatial/accessibility/dem/` | 서울 DEM 4개 ZIP 타일 |
| 서울 경계 | `data/raw/spatial/accessibility/boundary/seoul_gu.json` | GTFS 서울 운행 필터링 경계 |
| 시설 전처리 | `data/processed/accessibility/facilities/` | 동일 주소·좌표 공연시설 통합 결과, 원본-통합 매핑, 공연·스포츠 시설 마스터 |
| 네트워크 스냅 | `data/processed/accessibility/network_snap/` | 60,528개 격자와 361개 시설의 보행망 최근접 노드 |
| 배차 전처리 | `data/processed/accessibility/transit/` | 노선 및 노선 방향 패턴별 전일 배차간격 중앙값 |
| 최종 결과 | `output/report/accessibility/` | 격자·분야별 30분/60분 시설 수와 beta=2·3·4 접근성, 민감도 결과 |
| 처리 기록 | `data/metadata/accessibility/` | 단계별 생성·검증 요약과 고정 교통부담 산식 |
| 코드 | `code/03_accessibility/` | 전처리, 네트워크 경로, 접근성 계산, 독립 검증 코드 |

`grid_walk_node_snap.csv`는 기존 `grid_pop_access.csv`에서 `GRID_CD`, 행정구역, 중심점 좌표만 읽어 새 보행망에 스냅한 결과다. 기존 파일의 과거 접근성 열은 사용하거나 복사하지 않았다.

## Git에서 제외한 대용량 자료

| 파일 또는 폴더 | 대략적 크기 | 제외 이유 |
| --- | ---: | --- |
| `data/raw/spatial/accessibility/network/seoul_walk.graphml` | 199 MB | 서울 전역 OSM 보행망 원본 |
| `outputs/fixed_accessibility_inputs/terrain/seoul_walk_dem4_slope_elderly.graphml` | 359 MB | DEM·경사·노인 보행시간이 결합된 파생 그래프 |
| `data/raw/transport/seoul_gtfs/stop_times.txt` | 1.64 GB | GTFS 대용량 정차 원본 |
| `transit_paths/transit_grid_facility_paths_60min.parquet` | 1.08 GB | 격자-시설 대중교통 후보 경로 |
| `final_accessibility/grid_facility_best_paths_60min.parquet` | 635 MB | 격자-시설별 최종 선택 경로 |
| `walk_paths/walk_grid_facility_paths_60min.parquet` | 43 MB | 격자-시설 도보 경로 중간 산출물 |
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

- 분석 격자: 60,528개
- 분야: 공연, 스포츠관람
- 최종 격자·분야 행: 121,056개
- 공연시설: 동일 주소·좌표 기준 357개
- 스포츠관람시설: 4개
- 전시시설: 자료 확보 전까지 제외
- 배차 기준: 시간대 구분 없는 전일 출발간격 중앙값
- 접근성 민감도: beta=2, 3, 4

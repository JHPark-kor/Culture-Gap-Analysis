# 고정 접근성 분석 파이프라인

이 문서는 로컬의 전체 생성 결과(`outputs/fixed_accessibility_inputs/`)를 기준으로 각 단계를 설명한다. Git에는 최종·핵심 자료만 `data/**/accessibility/`, `output/report/accessibility/`, `data/metadata/accessibility/`에 선별해 포함했다. 포함·제외 목록은 `data/metadata/accessibility/data_inventory.md`에 정리했다.

접근성 산식은 요청한 안으로 고정했다. 대중교통 배차는 별도의 시간대 조건을 두지 않고, 각 노선 방향 패턴의 **전일 연속 출발 간격 중앙값**을 사용한다.

분석 출발 격자는 `grid_senior_population_score.csv`의 기존 `취약노인수`를 사용해 `취약노인수 > 0`인 격자만 남긴다. 접근성 단계에서는 인구를 새로 추정하지 않는다. 경로 허용 기준은 도보 20분, 대중교통 접근·환승·하차 보행 합계 15분, 기대대기를 포함한 전체 대중교통 여정 90분이다.

## 1. 서울 전역 보행망·경사

- `terrain/seoul_dem_4tiles_2025_epsg5179.tif`: DEM 4개 타일 모자이크
- `terrain/seoul_walk_dem4_slope_elderly.graphml`: 고도·경사·노인 보행시간이 붙은 서울 보행 그래프
- `terrain/seoul_walk_nodes_elevation.parquet`: 노드별 고도
- `terrain/seoul_walk_edges_terrain.parquet`: 엣지별 고도차, 경사, 절대경사각, 60m/min 보행시간
- `terrain/terrain_build_summary.json`: 처리 범위와 검증 통계

경사 부담은 `clip((절대경사각 - 2) / 6, 0, 1)`로 계산한다. 오르막과 내리막을 모두 반영하며, 8도 이상 여부도 별도 필드로 저장했다.

## 2. 서울 운행 GTFS·전일 중앙 배차

- `transit/seoul_stop_times.parquet`: 서울 정류장을 지나는 버스·도시철도 운행의 정차기록
- `transit/route_pattern_headways_all_day.csv`: 노선 방향 패턴별 전일 중앙 배차와 기대 최초 대기시간
- `transit/route_pattern_stops.parquet`: 패턴별 정류장 순서
- `transit/trip_route_patterns.parquet`: 운행과 노선 방향 패턴 연결표
- `transit/seoul_gtfs_analysis_subset/`: 분석 대상 노선·운행·정류장 메타데이터

전일 중앙 배차를 직접 구할 수 없는 패턴은 같은 노선 중앙값, 그마저 없는 경우 전체 중앙값으로 보완했으며 보완 출처는 `headway_source`에 남겼다.

## 3. 환승·기대대기 비용

- `transit/route_pattern_transfer_wait_edges.parquet`: 환승 가능한 노선 방향 패턴 쌍, 환승 1회, 다음 노선 중앙 배차와 기대대기시간
- `transit/transit_cost_methodology.json`: 고정 산식
- `code/03_accessibility/calculate_transit_burden.py`: 경로의 패턴 ID 순서를 받아 최종 교통부담을 계산하는 실행 도구

경로가 탑승하는 패턴 수를 `n`, 각 패턴의 중앙 배차를 `h_k`라 하면 다음을 적용한다.

- 환승 횟수: `max(n - 1, 0)`
- 총 기대대기시간: `sum(h_k / 2)` (최초 탑승 포함)
- 환승 부담: `min(환승 횟수 / 3, 1)`
- 대기 부담: `min(총 기대대기시간 / 20분, 1)`
- 교통 부담: `0.5 × 환승 부담 + 0.5 × 대기 부담`

도보만 사용하는 경로는 교통 부담을 0으로 처리한다. 격자-시설별 실제 경로가 생성되면 그 경로의 패턴 ID 순서에 위 도구를 적용한다.

## 4. 격자·시설·정류장 보행망 연결

- `network_snap/facility_master_available.csv`: 현재 확보된 공연 357개와 스포츠관람 4개 시설 마스터
- `network_snap/grid_walk_node_snap.parquet`: 취약노인수가 있는 서울 100m 격자 21,263개와 최근접 보행 노드
- `network_snap/facility_walk_node_snap.parquet`: 시설 361개와 최근접 보행 노드
- `network_snap/transit_stop_walk_node_snap.parquet`: GTFS 정류장과 최근접 보행 노드
- `network_snap/network_snap_summary.json`: 스냅거리 분포와 입력 범위

전체 60,528개 격자 중 `취약노인수 = 0`인 39,265개를 제외했다. 남은 격자 중심점과 시설 좌표를 EPSG:5179 보행망의 최근접 노드에 연결했다. 스냅구간 보행시간은 60m/min으로 계산한다. GTFS 부분집합에는 서울 밖까지 이어지는 노선의 정류장도 포함되므로 보행망 1km 이내 여부를 별도 필드로 표시했다.

## 5. 도보 경로 원시 결과

- `walk_paths/walk_grid_facility_paths_20min.parquet`: 도보 20분 이내 격자-시설 실제 네트워크 경로
- `walk_paths/walk_grid_category_summary.csv`: 격자·분야별 20분 이내 시설 수와 최근접 도보 비용
- `walk_paths/walk_path_build_summary.json`: 경로 산식과 검증 통계

경로별 총거리는 격자 스냅거리, 네트워크 최단거리, 시설 스냅거리의 합이다. 경사부담은 네트워크 엣지의 절대경사각을 길이로 가중평균해 계산한다. 스냅구간은 DEM 경사를 알 수 없어 경사 평균에서는 제외하고 거리에는 포함한다.

현재 시설 마스터에는 공연과 스포츠관람만 포함된다. 전시시설 전용 위경도 파일은 아직 제공되지 않아 전시 분야는 미산출 상태로 남긴다.

## 6. 정적 대중교통 경로망

- `transit_network/transit_pattern_stop_states.parquet`: 노선 방향 패턴과 정류장 경유 순서를 구분한 상태 노드
- `transit_network/transit_pattern_ride_edges.parquet`: 연속 정류장 사이의 시간표상 차내 이동 엣지
- `transit_network/transit_pattern_boarding_edges.parquet`: 전일 중앙 배차의 절반을 적용한 최초 탑승 엣지
- `transit_network/transit_pattern_state_transfer_edges.parquet`: 반복 경유 정류장 순서를 보존한 환승 엣지
- `transit_network/static_transit_network_summary.json`: 노드·엣지 수와 운행시간 검증 통계

정류장 상태 111,363개, 운행 엣지 109,406개, 최초 탑승 엣지 111,363개, 환승 엣지 2,678,644개를 생성했다. 운행시간 음수, 상태·운행 엣지 중복, 필수값 결측은 없다.

## 7. 대중교통 접근·하차 보행 연결

- `transit_connectors/grid_to_transit_stop_walk_900m.parquet`: 격자에서 정류장까지 최대 15분 방향성 보행경로
- `transit_connectors/transit_stop_to_facility_walk_900m.parquet`: 정류장에서 시설까지 최대 15분 방향성 보행경로
- `transit_connectors/transit_walk_connector_summary.json`: 연결 수와 중복 검증 결과

개별 접근·하차 연결을 900m까지 만든 뒤 실제 대중교통 경로에서 접근보행, 환승보행, 하차보행의 합이 15분 이하인지 다시 검사한다. 새 격자 기준 격자-정류장 연결은 813,577개, 정류장-시설 연결은 15,124개다. 보행망 바깥 스냅구간은 거리와 시간에는 포함하되 DEM 경사를 알 수 없어 경사 계산에서는 제외한다.

## 8. 격자-시설 대중교통 경로

- `transit_paths/transit_grid_facility_paths_90min_walk15min.parquet`: 기대 총이동시간 90분, 총보행 15분 이내 대중교통 경로
- `transit_paths/transit_grid_category_summary.csv`: 격자·분야별 기준 충족 시설 수와 최근접 비용
- `transit_paths/transit_path_build_summary.json`: 경로 선택과 부담 산식
- `transit_paths/transit_path_validation.json`: 전체 경로 검증 결과

경로 선택 기준은 `접근보행 + 최초 기대대기 + 차내이동 + 환승이동·기대대기 + 하차보행`의 합이 가장 작은 경로다. 그 최단 기대시간 경로에서 접근·환승·하차 보행 합계가 15분을 넘으면 제외한다. 최종 4,819,532개 경로를 만들었고, 중복·결측·90분 초과·총보행 15분 초과는 없다.

대중교통 경로의 거리부담은 보행 600m가 10분이라는 기준을 전체 이동시간으로 확장해 `min(대기 제외 이동시간 / 10분, 1)`로 계산한다. 기대대기시간은 거리부담에서 제외하고 교통부담에만 반영한다. 경사부담은 위치가 확인되는 접근·하차 보행망 엣지만 사용하며, GTFS 환승 연결구간은 경사에서 제외한다.

## 9. 최종 경로와 접근성 민감도

- `final_accessibility/grid_facility_best_paths_mode_limits.parquet`: 같은 격자-시설 쌍에서 각 수단의 허용 기준을 통과한 도보와 대중교통 중 종합 이동비용이 작은 최종 경로
- `final_accessibility/grid_category_accessibility_beta_sensitivity.csv`: 격자·분야별 기준 충족 시설 수, β=2·3·4 접근성 지수, 전체 시설 종합 정규화 점수
- `final_accessibility/beta_sensitivity_distribution.csv`: β별 접근성 분포
- `final_accessibility/beta_sensitivity_correlations.csv`: β 조합별 피어슨 및 스피어만 상관
- `final_accessibility/final_accessibility_validation.json`: 최종 경로 선택과 접근성 합산 독립 재검증

최종 경로는 4,822,866개이며 도보 선택 23,544개, 대중교통 선택 4,799,322개다. 분야별 접근성은 `A_i,c = Σ exp(-βG_ij)`로 계산한다. 전체 문화시설 종합점수는 공연 357개와 스포츠관람 4개를 한 집합으로 보고 `100 / 361 × Σ exp(-3G_ij)`로 계산한다. 경로가 없는 시설은 0을 기여한다. 결과표에는 `overall_accessibility_beta_3_normalized_0_1`, `overall_accessibility_beta_3_percent_0_100`, `overall_accessibility_deficit_0_1`을 추가했으며 종합 열은 같은 격자의 공연·스포츠 행에 동일하게 반복된다. 전시시설이 추가되면 시설 마스터의 전체 시설 수에 자동 포함된다.

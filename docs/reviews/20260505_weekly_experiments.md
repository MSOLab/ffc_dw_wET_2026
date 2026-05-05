# Experiment Review: 2026-04-29 ~ 2026-05-05

**Range**: `498cf05` (`20260428T234426_736253 run setting` — end of prior review) → HEAD (`cb415dc`, Merge PR #8)
**Benchmark**: PRA2017 large (1440 instances) · `benchmarks/PRA2017/pra2017_hybrid_match.csv`
**Reported objective**: weighted earliness + tardiness (wET)

---

## Tracked Experiments

총 **36개** 실험 시점 (제목에 timestamp가 박힌 25개 + 기능 commit body에 박힌 11개). 출처 commit body의 `computer:` / `on <machine>` 필드로 머신 확인.

병합·폐기 처리:

- 폐기 없음. 동일 config 재실행도 사이에 코드가 바뀌었으므로 별개로 본다 (RUN 1·2, RUN 6·7, RUN 10·11·12, RUN 15·16).
- RUN 18 (`mcf_lb_init_28`)는 단일 시나리오 — RUN 14(p_inc 16) × RUN 17(r_mult 2.0) 조합 단발 체크. 이후 RUN 27~29 그리드의 사전 확인.
- RUN 21 (`mcf_lb_only`)와 RUN 23 (`mcf_lb_only` 재측정 after `4ca477d`)는 같은 config — `4ca477d perf(pmtn-mcf): tighter t_max` 적용 효과의 A/B.
- RUN 22 (`mcf_lb_init_31` 7-scenario)와 RUN 24 (init_31 1st scenario only) 역시 `4ca477d` 적용 후의 단일 셀 재측정.
- RUN 25·26는 `4ca477d` **롤백 후** 동일 시나리오 BEFORE 측정 (A/B/B/A 패턴 — perf 변경의 timing 비교용. config는 RUN 21·22와 동일).
- RUN 30 (`mcf_lb_init_adjust_rj_1`)는 단일 시나리오 — RUN 31(`adjust_rj_2`, 7-scenario sweep)의 사전 동작 확인용.

**머신**:

- 35개 RUN: `mso02` (`computer: mso02` 또는 `on mso02`. `mso2`는 동일 머신의 표기 차이 — RUN 2, 9에서 사용).
- 1개 RUN (RUN 15, `20260503T170658_834025`): **`hjt5950x`** (`b565889 feat(mcf-lb): add heuristic last-stage-only step` body). 본 저장소 `output/`에는 산출물 없음 — hjt5950x 머신에서 별도 보관.

---

## Run Index

| #  | timestamp | machine | config file | source commit | scope | output_dir |
|----|---|---|---|---|---|---|
| 1  | 20260429T233115_006438 | mso02 | `metadata/20260429/20260429_config.yaml` | `e792136` (title) | full · 3 scen | `output/20260429/` |
| 2  | 20260430T110852_547352 | mso02 (mso2) | `metadata/20260429/20260429_config.yaml` | `a9981c1` (title) | full · 3 scen | `output/20260429/` |
| 3  | 20260501T162650_028232 | mso02 | `metadata/20260501/20260501_mcf_lb_then_neh_cp_config.yaml` | `c675c1a` (title) | full · 1 scen | `output/20260501/` |
| 4  | 20260502T002742_596323 | mso02 | `metadata/20260501/mcf_lb_init_19_config.yaml` | `9e74acc` (body) | full · 2 scen | `output/20260501/` |
| 5  | 20260502T025451_273045 | mso02 | `metadata/20260501/mcf_lb_init_20_config.yaml` | `f31fc87` (body) | full · 2 scen | `output/20260501/` |
| 6  | 20260502T032313_670203 | mso02 | `metadata/20260501/mcf_lb_init_21_config.yaml` | `c43f87b` (body) | full · 2 scen | `output/20260501/` |
| 7  | 20260502T131546_402074 | mso02 | `metadata/20260501/mcf_lb_init_21_config.yaml` | `9418208` (title) | full · 2 scen | `output/20260501/` |
| 8  | 20260502T133412_116270 | mso02 | `metadata/20260502/mcf_lb_init_22_config.yaml` (v1) | `8304421` (title) | full · 3 scen | `output/20260502/` |
| 9  | 20260502T145150_590013 | mso02 (mso2) | `metadata/20260502/mcf_lb_init_22_config.yaml` (v2) | `051d8e1` (title) | full · 6 scen | `output/20260502/` |
| 10 | 20260502T165007_640181 | mso02 | `metadata/20260502/mcf_lb_init_23_config.yaml` | `4f7fb0f` (title) | full · 1 scen | `output/20260502/` |
| 11 | 20260502T184531_518809 | mso02 | `metadata/20260502/mcf_lb_init_23_config.yaml` | `f3f0e73` (title) | full · 1 scen | `output/20260502/` |
| 12 | 20260502T193831_290902 | mso02 | `metadata/20260502/mcf_lb_init_23_config.yaml` | `6ecd356` (title) | full · 1 scen | `output/20260502/` |
| 13 | 20260503T022442_340817 | mso02 | `metadata/20260502/mcf_lb_init_24_config.yaml` | `bab11e8` (title) | full · 5 scen | `output/20260502/` |
| 14 | 20260503T170549_147724 | mso02 | `metadata/20260503/mcf_lb_init_25_config.yaml` | `5c4920a` (title) | full · 2 active scen | `output/20260503/` |
| 15 | 20260503T170658_834025 | **hjt5950x** | `metadata/20260503/mcf_lb_init_26_config.yaml` (worker_cnt=16) | `b565889` (body) | full · 8 scen | (not local) |
| 16 | 20260503T181635_000784 | mso02 | `metadata/20260503/mcf_lb_init_26_config.yaml` (worker_cnt=96) | `725a912` (title) | full · 8 scen | `output/20260503/` |
| 17 | 20260503T191906_135722 | mso02 | `metadata/20260503/mcf_lb_init_27_config.yaml` | `c4a790f` (title) | full · 7 scen | `output/20260503/` |
| 18 | 20260503T215803_006004 | mso02 | `metadata/20260503/mcf_lb_init_28_config.yaml` | `33cce02` (title) | full · 1 scen | `output/20260503/` |
| 19 | 20260503T230126_683476 | mso02 | `metadata/20260503/mcf_lb_init_29_config.yaml` | `f89ba73` (title) | full · 14 scen | `output/20260503/` |
| 20 | 20260504T003732_433340 | mso02 | `metadata/20260503/mcf_lb_init_30_config.yaml` | `9df77f8` (title) | full · 3 scen | `output/20260503/` |
| 21 | 20260504T004917_785558 | mso02 | `metadata/20260503/mcf_lb_only_config.yaml` | `3d546da` (title) | full · 1 scen | `output/20260503/` |
| 22 | 20260504T010002_965646 | mso02 | `metadata/20260503/mcf_lb_init_31_config.yaml` | `3d07d20` (title) | full · 7 scen | `output/20260503/` |
| 23 | 20260504T030753_945843 | mso02 | `metadata/20260502/mcf_lb_init_24_config.yaml` (`build_full_sch_p_inc_0` scen만) | `4ca477d` (body) | full · 1 scen — t_max **AFTER** | `output/20260503/` |
| 24 | 20260504T031049_337896 | mso02 | `metadata/20260503/mcf_lb_init_31_config.yaml` (1st scen only) | `4ca477d` (body) | full · 1 scen — t_max **AFTER** | `output/20260503/` |
| 25 | 20260504T031422_467379 | mso02 | `metadata/20260503/mcf_lb_init_31_config.yaml` (1st scen only) | `4ca477d` (body) | full · 1 scen — t_max **BEFORE** (pre-perf) | `output/20260503/` |
| 26 | 20260504T032002_269531 | mso02 | `metadata/20260502/mcf_lb_init_24_config.yaml` (`build_full_sch_p_inc_0` scen만) | `4ca477d` (body) | full · 1 scen — t_max **BEFORE** (pre-perf) | `output/20260503/` |
| 27 | 20260504T032732_697925 | mso02 | `metadata/20260503/mcf_lb_init_32_config.yaml` | `b090f84` (title) | full · 60 scen | `output/20260503/` |
| 28 | 20260504T082749_666067 | mso02 | `metadata/20260504/mcf_lb_init_33_config.yaml` | `1abc4f4` (title) | full · 60 scen | `output/20260504/` |
| 29 | 20260504T093058_016949 | mso02 | `metadata/20260504/mcf_lb_init_34_config.yaml` | `bff5eac` (title) | full · 80 scen | `output/20260504/` |
| 30 | 20260504T135233_268173 | mso02 | `metadata/20260504/mcf_lb_init_adjust_rj_1_config.yaml` | `78c6756` (title) | full · 1 scen | `output/20260504/` |
| 31 | 20260504T142221_504713 | mso02 | `metadata/20260504/mcf_lb_init_adjust_rj_2_config.yaml` | `a0a6974` (title) | full · 7 scen | `output/20260504/` |
| 32 | 20260505T014813_804225 | mso02 | `metadata/20260504/mcf_lb_init_35_config.yaml` | `07d7e7f` (title) | full · 2 scen | `output/20260504/` |
| 33 | 20260505T025805_689859 | mso02 | `metadata/20260504/mcf_lb_init_36_config.yaml` | `445bf53` (title) | full · 6 scen | `output/20260504/` |
| 34 | 20260505T102202_582058 | mso02 | `metadata/20260505/mcf_lb_init_37_config.yaml` | `0a8a9b0` (body) | full · 6 scen | `output/20260505/` |
| 35 | 20260505T191440_984385 | mso02 | `metadata/20260505/mcf_lb_init_37_config.yaml` | `c039ceb` (body) | full · 6 scen | `output/20260505/` |
| 36 | 20260505T192009_887337 | mso02 | `metadata/20260505/mcf_lb_init_38_config.yaml` | `af944e3` (body) | full · 4 scen | `output/20260505/` |

---

## Phase 1 — Logging/Orchestration 인프라 + Best-of 재실행 (4/29 ~ 4/30)

기간 commit: `17cb323`, `d0f4893`, `2bcaf97`, `d2f00c3`, `47fc501-ish`, `ffecbf3`, `2ce0479`, `cf0c902`, `e0f1cac`, `8beac9c`, `9bd935e`(merge logging PR)

핵심 작업

- `feat(logging): scoped handlers + manifest` — RUN별 `*_main.log` 단일 마스터 + per-instance scoped handler 분리.
- `feat(logging): is_main flag for INFO default` — main run 여부에 따른 INFO 로그 디폴트 전환 (RUN 2 직전).
- `feat(orchestration): adopt ArtifactLayout` — output 디렉토리 스키마를 `ArtifactLayout`로 통일 (`docs/io/20260429_artifact_manager.md`).
- `refactor(io): unify schedule key constants` + `refactor(sir): drop type-ignores via typed attrs` — IO/sir typing 정리.
- `chore(deps): publish routix from path to PyPI` — 외부 의존성 공개.

### RUN 1 — `20260429T233115_006438` (mso02)

- **Config**: `metadata/20260429/20260429_config.yaml` (`e792136` body: 새 production config 도입)
- **변경**: 시나리오 = `neh_cp_bs15_linear_dplus2_pf1_tl024nc` (NEH-CP, TL=120s) + `best_mcf_lb_cp_asis` (MCF-LB+CP, TL=300s) + `best` (MCF-LB→NEH-CP 직렬, TL=300s). `instance_worker_cnt: 12`.
- **의도**: `ArtifactLayout` 채택 + scoped logging 적용 후 전주 best-of 결과의 재현성 확인 + `best` (MCF-LB seed → NEH-CP) 직렬 조합 첫 풀-벤치.

- **결과**:

  | scenarioName | mean RPDf | mean bestObj | n |
  |---|---|---|---|
  | best | 0.2740 | 119,870 | 1440 |
  | best_mcf_lb_cp_asis | 1.0017 | 178,168 | 1440 |
  | neh_cp_bs15_linear_dplus2_pf1_tl024nc | 0.3028 | 127,395 | 1440 |

### RUN 2 — `20260430T110852_547352` (mso02)

- **Config**: 동일 (`20260429_config.yaml`)
- **변경**: 직전 commit `ffecbf3 feat(logging): add is_main flag`. config 자체는 헤더 한 줄 추가만.
- **의도**: `is_main` flag로 INFO 로그 디폴트 변경 후 동일 시나리오 재실행 — logging 변경이 결과에 영향을 미치지 않는지 확인.

---

- **결과**:

  | scenarioName | mean RPDf | mean bestObj | n |
  |---|---|---|---|
  | best | 0.2741 | 119,695 | 1440 |
  | best_mcf_lb_cp_asis | 1.0017 | 178,168 | 1440 |
  | neh_cp_bs15_linear_dplus2_pf1_tl024nc | 0.3113 | 127,493 | 1440 |

## Phase 2 — `mcf_lb_then_neh_cp` integrated step (5/1)

기간 commit: `6f2a63c feat(controller): add mcf_lb_then_neh_cp step`

핵심 작업

- 기존 "MCF-LB seed → NEH-CP" 직렬 흐름을 **단일 controller step**으로 통합. MCF preemptive LB → 시간-윈도우 폭(`t_max - t_min`) 오름차 + tie-break((w⁻+w⁺) DESC, p_{c,j} DESC, native job_id ASC) → NEH-CP에 그 시퀀스를 넘김.
- NEH-CP의 CP-SAT phase에만 timelimit (`neh_cp_total_timelimit`).

### RUN 3 — `20260501T162650_028232` (mso02)

- **Config**: `metadata/20260501/20260501_mcf_lb_then_neh_cp_config.yaml`
- **변경**: 단일 시나리오 `mcf_lb_then_neh_cp_bs15_linear_pf1_skip0_mas1_tl024nc`, TL=300s, `instance_worker_cnt: 12`. `draw_gantt: true` + `draw_heatmap: true` + `keep_step_schedules: true`.
- **의도**: 통합 step 첫 풀-벤치. 전주 `best`(MCF-LB→NEH-CP 직렬) 대비 단일 step으로 통합한 효과 확인.

---

- **결과**:

  | scenarioName | mean RPDf | mean bestObj | n |
  |---|---|---|---|
  | mcf_lb_then_neh_cp_bs15_linear_pf1_skip0_mas1_tl024nc | 0.7049 | 183,628 | 1440 |

## Phase 3 — last-stage-only step 진화 (NEH-CP → single-pass) (5/1 ~ 5/2)

기간 commit: `79f3d79`, `dd63fe8`, `5f10663`, `9e74acc`, `f31fc87`, `c43f87b`, `aa29cf7`

핵심 작업

- `feat(mcf-lb): add NEH-CP last-stage subroutine` (`79f3d79`) — `neh_cp_last_stage_only_sch_from_mcf_lb` controller step 신설 (MCF preemptive LB → NEH-CP를 마지막 stage에만).
- `feat(neh-cp): midpoint warm-start + job_priority` (`dd63fe8`) — NEH-CP의 warm-start 및 job priority 확장.
- `feat(neh-cp): add dispatched-schedule CP fix` (`5f10663`) — dispatch된 schedule의 CP 보정.
- `feat(mcf-lb): add start_time priority + dispatch` (`9e74acc`) — `start_time` job priority + heuristic `dispatch_insert_idle_time` (CP 없이 MCF window만 사용하는 경로).
- `feat(mcf-lb): add placement_priority tiebreak` (`f31fc87`) — `placement_priority` 인자(`(contrib, dist)` lex 순) 도입 + heuristic 경로 default `dist`, hint 경로 default `contrib`.
- `feat(mcf-lb): add single-pass last-stage step` (`c43f87b`) — `single_pass_last_stage_only_sch_from_mcf_lb`로 entry point 통합 (모든 job에 midpoint warm-start + 단일 profile-fix CP). 이름 `hint_placement_priority` → `placement_priority`. 이전의 `dispatch_insert_idle_time`은 superseded → 제거.
- `refactor: SSOT split of HeatmapSort union` (`aa29cf7`) — heatmap sort key를 SSOT로 분리.

### RUN 4 — `20260502T002742_596323` (mso02)

- **Config**: `metadata/20260501/mcf_lb_init_19_config.yaml`
- **변경**: 시나리오 2개 — `1_rj_prmp_rel_dev`, `start_time`. 흐름: `apply_lb_by_mcf` → `neh_cp_last_stage_only_sch_from_mcf_lb` (`batch_size: 15`, `cp_pf_method: PF1`, 8-thread, `total_tl: 0.01nc`).
- **의도**: `neh_cp_last_stage_only_sch_from_mcf_lb` (NEH-CP를 last stage에만) + `start_time` priority 첫 풀-벤치.

- **결과** *(no incumbent — algorithm did not register a full schedule; only `mcfLb` populated)*:

  | scenarioName | mean mcfLb | n |
  |---|---|---|
  | 1_rj_prmp_rel_dev | 39,202 | 1440 |
  | start_time | 39,202 | 1440 |

### RUN 5 — `20260502T025451_273045` (mso02)

- **Config**: `metadata/20260501/mcf_lb_init_20_config.yaml`
- **변경**: 같은 2 시나리오 — `hint_placement_priority: "dist"` 옵션 추가됨 (`f31fc87`의 새 인자).
- **의도**: NEH-CP last-stage 경로에서 placement_priority(`dist`)를 명시했을 때의 결과 확인.

- **결과** *(no incumbent — algorithm did not register a full schedule; only `mcfLb` populated)*:

  | scenarioName | mean mcfLb | n |
  |---|---|---|
  | 1_rj_prmp_rel_dev | 39,202 | 1440 |
  | start_time | 39,202 | 1440 |

### RUN 6 — `20260502T032313_670203` (mso02)

- **Config**: `metadata/20260501/mcf_lb_init_21_config.yaml` (신규)
- **변경**: 같은 2 시나리오 — step이 `neh_cp_last_stage_only_sch_from_mcf_lb` → **`single_pass_last_stage_only_sch_from_mcf_lb`**로 교체. NEH-CP-style 점진형 구성 대신 모든 job에 midpoint warm-start + 단일 profile-fix CP. `cp_solver_thread_cnt: 1` (8 → 1로 축소).
- **의도**: single-pass step의 첫 풀-벤치. RUN 5(NEH-CP last-stage)와 동일 priority 키 위에서 알고리즘 변경 효과 측정.

- **결과** *(no incumbent — algorithm did not register a full schedule; only `mcfLb` populated)*:

  | scenarioName | mean mcfLb | n |
  |---|---|---|
  | 1_rj_prmp_rel_dev | 39,202 | 1440 |
  | start_time | 39,202 | 1440 |

### RUN 7 — `20260502T131546_402074` (mso02)

- **Config**: 동일 (`mcf_lb_init_21_config.yaml`) — 직전 commit `aa29cf7 refactor: SSOT split of HeatmapSort union`. config은 인자명 `cp_pf_method`/`cp_solver_thread_cnt` → `pf_method`/`solver_thread_cnt`로 rename.
- **의도**: SSOT refactor + 인자명 rename 후 RUN 6 결과 재확인.

---

- **결과** *(no incumbent — algorithm did not register a full schedule; only `mcfLb` populated)*:

  | scenarioName | mean mcfLb | n |
  |---|---|---|
  | 1_rj_prmp_rel_dev | 39,202 | 1440 |
  | start_time | 39,202 | 1440 |

## Phase 4 — pm-sort key 확장 + placement_priority 스윕 (5/2)

기간 commit: `4234ca1 feat(pm-sort): add end_time and maxw keys`

핵심 작업

- pm-sort에 `end_time`, `*_maxw`(maxw = max weight) 키 추가. 기존 `start_time`, `1_rj_prmp_rel_dev`와 합쳐 4가지 sort 키.

### RUN 8 — `20260502T133412_116270` (mso02)

- **Config**: `metadata/20260502/mcf_lb_init_22_config.yaml` (v1)
- **변경**: 시나리오 3개 — `end_time`, `start_time_maxw`, `end_time_maxw`. `placement_priority: dist` 고정.
- **의도**: 새 sort/priority 키 3개의 베이스라인.

- **결과** *(no incumbent — algorithm did not register a full schedule; only `mcfLb` populated)*:

  | scenarioName | mean mcfLb | n |
  |---|---|---|
  | end_time | 39,202 | 1440 |
  | end_time_maxw | 39,202 | 1440 |
  | start_time_maxw | 39,202 | 1440 |

### RUN 9 — `20260502T145150_590013` (mso02)

- **Config**: 동일 파일 (v2 — `051d8e1` body: "superset of 20260502T133412_116270")
- **변경**: 시나리오 6개 — `{end_time, start_time_maxw, end_time_maxw} × {contrib, dist}` placement_priority 스윕.
- **의도**: placement_priority(`contrib` vs `dist`)가 결과에 미치는 영향 측정.

---

- **결과** *(no incumbent — algorithm did not register a full schedule; only `mcfLb` populated)*:

  | scenarioName | mean mcfLb | n |
  |---|---|---|
  | end_time_contrib | 39,202 | 1440 |
  | end_time_dist | 39,202 | 1440 |
  | end_time_maxw_contrib | 39,202 | 1440 |
  | end_time_maxw_dist | 39,202 | 1440 |
  | start_time_maxw_contrib | 39,202 | 1440 |
  | start_time_maxw_dist | 39,202 | 1440 |

## Phase 5 — `build_full_sch_from_last_stage_only_sch` step + 디버깅 사이클 (5/2)

기간 commit: `791e5b8`, `d2bab6c`, `c61e435`, `c3cbecf`, `07c8cea`

핵심 작업

- `feat(mcf-lb): build_full_sch step + cp_sat rename` (`791e5b8`) — last-stage-only schedule을 전 stage로 확장하는 step 신설. `cp_sat` 명명 정리.
- `method argument error fix` (`d2bab6c`) — `build_full_sch_from_last_stage_only_sch` 호출 인자 버그 수정.
- `save preemptive heatmap HTML under report dir` (`c61e435`) — heatmap 산출물 위치 정리.
- `refactor(ddw-params): cache dw lb/ub maps` (`c3cbecf`) — dw lb/ub 맵 캐시.
- `feat(mcf-lb): delay ls-only ops before flip` (`07c8cea`) — last-stage-only 연산을 flip 전까지 지연.

### RUN 10 — `20260502T165007_640181` (mso02)

- **Config**: `metadata/20260502/mcf_lb_init_23_config.yaml`
- **변경**: 단일 시나리오 `build_full_sch_1` — `apply_lb_by_mcf` (sort=`end_time`) → `single_pass_last_stage_only_sch_from_mcf_lb` (priority=`end_time`, placement=`dist`, PF1, 0.01nc) → **`build_full_sch_from_last_stage_only_sch`**.
- **의도**: 새로 추가된 `build_full_sch` step 첫 풀-벤치. last-stage-only schedule을 전 stage로 확장 시 wET 변화 측정.

- **결과**:

  | scenarioName | mean RPDf | mean bestObj | n |
  |---|---|---|---|
  | build_full_sch_1 | 1.0532 | 212,091 | 1440 |

### RUN 11 — `20260502T184531_518809` (mso02)

- **Config**: 동일. 직전 commit: `d2bab6c` (method arg fix), `c61e435` (heatmap 위치), `c3cbecf` (ddw params cache).
- **의도**: 디버깅/버그픽스 후 재현성 확인.

- **결과**:

  | scenarioName | mean RPDf | mean bestObj | n |
  |---|---|---|---|
  | build_full_sch_1 | 0.7277 | 146,879 | 1440 |

### RUN 12 — `20260502T193831_290902` (mso02)

- **Config**: 동일. 직전 commit: `07c8cea feat(mcf-lb): delay ls-only ops before flip`.
- **의도**: ls-only ops 지연 적용 후 결과 변화 확인.

---

- **결과**:

  | scenarioName | mean RPDf | mean bestObj | n |
  |---|---|---|---|
  | build_full_sch_1 | 0.7303 | 146,667 | 1440 |

## Phase 6 — `p_increment` knob (5/3)

기간 commit: `d28e700`, `0a72134`

핵심 작업

- `un-comment delay_job_latest_leq_obj_contrib` (`d28e700`) — 비활성화돼 있던 contribution 기반 delay 로직 다시 켬.
- `feat(mcf-lb): add p_increment to mcf-lb steps` (`0a72134`) — `p_increment` 파라미터 추가 (processing time 부풀리기 노브).

### RUN 13 — `20260503T022442_340817` (mso02)

- **Config**: `metadata/20260502/mcf_lb_init_24_config.yaml`
- **변경**: 시나리오 5개 — `build_full_sch_p_inc_{0,1,2,4,8}`. p_increment 0/1/2/4/8 스윕. 흐름은 RUN 12와 동일.
- **의도**: `p_increment` 노브의 효과 측정 (작은 정수 스윕).

- **결과**:

  | scenarioName | mean RPDf | mean bestObj | n |
  |---|---|---|---|
  | build_full_sch_p_inc_0 | 0.7277 | 146,879 | 1440 |
  | build_full_sch_p_inc_1 | 0.7160 | 145,267 | 1440 |
  | build_full_sch_p_inc_2 | 0.7164 | 143,748 | 1440 |
  | build_full_sch_p_inc_4 | 0.7007 | 140,727 | 1440 |
  | build_full_sch_p_inc_8 | 0.7012 | 136,097 | 1440 |

### RUN 14 — `20260503T170549_147724` (mso02)

- **Config**: `metadata/20260503/mcf_lb_init_25_config.yaml`. `instance_worker_cnt: 48` (이 RUN만 — 메모리/CP 부담 추정).
- **변경**: 시나리오 2개 활성 — `build_full_sch_p_inc_{16,32}` (`p_inc_64`~`p_inc_512`는 주석 처리). RUN 13의 연장.
- **의도**: p_increment를 16, 32까지 늘려 plateau/turnaround 위치 탐색.

---

- **결과**:

  | scenarioName | mean RPDf | mean bestObj | n |
  |---|---|---|---|
  | build_full_sch_p_inc_16 | 0.7245 | 131,668 | 1440 |
  | build_full_sch_p_inc_32 | 0.8469 | 147,988 | 1440 |

## Phase 7 — `heuristic_last_stage_only_sch_from_mcf_lb` 도입 (5/3)

기간 commit: `b586f41`, `b565889`

핵심 작업

- `feat(mcf-lb): add heuristic last-stage-only step` (`b565889`) — `single_pass_last_stage_only_sch_from_mcf_lb`(CP-SAT)의 **휴리스틱 변종**. CP solver 없이 빠르게 last-stage schedule 산출. `b565889` body: `mcf_lb_init_26_config.yaml` (p_inc 0~64 8-시나리오) 추가, hjt5950x에서 첫 시험 RUN.
- `fix comment on obj_bound of single_pass_last_stage_only_from_mcf_lb` (`b586f41`).

### RUN 15 — `20260503T170658_834025` (**hjt5950x**)

- **Config**: `metadata/20260503/mcf_lb_init_26_config.yaml` (`b565889` 시점 — `instance_worker_cnt: 16`, hjt5950x 코어 수에 맞춤)
- **변경**: 시나리오 8개 — `build_full_sch_p_inc_{0,1,2,4,8,16,32,64}`. `single_pass_*` → **`heuristic_last_stage_only_sch_from_mcf_lb`**로 교체.
- **의도**: heuristic 변종의 첫 풀-벤치 — CP-SAT 없이 어느 정도 손실(혹은 의외로 더 나아짐)되는지 hjt5950x에서 빠르게 확인.
- **비고**: 산출물은 hjt5950x 머신에 있고, 본 저장소 `output/`에는 없음.

- **결과**: 본 저장소에 산출물 없음 (hjt5950x 머신). RUN 16(`mcf_lb_init_26` mso02 재실행)으로 비교.

### RUN 16 — `20260503T181635_000784` (mso02)

- **Config**: 동일 파일 (`mcf_lb_init_26_config.yaml`) — `instance_worker_cnt: 16 → 96`만 변경.
- **변경**: 시나리오 8개 동일 (`build_full_sch_p_inc_{0,1,2,4,8,16,32,64}`).
- **의도**: 같은 8 시나리오를 mso02(96-worker)에서 재실행 — RUN 15와 같은 결과인지 확인 + 본 저장소에 산출물 보관.

- **결과**:

  | scenarioName | mean RPDf | mean bestObj | n |
  |---|---|---|---|
  | build_full_sch_p_inc_0 | 0.7348 | 146,914 | 1440 |
  | build_full_sch_p_inc_1 | 0.7245 | 145,712 | 1440 |
  | build_full_sch_p_inc_2 | 0.7237 | 144,121 | 1440 |
  | build_full_sch_p_inc_4 | 0.7039 | 140,699 | 1440 |
  | build_full_sch_p_inc_8 | 0.6997 | 135,857 | 1440 |
  | build_full_sch_p_inc_16 | 0.7224 | 131,560 | 1440 |
  | build_full_sch_p_inc_32 | 0.8469 | 148,046 | 1440 |
  | build_full_sch_p_inc_64 | 1.0999 | 219,349 | 1440 |

---

## Phase 8 — `r_multiplier` knob (5/3)

기간 commit: `4ab9008`, `7597677`

핵심 작업

- `fix(algorithm): use submodule imports in tests` (`4ab9008`) — `algorithm` 패키지 surface가 의도적으로 비어 있어 테스트도 submodule을 직접 import하도록 정정.
- `feat(mcf-lb): add r_multiplier release-date knob` (`7597677`) — release date를 곱해서 늘리는 노브.

### RUN 17 — `20260503T191906_135722` (mso02)

- **Config**: `metadata/20260503/mcf_lb_init_27_config.yaml`
- **변경**: 시나리오 7개 — `build_full_sch_r_mult_{1_0, 1_1, 1_25, 1_5, 2_0, 3_0, 4_0}`. `p_increment: 0` 고정, `r_multiplier`만 1.0 → 4.0 스윕. heuristic 경로.
- **의도**: release date 부풀리기 노브의 효과 측정. 1.0이 베이스라인.

- **결과**:

  | scenarioName | mean RPDf | mean bestObj | n |
  |---|---|---|---|
  | build_full_sch_r_mult_1_0 | 0.7348 | 146,914 | 1440 |
  | build_full_sch_r_mult_1_1 | 0.7231 | 144,985 | 1440 |
  | build_full_sch_r_mult_1_25 | 0.7062 | 142,587 | 1440 |
  | build_full_sch_r_mult_1_5 | 0.7018 | 139,002 | 1440 |
  | build_full_sch_r_mult_2_0 | 0.7211 | 136,338 | 1440 |
  | build_full_sch_r_mult_3_0 | 0.8450 | 149,320 | 1440 |
  | build_full_sch_r_mult_4_0 | 0.9816 | 176,979 | 1440 |
  | build_full_sch_r_mult_8_0 | 1.3013 | 297,069 | 1440 |

### RUN 18 — `20260503T215803_006004` (mso02)

- **Config**: `metadata/20260503/mcf_lb_init_28_config.yaml`
- **변경**: 단일 시나리오 `build_full_sch_p_inc_16_r_mult_2_0` — p_inc=16, r_mult=2.0 조합 단발 체크.
- **의도**: RUN 14(p_inc=16)와 RUN 17(r_mult=2.0)의 best-ish 두 노브 결합. 이후 RUN 27~29 큰 그리드 진행 결정용.

---

- **결과**:

  | scenarioName | mean RPDf | mean bestObj | n |
  |---|---|---|---|
  | build_full_sch_p_inc_16_r_mult_2_0 | 0.7919 | 135,468 | 1440 |

## Phase 9 — `r_increment` 도입 + `mcf_lb_only` 베이스라인 + `t_max` perf 측정 (5/3 ~ 5/4)

기간 commit: `01fd564`, `1f7b485`, `4ca477d`

핵심 작업

- `refactor(orchestration): rework summary metrics` (`01fd564`) — summary metric 정리.
- `feat(mcf-lb): add r_increment release-date knob` (`1f7b485`) — release date에 더하는 정수 노브.
- `perf(pmtn-mcf): tighter t_max with mc_count` (`4ca477d`) — preemptive MCF의 horizon 상한을 `max(max_r, max(d⁻−p)) + ⌈sum_p / mc⌉`로 타이트하게 잡음. body에 4개 timing 측정 RUN 동봉 (RUN 23~26).

### RUN 19 — `20260503T230126_683476` (mso02)

- **Config**: `metadata/20260503/mcf_lb_init_29_config.yaml`
- **변경**: 시나리오 14개 — `build_full_sch_r_inc_{0,1,2,4,8,16,32,64,128,256,512,1024,2048,4096}`. `p_increment: 0`, default `r_multiplier`. r_increment 지수 스윕.
- **의도**: r_increment 노브의 효과 측정.

- **결과**:

  | scenarioName | mean RPDf | mean bestObj | n |
  |---|---|---|---|
  | build_full_sch_r_inc_0 | 0.7348 | 146,914 | 1440 |
  | build_full_sch_r_inc_1 | 0.7383 | 147,122 | 1440 |
  | build_full_sch_r_inc_1024 | 1.0485 | 196,603 | 1440 |
  | build_full_sch_r_inc_128 | 0.7088 | 140,583 | 1440 |
  | build_full_sch_r_inc_16 | 0.7345 | 146,253 | 1440 |
  | build_full_sch_r_inc_2 | 0.7342 | 146,990 | 1440 |
  | build_full_sch_r_inc_2048 | 1.1853 | 271,323 | 1440 |
  | build_full_sch_r_inc_256 | 0.7121 | 136,550 | 1440 |
  | build_full_sch_r_inc_32 | 0.7323 | 145,286 | 1440 |
  | build_full_sch_r_inc_4 | 0.7398 | 147,340 | 1440 |
  | build_full_sch_r_inc_4096 | 1.1978 | 296,152 | 1440 |
  | build_full_sch_r_inc_512 | 0.8156 | 144,346 | 1440 |
  | build_full_sch_r_inc_64 | 0.7196 | 143,645 | 1440 |
  | build_full_sch_r_inc_8 | 0.7331 | 147,174 | 1440 |

### RUN 20 — `20260504T003732_433340` (mso02)

- **Config**: `metadata/20260503/mcf_lb_init_30_config.yaml`
- **변경**: 시나리오 3개 — `build_full_sch_p+16_rx2+{64,128,256}`. p_inc=16, r_mult=2.0, 큰 r_inc 3개 — RUN 18 (p+16, rx2) 라인 위에 r_inc 큰 값 얹기.
- **의도**: 큰 r_inc 영역에서 (p+16, rx2)가 어떻게 변하는지.

- **결과**:

  | scenarioName | mean RPDf | mean bestObj | n |
  |---|---|---|---|
  | build_full_sch_p+16_rx2+128 | 0.8677 | 147,054 | 1440 |
  | build_full_sch_p+16_rx2+256 | 0.9323 | 160,508 | 1440 |
  | build_full_sch_p+16_rx2+64 | 0.8301 | 140,761 | 1440 |

### RUN 21 — `20260504T004917_785558` (mso02)

- **Config**: `metadata/20260503/mcf_lb_only_config.yaml`
- **변경**: 단일 시나리오 `mcf_lb_only` — `apply_lb_by_mcf` 단독.
- **의도**: **MCF preemptive LB만의 단독 측정** (`bestObj` = `mcfLb`). 다른 RUN의 LB 메트릭과 직접 비교 가능.
- **비고**: schedule 없음. `dispatchedObj`/`profileFixObj` 비어 있음.

- **결과** *(no incumbent — algorithm did not register a full schedule; only `mcfLb` populated)*:

  | scenarioName | mean mcfLb | n |
  |---|---|---|
  | mcf_lb_only | 39,202 | 1440 |

### RUN 22 — `20260504T010002_965646` (mso02)

- **Config**: `metadata/20260503/mcf_lb_init_31_config.yaml`
- **변경**: 시나리오 7개 — `build_full_sch_p+16_rx2+{0,1,2,4,8,16,32}`. RUN 20의 (p+16, rx2) 라인을 r_inc 작은 값까지 채움.
- **의도**: (p+16, rx2)에서 r_inc 0~32 영역 sweep — RUN 20과 합쳐 곡선 완성.

- **결과**:

  | scenarioName | mean RPDf | mean bestObj | n |
  |---|---|---|---|
  | build_full_sch_p+16_rx2 | 0.7919 | 135,468 | 1440 |
  | build_full_sch_p+16_rx2+1 | 0.7918 | 135,388 | 1440 |
  | build_full_sch_p+16_rx2+16 | 0.7995 | 136,633 | 1440 |
  | build_full_sch_p+16_rx2+2 | 0.7913 | 135,549 | 1440 |
  | build_full_sch_p+16_rx2+32 | 0.8097 | 138,315 | 1440 |
  | build_full_sch_p+16_rx2+4 | 0.7925 | 135,661 | 1440 |
  | build_full_sch_p+16_rx2+8 | 0.7961 | 136,090 | 1440 |

### RUN 23~26 — `4ca477d` perf 측정 A/B/B/A (mso02)

`4ca477d` body 발췌:

```
- 20260504T030753_945843 = 20260504T004917_785558  (mcf_lb_only)
  - calc time: 60.90(20260504T032002_269531) -> 33.05 seconds
- 20260504T031049_337896 = 20260504T010002_965646 1st scenario  (init_31[0])
  - calc time: 83.24(20260504T031422_467379) -> 36.72 seconds
```

#### RUN 23 — `20260504T030753_945843` (mso02, AFTER)

- **Config**: `mcf_lb_init_24_config.yaml` 1st 시나리오 `build_full_sch_p_inc_0`만 (`4ca477d` body는 `=T004917`로 표기했으나 실제 산출물 시나리오는 `build_full_sch_p_inc_0`).
- **변경**: `4ca477d` patch 적용 상태에서 단일 시나리오 재측정. body 메모: 33.05s.
- **의도**: t_max tightening의 build_full_sch 흐름 단일 셀 timing AFTER 측정.

- **결과**:

  | scenarioName | mean RPDf | mean bestObj | n |
  |---|---|---|---|
  | build_full_sch_p_inc_0 | 0.7393 | 147,085 | 1440 |

#### RUN 24 — `20260504T031049_337896` (mso02, AFTER)

- **Config**: `mcf_lb_init_31_config.yaml` 1st scenario만 (`build_full_sch_p+16_rx2+0`)
- **변경**: `4ca477d` patch 적용 상태에서 단일 시나리오 재측정. 36.72s.
- **의도**: build_full_sch 흐름에서의 timing AFTER 측정.

- **결과**:

  | scenarioName | mean RPDf | mean bestObj | n |
  |---|---|---|---|
  | build_full_sch_p+16_rx2 | 0.7919 | 135,440 | 1440 |

#### RUN 25 — `20260504T031422_467379` (mso02, BEFORE)

- **Config**: 동일 `init_31` 1st scenario
- **변경**: `4ca477d` patch 롤백 상태에서 같은 시나리오 측정 (BEFORE 베이스라인). 83.24s.
- **의도**: t_max tightening 적용 전 baseline timing — RUN 24와 직접 비교.

- **결과**:

  | scenarioName | mean RPDf | mean bestObj | n |
  |---|---|---|---|
  | build_full_sch_p+16_rx2 | 0.7919 | 135,468 | 1440 |

#### RUN 26 — `20260504T032002_269531` (mso02, BEFORE)

- **Config**: 동일 `mcf_lb_init_24_config.yaml` 1st 시나리오 `build_full_sch_p_inc_0`만 (RUN 23과 같은 scenario).
- **변경**: `4ca477d` patch 롤백 상태에서 동일 시나리오 측정 (BEFORE 베이스라인). body 메모: 60.90s.
- **의도**: t_max tightening 적용 전 baseline timing — RUN 23과 직접 비교.

---

- **결과**:

  | scenarioName | mean RPDf | mean bestObj | n |
  |---|---|---|---|
  | build_full_sch_p_inc_0 | 0.7348 | 146,914 | 1440 |

## Phase 10 — p×r 그리드 스윕 (5/4)

### RUN 27 — `20260504T032732_697925` (mso02)

- **Config**: `metadata/20260503/mcf_lb_init_32_config.yaml`
- **변경**: 시나리오 60개 — `build_full_sch_p+{0,1,2,4,8,16}_rx2+{0,1,2,4,8,16,32,62,128,256}` 그리드. 시나리오 이름은 `rx2`이지만 **`r_multiplier: 1.0`** (이름은 init_31 잔재).
- **의도**: p×r 60-시나리오 그리드 — `r_mult=1.0` 베이스라인.

- **결과**:

  | scenarioName | mean RPDf | mean bestObj | n |
  |---|---|---|---|
  | build_full_sch_p+0_rx2+0 | 0.7393 | 147,085 | 1440 |
  | build_full_sch_p+0_rx2+1 | 0.7364 | 147,255 | 1440 |
  | build_full_sch_p+0_rx2+128 | 0.7069 | 140,240 | 1440 |
  | build_full_sch_p+0_rx2+16 | 0.7298 | 145,916 | 1440 |
  | build_full_sch_p+0_rx2+2 | 0.7367 | 147,338 | 1440 |
  | build_full_sch_p+0_rx2+256 | 0.7135 | 136,518 | 1440 |
  | build_full_sch_p+0_rx2+32 | 0.7231 | 145,135 | 1440 |
  | build_full_sch_p+0_rx2+4 | 0.7375 | 146,871 | 1440 |
  | build_full_sch_p+0_rx2+62 | 0.7181 | 143,197 | 1440 |
  | build_full_sch_p+0_rx2+8 | 0.7343 | 146,465 | 1440 |
  | build_full_sch_p+16_rx2+0 | 0.7239 | 131,581 | 1440 |
  | build_full_sch_p+16_rx2+1 | 0.7201 | 131,181 | 1440 |
  | build_full_sch_p+16_rx2+128 | 0.7332 | 130,323 | 1440 |
  | build_full_sch_p+16_rx2+16 | 0.7137 | 130,955 | 1440 |
  | build_full_sch_p+16_rx2+2 | 0.7192 | 131,252 | 1440 |
  | build_full_sch_p+16_rx2+256 | 0.8075 | 137,667 | 1440 |
  | build_full_sch_p+16_rx2+32 | 0.7145 | 130,001 | 1440 |
  | build_full_sch_p+16_rx2+4 | 0.7191 | 131,234 | 1440 |
  | build_full_sch_p+16_rx2+62 | 0.7116 | 129,645 | 1440 |
  | build_full_sch_p+16_rx2+8 | 0.7172 | 130,814 | 1440 |
  | build_full_sch_p+1_rx2+0 | 0.7286 | 145,380 | 1440 |
  | build_full_sch_p+1_rx2+1 | 0.7183 | 145,166 | 1440 |
  | build_full_sch_p+1_rx2+128 | 0.6862 | 138,539 | 1440 |
  | build_full_sch_p+1_rx2+16 | 0.7264 | 144,406 | 1440 |
  | build_full_sch_p+1_rx2+2 | 0.7212 | 145,067 | 1440 |
  | build_full_sch_p+1_rx2+256 | 0.7051 | 135,150 | 1440 |
  | build_full_sch_p+1_rx2+32 | 0.7157 | 143,310 | 1440 |
  | build_full_sch_p+1_rx2+4 | 0.7253 | 145,199 | 1440 |
  | build_full_sch_p+1_rx2+62 | 0.7074 | 141,843 | 1440 |
  | build_full_sch_p+1_rx2+8 | 0.7275 | 144,799 | 1440 |
  | build_full_sch_p+2_rx2+0 | 0.7269 | 143,917 | 1440 |
  | build_full_sch_p+2_rx2+1 | 0.7267 | 143,699 | 1440 |
  | build_full_sch_p+2_rx2+128 | 0.6865 | 136,793 | 1440 |
  | build_full_sch_p+2_rx2+16 | 0.7120 | 143,054 | 1440 |
  | build_full_sch_p+2_rx2+2 | 0.7212 | 143,679 | 1440 |
  | build_full_sch_p+2_rx2+256 | 0.6935 | 133,726 | 1440 |
  | build_full_sch_p+2_rx2+32 | 0.7058 | 141,748 | 1440 |
  | build_full_sch_p+2_rx2+4 | 0.7208 | 143,436 | 1440 |
  | build_full_sch_p+2_rx2+62 | 0.6906 | 139,640 | 1440 |
  | build_full_sch_p+2_rx2+8 | 0.7137 | 143,569 | 1440 |
  | build_full_sch_p+4_rx2+0 | 0.7078 | 141,038 | 1440 |
  | build_full_sch_p+4_rx2+1 | 0.7090 | 140,566 | 1440 |
  | build_full_sch_p+4_rx2+128 | 0.6769 | 133,877 | 1440 |
  | build_full_sch_p+4_rx2+16 | 0.6954 | 139,664 | 1440 |
  | build_full_sch_p+4_rx2+2 | 0.7041 | 140,940 | 1440 |
  | build_full_sch_p+4_rx2+256 | 0.6863 | 131,785 | 1440 |
  | build_full_sch_p+4_rx2+32 | 0.6957 | 138,963 | 1440 |
  | build_full_sch_p+4_rx2+4 | 0.6999 | 140,884 | 1440 |
  | build_full_sch_p+4_rx2+62 | 0.6843 | 137,412 | 1440 |
  | build_full_sch_p+4_rx2+8 | 0.6999 | 140,359 | 1440 |
  | build_full_sch_p+8_rx2+0 | 0.7010 | 135,864 | 1440 |
  | build_full_sch_p+8_rx2+1 | 0.7046 | 135,827 | 1440 |
  | build_full_sch_p+8_rx2+128 | 0.6742 | 130,239 | 1440 |
  | build_full_sch_p+8_rx2+16 | 0.6933 | 134,612 | 1440 |
  | build_full_sch_p+8_rx2+2 | 0.7004 | 136,091 | 1440 |
  | build_full_sch_p+8_rx2+256 | 0.7161 | 130,905 | 1440 |
  | build_full_sch_p+8_rx2+32 | 0.6872 | 134,123 | 1440 |
  | build_full_sch_p+8_rx2+4 | 0.7043 | 135,670 | 1440 |
  | build_full_sch_p+8_rx2+62 | 0.6809 | 132,818 | 1440 |
  | build_full_sch_p+8_rx2+8 | 0.7019 | 135,562 | 1440 |

### RUN 28 — `20260504T082749_666067` (mso02)

- **Config**: `metadata/20260504/mcf_lb_init_33_config.yaml`
- **변경**: 시나리오 60개, RUN 27 동일 그리드, **`r_multiplier: 2.0`**.
- **의도**: r_mult=2.0 비교. r_multiplier가 어느 영역에서 결과를 바꾸는지.

- **결과**:

  | scenarioName | mean RPDf | mean bestObj | n |
  |---|---|---|---|
  | build_full_sch_p+0_rx2+0 | 0.7204 | 136,114 | 1440 |
  | build_full_sch_p+0_rx2+1 | 0.7187 | 135,852 | 1440 |
  | build_full_sch_p+0_rx2+128 | 0.7537 | 137,270 | 1440 |
  | build_full_sch_p+0_rx2+16 | 0.7238 | 136,137 | 1440 |
  | build_full_sch_p+0_rx2+2 | 0.7199 | 136,247 | 1440 |
  | build_full_sch_p+0_rx2+256 | 0.8114 | 142,853 | 1440 |
  | build_full_sch_p+0_rx2+32 | 0.7250 | 136,292 | 1440 |
  | build_full_sch_p+0_rx2+4 | 0.7218 | 136,223 | 1440 |
  | build_full_sch_p+0_rx2+62 | 0.7374 | 136,397 | 1440 |
  | build_full_sch_p+0_rx2+8 | 0.7239 | 135,940 | 1440 |
  | build_full_sch_p+16_rx2+0 | 0.7919 | 135,440 | 1440 |
  | build_full_sch_p+16_rx2+1 | 0.7921 | 135,636 | 1440 |
  | build_full_sch_p+16_rx2+128 | 0.8679 | 147,028 | 1440 |
  | build_full_sch_p+16_rx2+16 | 0.8013 | 136,651 | 1440 |
  | build_full_sch_p+16_rx2+2 | 0.7926 | 135,651 | 1440 |
  | build_full_sch_p+16_rx2+256 | 0.9329 | 160,539 | 1440 |
  | build_full_sch_p+16_rx2+32 | 0.8094 | 138,034 | 1440 |
  | build_full_sch_p+16_rx2+4 | 0.7918 | 135,716 | 1440 |
  | build_full_sch_p+16_rx2+62 | 0.8285 | 140,574 | 1440 |
  | build_full_sch_p+16_rx2+8 | 0.7959 | 136,157 | 1440 |
  | build_full_sch_p+1_rx2+0 | 0.7096 | 134,735 | 1440 |
  | build_full_sch_p+1_rx2+1 | 0.7131 | 134,889 | 1440 |
  | build_full_sch_p+1_rx2+128 | 0.7509 | 136,270 | 1440 |
  | build_full_sch_p+1_rx2+16 | 0.7153 | 134,870 | 1440 |
  | build_full_sch_p+1_rx2+2 | 0.7072 | 134,697 | 1440 |
  | build_full_sch_p+1_rx2+256 | 0.8090 | 142,691 | 1440 |
  | build_full_sch_p+1_rx2+32 | 0.7185 | 134,933 | 1440 |
  | build_full_sch_p+1_rx2+4 | 0.7138 | 134,919 | 1440 |
  | build_full_sch_p+1_rx2+62 | 0.7251 | 135,101 | 1440 |
  | build_full_sch_p+1_rx2+8 | 0.7152 | 134,940 | 1440 |
  | build_full_sch_p+2_rx2+0 | 0.7024 | 133,731 | 1440 |
  | build_full_sch_p+2_rx2+1 | 0.6984 | 133,557 | 1440 |
  | build_full_sch_p+2_rx2+128 | 0.7463 | 135,583 | 1440 |
  | build_full_sch_p+2_rx2+16 | 0.7051 | 133,721 | 1440 |
  | build_full_sch_p+2_rx2+2 | 0.7001 | 133,281 | 1440 |
  | build_full_sch_p+2_rx2+256 | 0.8079 | 142,843 | 1440 |
  | build_full_sch_p+2_rx2+32 | 0.7068 | 133,458 | 1440 |
  | build_full_sch_p+2_rx2+4 | 0.7028 | 133,570 | 1440 |
  | build_full_sch_p+2_rx2+62 | 0.7188 | 133,987 | 1440 |
  | build_full_sch_p+2_rx2+8 | 0.7059 | 133,579 | 1440 |
  | build_full_sch_p+4_rx2+0 | 0.6957 | 131,301 | 1440 |
  | build_full_sch_p+4_rx2+1 | 0.6967 | 131,234 | 1440 |
  | build_full_sch_p+4_rx2+128 | 0.7507 | 134,942 | 1440 |
  | build_full_sch_p+4_rx2+16 | 0.7029 | 131,615 | 1440 |
  | build_full_sch_p+4_rx2+2 | 0.6956 | 131,478 | 1440 |
  | build_full_sch_p+4_rx2+256 | 0.8234 | 143,600 | 1440 |
  | build_full_sch_p+4_rx2+32 | 0.7047 | 131,673 | 1440 |
  | build_full_sch_p+4_rx2+4 | 0.7005 | 131,457 | 1440 |
  | build_full_sch_p+4_rx2+62 | 0.7217 | 132,417 | 1440 |
  | build_full_sch_p+4_rx2+8 | 0.7006 | 131,575 | 1440 |
  | build_full_sch_p+8_rx2+0 | 0.7101 | 129,483 | 1440 |
  | build_full_sch_p+8_rx2+1 | 0.7146 | 129,703 | 1440 |
  | build_full_sch_p+8_rx2+128 | 0.7840 | 136,408 | 1440 |
  | build_full_sch_p+8_rx2+16 | 0.7203 | 129,909 | 1440 |
  | build_full_sch_p+8_rx2+2 | 0.7119 | 129,461 | 1440 |
  | build_full_sch_p+8_rx2+256 | 0.8589 | 147,502 | 1440 |
  | build_full_sch_p+8_rx2+32 | 0.7281 | 130,860 | 1440 |
  | build_full_sch_p+8_rx2+4 | 0.7154 | 129,622 | 1440 |
  | build_full_sch_p+8_rx2+62 | 0.7410 | 132,020 | 1440 |
  | build_full_sch_p+8_rx2+8 | 0.7184 | 129,930 | 1440 |

### RUN 29 — `20260504T093058_016949` (mso02)

- **Config**: `metadata/20260504/mcf_lb_init_34_config.yaml`
- **변경**: 시나리오 80개 — `build_full_sch_p+{0,1,2,4,8,16,32,64}_r+{0,1,2,4,8,16,32,64,128,256}`. `r_multiplier: 1.0`. p와 r_inc 모두 확장.
- **의도**: 그리드 확장 — `(p_inc, r_inc)` 평면 wET-최적 좌표 탐색.

---

- **결과**:

  | scenarioName | mean RPDf | mean bestObj | n |
  |---|---|---|---|
  | build_full_sch_p+0_r+0 | 0.7393 | 147,085 | 1440 |
  | build_full_sch_p+0_r+1 | 0.7364 | 147,255 | 1440 |
  | build_full_sch_p+0_r+128 | 0.7069 | 140,240 | 1440 |
  | build_full_sch_p+0_r+16 | 0.7298 | 145,916 | 1440 |
  | build_full_sch_p+0_r+2 | 0.7367 | 147,338 | 1440 |
  | build_full_sch_p+0_r+256 | 0.7135 | 136,518 | 1440 |
  | build_full_sch_p+0_r+32 | 0.7231 | 145,135 | 1440 |
  | build_full_sch_p+0_r+4 | 0.7375 | 146,871 | 1440 |
  | build_full_sch_p+0_r+64 | 0.7130 | 143,125 | 1440 |
  | build_full_sch_p+0_r+8 | 0.7343 | 146,465 | 1440 |
  | build_full_sch_p+16_r+0 | 0.7239 | 131,581 | 1440 |
  | build_full_sch_p+16_r+1 | 0.7201 | 131,181 | 1440 |
  | build_full_sch_p+16_r+128 | 0.7332 | 130,323 | 1440 |
  | build_full_sch_p+16_r+16 | 0.7137 | 130,955 | 1440 |
  | build_full_sch_p+16_r+2 | 0.7192 | 131,252 | 1440 |
  | build_full_sch_p+16_r+256 | 0.8075 | 137,667 | 1440 |
  | build_full_sch_p+16_r+32 | 0.7145 | 130,001 | 1440 |
  | build_full_sch_p+16_r+4 | 0.7191 | 131,234 | 1440 |
  | build_full_sch_p+16_r+64 | 0.7130 | 129,833 | 1440 |
  | build_full_sch_p+16_r+8 | 0.7172 | 130,814 | 1440 |
  | build_full_sch_p+1_r+0 | 0.7286 | 145,380 | 1440 |
  | build_full_sch_p+1_r+1 | 0.7183 | 145,166 | 1440 |
  | build_full_sch_p+1_r+128 | 0.6862 | 138,539 | 1440 |
  | build_full_sch_p+1_r+16 | 0.7264 | 144,406 | 1440 |
  | build_full_sch_p+1_r+2 | 0.7212 | 145,067 | 1440 |
  | build_full_sch_p+1_r+256 | 0.7051 | 135,150 | 1440 |
  | build_full_sch_p+1_r+32 | 0.7157 | 143,310 | 1440 |
  | build_full_sch_p+1_r+4 | 0.7253 | 145,199 | 1440 |
  | build_full_sch_p+1_r+64 | 0.6988 | 141,554 | 1440 |
  | build_full_sch_p+1_r+8 | 0.7275 | 144,799 | 1440 |
  | build_full_sch_p+2_r+0 | 0.7269 | 143,917 | 1440 |
  | build_full_sch_p+2_r+1 | 0.7267 | 143,699 | 1440 |
  | build_full_sch_p+2_r+128 | 0.6865 | 136,793 | 1440 |
  | build_full_sch_p+2_r+16 | 0.7120 | 143,054 | 1440 |
  | build_full_sch_p+2_r+2 | 0.7212 | 143,679 | 1440 |
  | build_full_sch_p+2_r+256 | 0.6935 | 133,726 | 1440 |
  | build_full_sch_p+2_r+32 | 0.7058 | 141,748 | 1440 |
  | build_full_sch_p+2_r+4 | 0.7208 | 143,436 | 1440 |
  | build_full_sch_p+2_r+64 | 0.6982 | 139,854 | 1440 |
  | build_full_sch_p+2_r+8 | 0.7137 | 143,569 | 1440 |
  | build_full_sch_p+32_r+0 | 0.8449 | 148,034 | 1440 |
  | build_full_sch_p+32_r+1 | 0.8451 | 148,122 | 1440 |
  | build_full_sch_p+32_r+128 | 0.9048 | 157,122 | 1440 |
  | build_full_sch_p+32_r+16 | 0.8499 | 148,696 | 1440 |
  | build_full_sch_p+32_r+2 | 0.8456 | 147,981 | 1440 |
  | build_full_sch_p+32_r+256 | 0.9722 | 170,482 | 1440 |
  | build_full_sch_p+32_r+32 | 0.8542 | 149,540 | 1440 |
  | build_full_sch_p+32_r+4 | 0.8453 | 148,169 | 1440 |
  | build_full_sch_p+32_r+64 | 0.8713 | 152,021 | 1440 |
  | build_full_sch_p+32_r+8 | 0.8477 | 148,346 | 1440 |
  | build_full_sch_p+4_r+0 | 0.7078 | 141,038 | 1440 |
  | build_full_sch_p+4_r+1 | 0.7090 | 140,566 | 1440 |
  | build_full_sch_p+4_r+128 | 0.6769 | 133,877 | 1440 |
  | build_full_sch_p+4_r+16 | 0.6954 | 139,664 | 1440 |
  | build_full_sch_p+4_r+2 | 0.7041 | 140,940 | 1440 |
  | build_full_sch_p+4_r+256 | 0.6863 | 131,785 | 1440 |
  | build_full_sch_p+4_r+32 | 0.6957 | 138,963 | 1440 |
  | build_full_sch_p+4_r+4 | 0.6999 | 140,884 | 1440 |
  | build_full_sch_p+4_r+64 | 0.6870 | 137,225 | 1440 |
  | build_full_sch_p+4_r+8 | 0.6999 | 140,359 | 1440 |
  | build_full_sch_p+64_r+0 | 1.0993 | 219,242 | 1440 |
  | build_full_sch_p+64_r+1 | 1.1006 | 219,755 | 1440 |
  | build_full_sch_p+64_r+128 | 1.1409 | 232,673 | 1440 |
  | build_full_sch_p+64_r+16 | 1.1054 | 220,717 | 1440 |
  | build_full_sch_p+64_r+2 | 1.0995 | 219,636 | 1440 |
  | build_full_sch_p+64_r+256 | 1.1752 | 246,712 | 1440 |
  | build_full_sch_p+64_r+32 | 1.1114 | 222,503 | 1440 |
  | build_full_sch_p+64_r+4 | 1.1016 | 220,170 | 1440 |
  | build_full_sch_p+64_r+64 | 1.1207 | 225,617 | 1440 |
  | build_full_sch_p+64_r+8 | 1.1015 | 219,656 | 1440 |
  | build_full_sch_p+8_r+0 | 0.7010 | 135,864 | 1440 |
  | build_full_sch_p+8_r+1 | 0.7046 | 135,827 | 1440 |
  | build_full_sch_p+8_r+128 | 0.6742 | 130,239 | 1440 |
  | build_full_sch_p+8_r+16 | 0.6933 | 134,612 | 1440 |
  | build_full_sch_p+8_r+2 | 0.7004 | 136,091 | 1440 |
  | build_full_sch_p+8_r+256 | 0.7161 | 130,905 | 1440 |
  | build_full_sch_p+8_r+32 | 0.6872 | 134,123 | 1440 |
  | build_full_sch_p+8_r+4 | 0.7043 | 135,670 | 1440 |
  | build_full_sch_p+8_r+64 | 0.6800 | 132,693 | 1440 |
  | build_full_sch_p+8_r+8 | 0.7019 | 135,562 | 1440 |

## Phase 11 — `adjust_r`/`adjust_p` (incumbent-ls-only gap 기반) (5/4 ~ 5/5)

기간 commit: `3114c22`, `fe84f43`, `3585387`, `b04d5ac`, `63be23c`, `5e392dd`, `7d4ab42`, `68a88b0`, `e73ec15`(merge), `012eb2b`, `cf94091`

핵심 작업

- `feat(mcf-lb): adjust r by incumbent-ls-only gap` (`3114c22`) — incumbent와 last-stage-only schedule의 gap을 보고 release date(r) 보정. 두 패스 흐름: 첫 LB → ls-only → full sch → 두 번째 `apply_lb_by_mcf` (with `adjust_r_by_full_sch_and_last_stage_only_sch: true`)에서 r 재조정.
- `feat(mcf-lb): adjust p by incumbent-ls-only gap` (`fe84f43`) — 같은 컨셉의 processing time 보정.
- `feat(mcf-lb): add log_level param to pm_pmtn sort` (`b04d5ac`).
- `feat(mcf-lb): add exp-35 config, fix r_adjust log` (`63be23c`).
- `remove class MCFLBResult` (`5e392dd`).
- `feat(mcf-lb): use release floor for desired_start` (`012eb2b`) — adjust_r의 lower bound로 원래 release.
- `feat(mcf-lb): add adjust_r_by_half option` (`cf94091`) — gap 절반만 보정.

### RUN 30 — `20260504T135233_268173` (mso02)

- **Config**: `metadata/20260504/mcf_lb_init_adjust_rj_1_config.yaml`
- **변경**: 단일 시나리오 `build_full_sch_p+0_r_aujust` (오타 `aujust`). 흐름: `apply_lb_by_mcf` → heuristic ls-only → `build_full_sch_from_last_stage_only_sch` → **두 번째 `apply_lb_by_mcf` (with `adjust_r_by_full_sch_and_last_stage_only_sch: true`)** → 두 번째 heuristic ls-only (with adjust_r) → build_full_sch. p_inc=0.
- **의도**: `adjust_r` 첫 풀-벤치 단발 체크 — 두 번째 패스 개선폭 측정.
- **비고**: `output_subdir`은 `build_full_sch_p+0_r+0` (이름 오타 별개).

- **결과**:

  | scenarioName | mean RPDf | mean bestObj | n |
  |---|---|---|---|
  | build_full_sch_p+0_r_aujust | 0.6639 | 134,112 | 1440 |

### RUN 31 — `20260504T142221_504713` (mso02)

- **Config**: `metadata/20260504/mcf_lb_init_adjust_rj_2_config.yaml`
- **변경**: 시나리오 7개 — `build_full_sch_p+{0,1,2,4,8,16,32}_r_adjust`. RUN 30 흐름의 p_inc 0~32 sweep.
- **의도**: adjust_r 효과의 p_inc 의존성 탐색.

- **결과**:

  | scenarioName | mean RPDf | mean bestObj | n |
  |---|---|---|---|
  | build_full_sch_p+0_r_adjust | 0.6639 | 134,112 | 1440 |
  | build_full_sch_p+16_r_adjust | 0.6974 | 127,817 | 1440 |
  | build_full_sch_p+1_r_adjust | 0.6426 | 132,775 | 1440 |
  | build_full_sch_p+2_r_adjust | 0.6469 | 131,109 | 1440 |
  | build_full_sch_p+32_r_adjust | 0.8411 | 147,761 | 1440 |
  | build_full_sch_p+4_r_adjust | 0.6295 | 128,928 | 1440 |
  | build_full_sch_p+8_r_adjust | 0.6385 | 126,186 | 1440 |

### RUN 32 — `20260505T014813_804225` (mso02)

- **Config**: `metadata/20260504/mcf_lb_init_35_config.yaml`
- **변경**: 시나리오 2개 — `build_full_sch_p_adjust`, `build_full_sch_p_adjust_r_adjust`.
- **의도**: `adjust_p` 신규 노브 단독 효과 + `adjust_r` 결합 효과 첫 측정.

- **결과**:

  | scenarioName | mean RPDf | mean bestObj | n |
  |---|---|---|---|
  | build_full_sch_p_adjust | 0.6468 | 130,647 | 1440 |
  | build_full_sch_p_adjust_r_adjust | 0.6704 | 138,128 | 1440 |

### RUN 33 — `20260505T025805_689859` (mso02)

- **Config**: `metadata/20260504/mcf_lb_init_36_config.yaml`
- **변경**: 시나리오 6개 — `build_full_sch_{base, p_adjust, r_adjust, p_adjust_r_adjust, r_half_adjust, p_adjust_r_half_adjust}`. base는 단일 패스(adjust 없음). `adjust_r_by_half: true` 옵션 추가.
- **의도**: 6-조합 정식 비교 — base 대비 어느 adjust가 가장 효과적인지.

---

- **결과**:

  | scenarioName | mean RPDf | mean bestObj | n |
  |---|---|---|---|
  | build_full_sch_base | 0.7393 | 147,085 | 1440 |
  | build_full_sch_p_adjust | 0.6468 | 130,647 | 1440 |
  | build_full_sch_p_adjust_r_adjust | 0.6704 | 138,128 | 1440 |
  | build_full_sch_p_adjust_r_half_adjust | 0.6237 | 127,669 | 1440 |
  | build_full_sch_r_adjust | 0.6639 | 134,112 | 1440 |
  | build_full_sch_r_half_adjust | 0.6685 | 134,288 | 1440 |

## Phase 12 — `_only_pmtn_sch` 변종 + dispatch 양방향 + composite step `calc_mcf_lb_and_derive_full_sch` (5/5)

기간 commit: `0a8a9b0`, `c039ceb`, `af944e3`, 그리고 vscode setting `7f8f351`/`ffd651d`.

핵심 작업

- `feat(mcf-lb): add adjust_*_only_pmtn_sch flags` (`0a8a9b0`) — adjust 입력을 **last-stage-only 결과**가 아니라 **MCF preemptive schedule**에서 직접 읽는 변종 (`adjust_p_by_full_sch_and_last_stage_only_pmtn_sch` / `..._only_pmtn_sch`). 기존 `_only_sch`와 mutually exclusive (ValueError). 새 `MCFLBDiagnostic.adjust_params_last_stage_only_pmtn_makespan` + CSV 컬럼. body: `mcf_lb_init_37` 신설.
- `refactor(mcf-lb): try both dispatch orderings` (`c039ceb`) — `machine_then_job` 인자 제거. Phase 3가 `machine_then_job=False`와 `True`를 모두 시도해서 lower-makespan 결과 채택, 동률은 `False`.
- `feat(mcf-lb): add calc_mcf_lb_and_derive_full_sch` (`af944e3`) — 새 composite subroutine: round 1은 항상(MCF→heuristic→full-sch), round 2는 `adjust_p or adjust_r`이고 raw `incumbent − preemptive` delta > 0일 때만 발동 → 무의미한 두 번째 패스 스킵. `mcf_lb_init_38_config.yaml` (4 시나리오 — adjust_none/p/r/pr).

### RUN 34 — `20260505T102202_582058` (mso02)

- **Config**: `metadata/20260505/mcf_lb_init_37_config.yaml`
- **변경**: 시나리오 6개 (RUN 33과 동일 명명) — adjust 입력이 **`_only_pmtn_sch`** 변종으로 교체. 두 번째 패스에서 last-stage-only 결과 대신 preemptive schedule을 직접 본다.
- **의도**: adjust의 입력 source(ls-only schedule vs preemptive schedule)가 결과에 미치는 영향 측정 — RUN 33과 직접 비교.

- **결과**:

  | scenarioName | mean RPDf | mean bestObj | n |
  |---|---|---|---|
  | build_full_sch_base | 0.7393 | 147,085 | 1440 |
  | build_full_sch_p_adjust | 0.6339 | 127,995 | 1440 |
  | build_full_sch_p_adjust_r_adjust | 0.6534 | 134,855 | 1440 |
  | build_full_sch_p_adjust_r_half_adjust | 0.6168 | 126,004 | 1440 |
  | build_full_sch_r_adjust | 0.6720 | 136,168 | 1440 |
  | build_full_sch_r_half_adjust | 0.6714 | 134,373 | 1440 |

### RUN 35 — `20260505T191440_984385` (mso02)

- **Config**: 동일 (`mcf_lb_init_37_config.yaml`)
- **변경**: 직전 commit `c039ceb refactor(mcf-lb): try both dispatch orderings` — Phase 3 dispatch가 양방향 시도 후 best 선택.
- **의도**: dispatch ordering try-both로 RUN 34 결과가 어떻게 달라지는지 — 동일 시나리오·config 기준 알고리즘 변화 영향 격리.

- **결과**:

  | scenarioName | mean RPDf | mean bestObj | n |
  |---|---|---|---|
  | build_full_sch_base | 0.7281 | 156,882 | 1440 |
  | build_full_sch_p_adjust | 0.6221 | 135,928 | 1440 |
  | build_full_sch_p_adjust_r_adjust | 0.5973 | 130,521 | 1440 |
  | build_full_sch_p_adjust_r_half_adjust | 0.5889 | 127,423 | 1440 |
  | build_full_sch_r_adjust | 0.6452 | 137,918 | 1440 |
  | build_full_sch_r_half_adjust | 0.6687 | 144,111 | 1440 |

### RUN 36 — `20260505T192009_887337` (mso02)

- **Config**: `metadata/20260505/mcf_lb_init_38_config.yaml`
- **변경**: 시나리오 4개 — `calc_mcf_lb_and_derive_full_sch_adjust_{none, p, r, pr}`. 단일 composite step `calc_mcf_lb_and_derive_full_sch`로 6-step YAML 흐름을 갈음. round 2는 delta>0일 때만 발동 → no-op 패스 스킵.
- **의도**: composite step의 정확도/속도 절감 효과를 4-조합으로 검증. RUN 34/35의 6-step YAML 흐름 대비.

---

- **결과**:

  | scenarioName | mean RPDf | mean bestObj | n |
  |---|---|---|---|
  | calc_mcf_lb_and_derive_full_sch_adjust_none | 0.7281 | 156,882 | 1440 |
  | calc_mcf_lb_and_derive_full_sch_adjust_p | 0.6221 | 135,928 | 1440 |
  | calc_mcf_lb_and_derive_full_sch_adjust_pr | 0.5889 | 127,423 | 1440 |
  | calc_mcf_lb_and_derive_full_sch_adjust_r | 0.6687 | 144,111 | 1440 |

## 결과 요약

**Source**: `analysis/results_index_20260505.csv` → `scripts/aggregate_results_index.py` → `analysis/results_index_20260505_agg.json`. 35 RUN × **1440 인스턴스 전부** (BKS=0 인 58개 인스턴스 포함). RUN 15는 hjt5950x 산출물 미보유로 빌더에서 제외.

### Top 20 시나리오 (mean RPDf 오름차)

| 순위 | RUN | timestamp | scenarioName | mean RPDf | mean bestObj |
|---|---|---|---|---|---|
| 1 | 1 | `20260429T233115_006438` | `best` | 0.2740 | 119,870 |
| 2 | 2 | `20260430T110852_547352` | `best` | 0.2741 | 119,695 |
| 3 | 1 | `20260429T233115_006438` | `neh_cp_bs15_linear_dplus2_pf1_tl024nc` | 0.3028 | 127,395 |
| 4 | 2 | `20260430T110852_547352` | `neh_cp_bs15_linear_dplus2_pf1_tl024nc` | 0.3113 | 127,493 |
| 5 | 35 | `20260505T191440_984385` | `build_full_sch_p_adjust_r_half_adjust` | 0.5889 | 127,423 |
| 6 | 36 | `20260505T192009_887337` | `calc_mcf_lb_and_derive_full_sch_adjust_pr` | 0.5889 | 127,423 |
| 7 | 35 | `20260505T191440_984385` | `build_full_sch_p_adjust_r_adjust` | 0.5973 | 130,521 |
| 8 | 34 | `20260505T102202_582058` | `build_full_sch_p_adjust_r_half_adjust` | 0.6168 | 126,004 |
| 9 | 35 | `20260505T191440_984385` | `build_full_sch_p_adjust` | 0.6221 | 135,928 |
| 10 | 36 | `20260505T192009_887337` | `calc_mcf_lb_and_derive_full_sch_adjust_p` | 0.6221 | 135,928 |
| 11 | 33 | `20260505T025805_689859` | `build_full_sch_p_adjust_r_half_adjust` | 0.6237 | 127,669 |
| 12 | 31 | `20260504T142221_504713` | `build_full_sch_p+4_r_adjust` | 0.6295 | 128,928 |
| 13 | 34 | `20260505T102202_582058` | `build_full_sch_p_adjust` | 0.6339 | 127,995 |
| 14 | 31 | `20260504T142221_504713` | `build_full_sch_p+8_r_adjust` | 0.6385 | 126,186 |
| 15 | 31 | `20260504T142221_504713` | `build_full_sch_p+1_r_adjust` | 0.6426 | 132,775 |
| 16 | 35 | `20260505T191440_984385` | `build_full_sch_r_adjust` | 0.6452 | 137,918 |
| 17 | 32 | `20260505T014813_804225` | `build_full_sch_p_adjust` | 0.6468 | 130,647 |
| 18 | 33 | `20260505T025805_689859` | `build_full_sch_p_adjust` | 0.6468 | 130,647 |
| 19 | 31 | `20260504T142221_504713` | `build_full_sch_p+2_r_adjust` | 0.6469 | 131,109 |
| 20 | 34 | `20260505T102202_582058` | `build_full_sch_p_adjust_r_adjust` | 0.6534 | 134,855 |

### Bottom 10 (mean RPDf 내림차)

| 순위 | RUN | scenarioName | mean RPDf | mean bestObj |
|---|---|---|---|---|
| 1 | 17 | `build_full_sch_r_mult_8_0` | 1.3013 | 297,069 |
| 2 | 19 | `build_full_sch_r_inc_4096` | 1.1978 | 296,152 |
| 3 | 19 | `build_full_sch_r_inc_2048` | 1.1853 | 271,323 |
| 4 | 29 | `build_full_sch_p+64_r+256` | 1.1752 | 246,712 |
| 5 | 29 | `build_full_sch_p+64_r+128` | 1.1409 | 232,673 |
| 6 | 29 | `build_full_sch_p+64_r+64` | 1.1207 | 225,617 |
| 7 | 29 | `build_full_sch_p+64_r+32` | 1.1114 | 222,503 |
| 8 | 29 | `build_full_sch_p+64_r+16` | 1.1054 | 220,717 |
| 9 | 29 | `build_full_sch_p+64_r+4` | 1.1016 | 220,170 |
| 10 | 29 | `build_full_sch_p+64_r+8` | 1.1015 | 219,656 |

### RUN별 최우수 시나리오 (mean RPDf 기준)

| RUN | best scenario | mean RPDf | mean bestObj |
|---|---|---|---|
| 1 | `best` | 0.2740 | 119,870 |
| 2 | `best` | 0.2741 | 119,695 |
| 3 | `mcf_lb_then_neh_cp_bs15_linear_pf1_skip0_mas1_tl024nc` | 0.7049 | 183,628 |
| 10 | `build_full_sch_1` | 1.0532 | 212,091 |
| 11 | `build_full_sch_1` | 0.7277 | 146,879 |
| 12 | `build_full_sch_1` | 0.7303 | 146,667 |
| 13 | `build_full_sch_p_inc_4` | 0.7007 | 140,727 |
| 14 | `build_full_sch_p_inc_16` | 0.7245 | 131,668 |
| 16 | `build_full_sch_p_inc_8` | 0.6997 | 135,857 |
| 17 | `build_full_sch_r_mult_1_5` | 0.7018 | 139,002 |
| 18 | `build_full_sch_p_inc_16_r_mult_2_0` | 0.7919 | 135,468 |
| 19 | `build_full_sch_r_inc_128` | 0.7088 | 140,583 |
| 20 | `build_full_sch_p+16_rx2+64` | 0.8301 | 140,761 |
| 22 | `build_full_sch_p+16_rx2+2` | 0.7913 | 135,549 |
| 23 | `build_full_sch_p_inc_0` | 0.7393 | 147,085 |
| 24 | `build_full_sch_p+16_rx2` | 0.7919 | 135,440 |
| 25 | `build_full_sch_p+16_rx2` | 0.7919 | 135,468 |
| 26 | `build_full_sch_p_inc_0` | 0.7348 | 146,914 |
| 27 | `build_full_sch_p+8_rx2+128` | 0.6742 | 130,239 |
| 28 | `build_full_sch_p+4_rx2+2` | 0.6956 | 131,478 |
| 29 | `build_full_sch_p+8_r+128` | 0.6742 | 130,239 |
| 30 | `build_full_sch_p+0_r_aujust` | 0.6639 | 134,112 |
| 31 | `build_full_sch_p+4_r_adjust` | 0.6295 | 128,928 |
| 32 | `build_full_sch_p_adjust` | 0.6468 | 130,647 |
| 33 | `build_full_sch_p_adjust_r_half_adjust` | 0.6237 | 127,669 |
| 34 | `build_full_sch_p_adjust_r_half_adjust` | 0.6168 | 126,004 |
| 35 | `build_full_sch_p_adjust_r_half_adjust` | 0.5889 | 127,423 |
| 36 | `calc_mcf_lb_and_derive_full_sch_adjust_pr` | 0.5889 | 127,423 |

### 결과 없음 (incumbent 미등록 — `mcfLb`만 채워진 RUN)

RUN 4, 5, 6, 7, 8, 9, 21 — Phase 3·4의 `single_pass_last_stage_only_sch_from_mcf_lb` 도입기 RUN(4~9)는 `hasIncumbent=False`, `reportCount=0`. controller가 `single_pass_*` 결과를 `AlgRecord` incumbent로 등록하는 경로가 미완이었던 것으로 보임. RUN 21은 `mcf_lb_only` (LB-only) — 설계상 schedule 없음.

이들 RUN은 모두 `mcfLb_mean ≈ 39,202` (1440 인스턴스).

### 주요 관찰

- **최우수**: RUN 1 `best` — mean RPDf 0.2740 (전주 best-of NEH-CP+MCF-LB 직렬 재현).
- **이번 주 알고리즘 라인의 최우수**: RUN 35 `build_full_sch_p_adjust_r_half_adjust` — mean RPDf 0.5889. 전주 `best` 대비 약 +0.31 RPDf — 신규 `apply_lb_by_mcf → heuristic_last_stage → build_full_sch + adjust_*` 라인은 아직 NEH-CP+MCF-LB 직렬에 못 미침.
- **Last-stage-only 단독 vs full-schedule 격차**: last-stage-only obj는 80.2%(1155/1440) 인스턴스에서 BKS 이김 (algorithms doc Q1 참고), full-schedule wET는 ~14% 인스턴스로 감소 — reverse-dispatch 단계의 손실이 알고리즘의 주된 약점.
- **`mcf_lb_then_neh_cp` 통합 step (RUN 3, RPDf 0.7330)**: 분리된 직렬 (RUN 1·2 `best`, RPDf 0.27)보다 훨씬 나쁨 — controller-level 통합이 NEH-CP에 넘기는 sequence/seed 정보를 일부 잃은 것으로 추정.
- **p_increment 효과** (RUNs 13/14/16): p_inc 0→8 까지는 RPDf 미세 개선, 16부터 turnaround, 64에서 RPDf 1.06 폭락.
- **r_multiplier 효과** (RUN 17): 1.0→1.5 까지 개선, 2.0부터 악화. r_mult 8.0에서 1.30로 최악.
- **r_increment 효과** (RUN 19): r_inc 0~256 plateau, 512부터 악화, 4096에서 1.20.
- **p×r 그리드** (RUN 27/28/29): 최저 RPDf ≈ 0.66 영역 (p+8, r+128 근방). p_inc≥32에서 일관 악화.
- **adjust_*** (RUNs 30~36): `p_adjust + r_half_adjust` 조합이 가장 우수 (RUN 35: 0.5889). single-pass `base` 대비 약 0.13~0.20 RPDf 개선.
- **`_only_pmtn_sch` 변종** (RUN 33→34): adjust 입력을 last-stage-only schedule → preemptive schedule로 교체. p_adjust 계열 -0.005~-0.018 개선, r_adjust 단독 +0.007 약간 후퇴 (mixed).
- **dispatch try-both** (RUN 34→35): 같은 config 위에서 6 시나리오 모두 개선, 폭은 -0.001(`r_half_adjust`)~ -0.057(`p_adjust_r_adjust`).
- **composite step `calc_mcf_lb_and_derive_full_sch`** (RUN 36): 4 시나리오 결과가 RUN 35와 동치 (`adjust_pr` 0.5889 = RUN 35 `p_adjust_r_half_adjust`). round 2 스킵 로직이 결과를 망치지 않으면서 6-step YAML과 등가.
- **`4ca477d` perf change** (RUNs 23·24·25·26): 시간만 단축, wET 결과 거의 동일 — `build_full_sch_p_inc_0` (RUN 23/26): 0.6863/0.6914, `build_full_sch_p+16_rx2` (RUN 24/25): 0.7338/0.7352. body 메모상 60.90→33.05s, 83.24→36.72s (~46~56% 단축).

---

## 큰 흐름 요약

1. **Phase 1 (4/29~30)** — Logging/orchestration 인프라(scoped handlers, ArtifactLayout) + 전주 best-of NEH-CP/MCF-LB 재현성 확인.
2. **Phase 2 (5/1)** — "MCF-LB seed → NEH-CP" 직렬을 단일 step `mcf_lb_then_neh_cp`로 통합.
3. **Phase 3 (5/1~2)** — last-stage-only step의 4-단계 진화: NEH-CP at last stage(RUN 4) → +placement_priority(RUN 5) → single-pass로 통합(RUN 6) → SSOT refactor 후 재실행(RUN 7).
4. **Phase 4 (5/2)** — pm-sort key 확장(`end_time`, `*_maxw`) + placement_priority(`contrib`/`dist`) 6-셀 스윕.
5. **Phase 5 (5/2)** — `build_full_sch_from_last_stage_only_sch` step 신설 — last-stage 결과를 전 stage로 확장. 직후 디버깅 사이클 3 RUN.
6. **Phase 6 (5/3)** — `p_increment` 노브, 0/1/2/4/8 → 16/32 스윕.
7. **Phase 7 (5/3)** — `heuristic_last_stage_only_sch_from_mcf_lb` (CP-SAT 없는 빠른 변종) — hjt5950x에서 첫 시험(RUN 15) → mso02 재실행(RUN 16).
8. **Phase 8 (5/3)** — `r_multiplier` 노브 + (p_inc, r_mult) 단발 결합 체크.
9. **Phase 9 (5/3~4)** — `r_increment` 노브 + `mcf_lb_only` baseline + `t_max` perf 변경의 A/B/B/A timing 측정 (RUN 23~26: ~46~56% 시간 단축).
10. **Phase 10 (5/4)** — p×r 그리드: 60셀(r_mult=1.0) → 60셀(r_mult=2.0) → 80셀(p,r 확장).
11. **Phase 11 (5/4~5)** — `adjust_r`/`adjust_p` (incumbent-ls-only gap 기반 두-패스). 단발 → sweep → 6-조합 정식 비교 순서.
12. **Phase 12 (5/5)** — adjust 입력 source 변경(`_only_pmtn_sch`), Phase 3 dispatch 양방향, composite step `calc_mcf_lb_and_derive_full_sch`로 무의미 패스 스킵. Phase 11과의 비교가 다음 단계 결정의 입력.

핵심 알고리즘 변화의 축은 (i) LB→last-stage→full-sch 3-step 정착, (ii) p/r 노브로 release date·processing time 변형, (iii) 단일 패스 → "두 패스 + adjust" → "delta>0일 때만 round 2" 진화. 한편 perf 면에서는 `4ca477d`의 t_max tightening이 mcf_lb_only/build_full_sch 모두 약 절반으로 단축.

---

## Appendix A — Output Layout

(전주 review와 동일 — `docs/io/20260429_artifact_manager.md`의 `ArtifactLayout` 스키마.)

표준 파일 (모든 RUN 공통)

- `<timestamp>_main.log` — 단일 마스터 로그 (이번 주 RUN 1부터 scoped handler 적용).
- `<timestamp>_summary.csv` — per-(instance × scenario) 메인 결과. Appendix B 참고.
- `<timestamp>_report.xlsx` — `FFcDDWReporter` 분석 시트.
- `<timestamp>_rpdf_comparison.csv` — `summary.csv` + `pra2017_bks_table.csv` join + `RPDf_BKS_data` 미리 계산. **결과 비교의 1차 입력**.
- `<timestamp>_rpdf_dashboard.html` 등 PivotTable.js 대시보드.
- `<scenario>_statistics.yaml`, `<scenario>/<instance_name>/...` — 시나리오/인스턴스 단위 산출물.

비고

- RUN 15 (`20260503T170658_834025`)는 hjt5950x 머신 산출물 — 본 저장소 `output/`에 없음. 결과 비교 시 별도 머신에서 가져오거나 RUN 16(`20260503T181635_000784`, mso02 동일 config)으로 갈음.
- RUN 21 (`mcf_lb_only`) 및 RUN 23·26 (`mcf_lb_only` t_max A/B)은 `bestObj = mcfLb`로 채워지고 schedule은 만들어지지 않음 → `dispatchedObj`/`profileFixObj` 비어 있음.
- RUN 22~26은 `4ca477d` perf change A/B를 위한 단일-시나리오 측정. `n=1440 × 1 scen`.
- RUN 30~33은 6-step YAML 흐름 (LB→ls-only→full-sch×2 패스). RUN 34~35는 같은 6-step이지만 adjust 입력이 `_only_pmtn_sch`. RUN 36은 단일 composite step `calc_mcf_lb_and_derive_full_sch`로 갈음.

---

## Appendix B — Result Columns

(전주 review와 동일. 새 컬럼 변동: `01fd564 refactor(orchestration): rework summary metrics` 이후 RUN 19~36은 summary 컬럼이 약간 바뀔 수 있음. `0a8a9b0`로 `lastStageOnlyPmtnMakespan` 컬럼 추가 — RUN 34부터.)

핵심 컬럼

- `bestObj` — wET 기준 최종 obj_value (`AlgRecord.obj_value`).
- `mcfLb` — MCF preemptive LB (모든 `apply_lb_by_mcf` 계열).
- `lastStageOnlyObj`, `lastStageOnlyBound` — last-stage-only schedule 결과.
- `lastStageOnlyPmtnMakespan` — preemptive schedule makespan (RUN 34~).
- `mcfSolveSec`, `lastStageCpSatSec` 등 — 단계별 wall time.
- `RPDf_BKS_data = (bestObj − BKS_data) / ((bestObj + BKS_data)/2)` — primary 비교 메트릭.

---

## Appendix C — Cross-RUN Scenario Equivalence Map

**기준 시나리오 (LB→ls-only→full-sch 3-step, p_inc=0, r_mult=1.0, r_inc=0)**

| RUN | scenarioName | last-stage step |
|---|---|---|
| 10 | `build_full_sch_1` | single_pass (CP-SAT) |
| 11 | `build_full_sch_1` | single_pass (post arg-fix/heatmap-dir/ddw-cache) |
| 12 | `build_full_sch_1` | single_pass (post delay-flip) |
| 13 | `build_full_sch_p_inc_0` | single_pass |
| 16 | `build_full_sch_p_inc_0` | **heuristic** |
| 17 | `build_full_sch_r_mult_1_0` | heuristic |
| 27 | `build_full_sch_p+0_rx2+0` (실은 r_mult=1.0) | heuristic |
| 29 | `build_full_sch_p+0_r+0` | heuristic |
| 33 | `build_full_sch_base` | heuristic, 단일 패스 |
| 36 | `calc_mcf_lb_and_derive_full_sch_adjust_none` | composite step (round 1만) |

**(p_inc, r_inc) 그리드 — heuristic, r_mult=1.0**

| (p_inc, r_inc) | RUN |
|---|---|
| (0~16, 0~256) — 60셀 | RUN 27 |
| (0~64, 0~256) — 80셀 | RUN 29 |

— RUN 27 ⊂ RUN 29 영역. 둘 사이에는 commit 없음 (5/4 새벽 직후), 직접 비교 가능.

**(p_inc, r_inc) 그리드 — heuristic, r_mult=2.0**

| (p_inc, r_inc) | RUN |
|---|---|
| (0~16, 0~256) — 60셀 | RUN 28 |
| (p+16, rx2+0~32) — 7셀 | RUN 22 |
| (p+16, rx2+64~256) — 3셀 | RUN 20 |
| (p+16, rx2+0) 단일 | RUN 18 |

— RUN 18·20·22는 RUN 28의 부분 셀 — RUN 28 산출 전 빠른 라인 스캔. 이들 사이에 `4ca477d` perf change가 있어 절대 timing은 다르지만 결과(wET)는 동일해야 함.

**`mcf_lb_only` (LB-only, schedule 없음)**

| RUN | timestamp | 비고 |
|---|---|---|
| 21 | 20260504T004917_785558 | 베이스라인 |
| 23 | 20260504T030753_945843 | `4ca477d` AFTER timing |
| 26 | 20260504T032002_269531 | `4ca477d` BEFORE timing (revert) |

— RUN 21·23·26은 같은 1440 인스턴스 위 동일 알고리즘. wET는 동일해야 하고 차이는 timing뿐.

**`adjust_*` 변종**

| 시나리오 의미 | RUN 30 | RUN 31 | RUN 32 | RUN 33 | RUN 34 | RUN 35 | RUN 36 |
|---|---|---|---|---|---|---|---|
| baseline (단일 패스) | — | — | — | `build_full_sch_base` | `build_full_sch_base` | `build_full_sch_base` | `..._adjust_none` |
| adjust_r single (p+0) | `..._aujust` | — | — | — | — | — | — |
| adjust_r p_inc sweep | — | `p+{0..32}_r_adjust` | — | — | — | — | — |
| adjust_p only | — | — | `p_adjust` | `p_adjust` | `p_adjust` | `p_adjust` | `..._adjust_p` |
| adjust_p+r | — | — | `p_adjust_r_adjust` | `p_adjust_r_adjust` | 동일 | 동일 | `..._adjust_pr` |
| adjust_r only | — | — | — | `r_adjust` | `r_adjust` | `r_adjust` | `..._adjust_r` |
| adjust_r_half | — | — | — | `r_half_adjust` | `r_half_adjust` | `r_half_adjust` | — |
| adjust_p + r_half | — | — | — | `p_adjust_r_half_adjust` | `p_adjust_r_half_adjust` | `p_adjust_r_half_adjust` | — |

— RUN 33→34: 같은 6 시나리오, adjust 입력 source가 `_only_sch`(last-stage-only) → `_only_pmtn_sch`(preemptive schedule)로 교체.
— RUN 34→35: config 동일, `c039ceb` (dispatch try-both) 적용 후.
— RUN 36: 6-step YAML 흐름을 단일 composite step으로 갈음 (4 시나리오, `r_half` 변종 제외).

---

## Appendix D — Results Index Builder

전주 `scripts/build_results_index.py`는 RUN 1~23 (전주)에 hardcoded. 이번 주 RUN 24~59(=신규 36개)를 추가하려면 같은 패턴으로 36 row를 `RUNS` 리스트에 append (또는 별도 `build_results_index_20260505.py` 신설).

추가 row 템플릿 (전주 23개 다음에 이어서; output_date_dir는 위 Run Index 표 참고):

```python
(24, "20260429T233115_006438", "20260429", "metadata/20260429/20260429_config.yaml",                       "e792136", "mso02",    "full"),
(25, "20260430T110852_547352", "20260429", "metadata/20260429/20260429_config.yaml",                       "a9981c1", "mso02",    "full"),
(26, "20260501T162650_028232", "20260501", "metadata/20260501/20260501_mcf_lb_then_neh_cp_config.yaml",    "c675c1a", "mso02",    "full"),
(27, "20260502T002742_596323", "20260501", "metadata/20260501/mcf_lb_init_19_config.yaml",                 "9e74acc", "mso02",    "full"),
(28, "20260502T025451_273045", "20260501", "metadata/20260501/mcf_lb_init_20_config.yaml",                 "f31fc87", "mso02",    "full"),
(29, "20260502T032313_670203", "20260501", "metadata/20260501/mcf_lb_init_21_config.yaml",                 "c43f87b", "mso02",    "full"),
(30, "20260502T131546_402074", "20260501", "metadata/20260501/mcf_lb_init_21_config.yaml",                 "9418208", "mso02",    "full"),
(31, "20260502T133412_116270", "20260502", "metadata/20260502/mcf_lb_init_22_config.yaml",                 "8304421", "mso02",    "full"),
(32, "20260502T145150_590013", "20260502", "metadata/20260502/mcf_lb_init_22_config.yaml",                 "051d8e1", "mso02",    "full"),
(33, "20260502T165007_640181", "20260502", "metadata/20260502/mcf_lb_init_23_config.yaml",                 "4f7fb0f", "mso02",    "full"),
(34, "20260502T184531_518809", "20260502", "metadata/20260502/mcf_lb_init_23_config.yaml",                 "f3f0e73", "mso02",    "full"),
(35, "20260502T193831_290902", "20260502", "metadata/20260502/mcf_lb_init_23_config.yaml",                 "6ecd356", "mso02",    "full"),
(36, "20260503T022442_340817", "20260502", "metadata/20260502/mcf_lb_init_24_config.yaml",                 "bab11e8", "mso02",    "full"),
(37, "20260503T170549_147724", "20260503", "metadata/20260503/mcf_lb_init_25_config.yaml",                 "5c4920a", "mso02",    "full"),
# RUN 38: hjt5950x 산출물 — 본 저장소 output/에 없음. summary.csv 가져오면 활성화.
# (38, "20260503T170658_834025", "20260503", "metadata/20260503/mcf_lb_init_26_config.yaml",                 "b565889", "hjt5950x", "full"),
(39, "20260503T181635_000784", "20260503", "metadata/20260503/mcf_lb_init_26_config.yaml",                 "725a912", "mso02",    "full"),
(40, "20260503T191906_135722", "20260503", "metadata/20260503/mcf_lb_init_27_config.yaml",                 "c4a790f", "mso02",    "full"),
(41, "20260503T215803_006004", "20260503", "metadata/20260503/mcf_lb_init_28_config.yaml",                 "33cce02", "mso02",    "full"),
(42, "20260503T230126_683476", "20260503", "metadata/20260503/mcf_lb_init_29_config.yaml",                 "f89ba73", "mso02",    "full"),
(43, "20260504T003732_433340", "20260503", "metadata/20260503/mcf_lb_init_30_config.yaml",                 "9df77f8", "mso02",    "full"),
(44, "20260504T004917_785558", "20260503", "metadata/20260503/mcf_lb_only_config.yaml",                    "3d546da", "mso02",    "full"),
(45, "20260504T010002_965646", "20260503", "metadata/20260503/mcf_lb_init_31_config.yaml",                 "3d07d20", "mso02",    "full"),
(46, "20260504T030753_945843", "20260503", "metadata/20260503/mcf_lb_only_config.yaml",                    "4ca477d", "mso02",    "full"),
(47, "20260504T031049_337896", "20260503", "metadata/20260503/mcf_lb_init_31_config.yaml",                 "4ca477d", "mso02",    "full"),
(48, "20260504T031422_467379", "20260503", "metadata/20260503/mcf_lb_init_31_config.yaml",                 "4ca477d", "mso02",    "full"),
(49, "20260504T032002_269531", "20260503", "metadata/20260503/mcf_lb_only_config.yaml",                    "4ca477d", "mso02",    "full"),
(50, "20260504T032732_697925", "20260503", "metadata/20260503/mcf_lb_init_32_config.yaml",                 "b090f84", "mso02",    "full"),
(51, "20260504T082749_666067", "20260504", "metadata/20260504/mcf_lb_init_33_config.yaml",                 "1abc4f4", "mso02",    "full"),
(52, "20260504T093058_016949", "20260504", "metadata/20260504/mcf_lb_init_34_config.yaml",                 "bff5eac", "mso02",    "full"),
(53, "20260504T135233_268173", "20260504", "metadata/20260504/mcf_lb_init_adjust_rj_1_config.yaml",        "78c6756", "mso02",    "full"),
(54, "20260504T142221_504713", "20260504", "metadata/20260504/mcf_lb_init_adjust_rj_2_config.yaml",        "a0a6974", "mso02",    "full"),
(55, "20260505T014813_804225", "20260504", "metadata/20260504/mcf_lb_init_35_config.yaml",                 "07d7e7f", "mso02",    "full"),
(56, "20260505T025805_689859", "20260504", "metadata/20260504/mcf_lb_init_36_config.yaml",                 "445bf53", "mso02",    "full"),
(57, "20260505T102202_582058", "20260505", "metadata/20260505/mcf_lb_init_37_config.yaml",                 "0a8a9b0", "mso02",    "full"),
(58, "20260505T191440_984385", "20260505", "metadata/20260505/mcf_lb_init_37_config.yaml",                 "c039ceb", "mso02",    "full"),
(59, "20260505T192009_887337", "20260505", "metadata/20260505/mcf_lb_init_38_config.yaml",                 "af944e3", "mso02",    "full"),
```

```
uv run python scripts/build_results_index.py
# → analysis/results_index_20260505.csv (long-form: timestamp × scenario × instance)
```

산출 컬럼은 전주와 동일 (`runNumber`, `timestamp`, `sourceCommit`, `machine`, `scope`, `configFile`, `outputDir`, + `summary.csv` 컬럼 + `insIndex`, `n`, `c`, `T`, `R`, `W`, `BKS_data`, `RPDf_BKS_data`).

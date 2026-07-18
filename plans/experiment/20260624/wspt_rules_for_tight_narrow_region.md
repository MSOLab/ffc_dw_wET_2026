# T=0.6,R=0.2 영역용 WSPT 계열 dispatch 규칙 추가

## 동기 / 가설

- 분석 run `20260624T204200_491609`에서 **T=0.6,R=0.2** 영역(160 instance)의
  챔피언은 `w1` (sd_w1 평균 RPDf 0.5725, 160 중 111승). oracle 0.5314.
- T=0.6(빡빡한 due) + R=0.2(좁은 산포) → 모든 job의 due date가 이른 시점에 군집 →
  대부분 **tardy** → 사실상 **total weighted tardiness** 문제.
- 단일기계 가중지연 최소화에서 모든 job이 지연일 때 최적은 **WSPT**
  (`w⁺/p` 내림차순). 현 챔피언 `w1`은 `(w⁺−w⁻)` 내림차순으로 **처리시간을
  완전히 무시** → WSPT 보정으로 이길 여지가 있다.

## 추가 규칙 (ffc_ddw_params.py)

P_j = Σ_i p_{ij} (`job_2_stage_2_p_map` 합), p_last_j = 마지막 stage 처리시간.
모두 native position tie-break, 내림차순.

1. `get_wspt_twt_job_sequence`  — key: `w⁺_j / P_j`  (정통 WSPT)
2. `get_wspt_net_job_sequence`  — key: `(w⁺_j − w⁻_j) / P_j`  (w1 net weight의 WSPT 보정)
3. `get_wspt_net_last_job_sequence` — key: `(w⁺_j − w⁻_j) / p_last_j` (마지막 stage 병목 proxy)

## sorter.py

- `DispatchSeqKey` literal에 `wspt_twt`, `wspt_net`, `wspt_net_last` 추가.
- `dispatch_seq_job_sequence`의 `direct` dict에 3개 매핑 추가.

## logging (controller.py)

- `_log_dispatch_seed_diagnostics(label, schedule)` 추가: seed schedule의
  가중 E/T, `T/(E+T)`, early/ontime/tardy job 수 + tardy% 를 `logger.debug`로 기록.
- `initialize_by_simple_dispatch`(label `sd:<seq>`)와
  `_initialize_by_reversed_sequence`(`diag_label` 인자 추가, `rd:<seq>`)에서
  **`_register` 직후** 호출 → step contract(1 register, tight elapsed) 준수.
- 목적: "T=0.6,R=0.2 → tardiness 지배" 가설 검증 + 새 규칙이 E/T 균형을
  어떻게 바꾸는지 관찰.

## 실험

- targeted config `metadata/20260624/wspt_region_probe_config.yaml`:
  region 160 instance(ins_index 명시) × { sd/rd_w1, sd_weight_due_pos(참조),
  sd/rd × {wspt_twt, wspt_net, wspt_net_last} }, timelimit `0.09nc`.
- 평가: 영역 평균 RPDf와 글로벌-best 승수로 w1 대비 우열 판정.
- w1을 이기는 규칙이 나오면 full sweep config 포트폴리오에 추가.

## 검증

- `uv run ruff check`, `uv run ruff format`.
- 기존 scenario(`sd_w1` 등) 결과가 재현되는지 region run에서 교차 확인.

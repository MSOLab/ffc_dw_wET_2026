# coarse-exact `insert_idle_time` — higher-K (8/16/32) 검증 계획

> 선행: `plans/20260714/cpsat_reconstruct_coarse_et_gap.md`
> (§"Full-grid 검증" — K=4 검증까지 완료). 본 문서는 그 **K=4-only caveat**를
> 닫기 위한 higher-K 검증 계획이다. **구현·실행은 별도(새) 대화에서** 수행.

## 배경 / 목적

commit `9b7ad2a`(coarse-exact `insert_idle_time`)는 지금까지 **K=4 (factor:4)**
단일 조건으로만 real-instance 검증됐다. `TODO.md`의 idle_mode 관련 4개 주석
항목(특히 **"Drop the `idle_mode` knob and hardcode lookahead"**)은 *"higher-`K`
(8, 16) grid 미검증"*을 이유로 삭제 대신 보존 상태다.

**목표**: `K ∈ {8, 16, 32}`에서 아래 3가지를 real instance로 확인해 caveat를
종결한다.

1. **Warning 0** — CpsatAdapter `post-process ... > CP-SAT`, sw_cp
   `insert_idle_time left E/T on the table` 둘 다 0건.
2. **Crash 0** — `AssertionError`(특히 sw_cp dispatcher의
   `assert rj_obj <= inc_obj + _FP_TOLERANCE`)·`ERROR`·`Traceback` 0건, 완료
   카운트 == 대상 instance 수.
3. **불변식 유지** — `obj_value <= cp_obj`가 깨지지 않음(=1의 CpsatAdapter
   warning이 그 지문).

### 왜 higher-K가 *더 강한* 테스트인가

coarsening은 due window를 `ceil(d_lo/K)`·`ceil(d_hi/K)`로 축소한다. K가 커질수록
**한 윈도우 안에 K의 배수가 하나도 없는 straddler**(좌측압축 시 early인데 한 셀
이동으로 곧장 tardy로 건너뛰는 경우, coarse 윈도우 폭이 0으로 collapse)가
지배적이 된다. 이 straddler가 바로 magnitude-gate 보정
(`earliness_saved > tardiness_added`, 가중치가 아닌 **magnitude** 비교 +
window-edge unit-step)이 겨냥한 케이스다. 즉 **K=32는 magnitude-gate의 최강
스트레스**이며, 여기서 warning 0이면 보정이 규모에서 견고함을 입증한다.

(참고: 기간은 `ceil`이라 0으로 붕괴하지 않음 — 즉 degenerate-zero-duration은
없고, collapse는 *윈도우 폭*에서만 발생.)

## 범위

- **Instances**: 아래 160개 subset (16 size-group × 각 10 rep). full 1440이 아님.
- **K (`coarsen_solve_reconstruct.factor`)**: 8, 16, 32.
- **Scenario**: `csr_neh_d2wp`(K=4 게이트와 정합) — **primary**.
  - *Optional 확장*: `csr_full_d2wp` — pre-fix warning 최다(195341에서 29건)이자
    inner flow 전체(`mcf_lb → flip → neh → sw_cp → base_cp`)를 태워
    `insert_idle_time` 호출 경로를 가장 넓게 커버. 시간 여유 시 추가 권장.
- **총 실행 수**: 160 × 3(K) × {1 or 2 scenario} = 480 ~ 960 instance-run.
  (higher K = 더 작은 coarse 문제 = K=4보다 빠름. 부담 작음.)

### ins_index (그대로 사용)

```yaml
ins_index: [
  60, 61, 62, 63, 64, 65, 66, 67, 68, 69,
  150, 151, 152, 153, 154, 155, 156, 157, 158, 159,
  240, 241, 242, 243, 244, 245, 246, 247, 248, 249,
  330, 331, 332, 333, 334, 335, 336, 337, 338, 339,
  420, 421, 422, 423, 424, 425, 426, 427, 428, 429,
  510, 511, 512, 513, 514, 515, 516, 517, 518, 519,
  600, 601, 602, 603, 604, 605, 606, 607, 608, 609,
  690, 691, 692, 693, 694, 695, 696, 697, 698, 699,
  780, 781, 782, 783, 784, 785, 786, 787, 788, 789,
  870, 871, 872, 873, 874, 875, 876, 877, 878, 879,
  960, 961, 962, 963, 964, 965, 966, 967, 968, 969,
  1050, 1051, 1052, 1053, 1054, 1055, 1056, 1057, 1058, 1059,
  1140, 1141, 1142, 1143, 1144, 1145, 1146, 1147, 1148, 1149,
  1230, 1231, 1232, 1233, 1234, 1235, 1236, 1237, 1238, 1239,
  1320, 1321, 1322, 1323, 1324, 1325, 1326, 1327, 1328, 1329,
  1410, 1411, 1412, 1413, 1414, 1415, 1416, 1417, 1418, 1419,
]
```

## Config 작성

`metadata/20260714/csr_neh_d2wp_full.yaml`(K=4 검증용)를 베이스로 K만 바꾼 3벌.
한 파일에 factor만 다른 3개 시나리오로 묶어도 되고(권장 — 단일 run root),
K별 개별 파일로 나눠도 됨.

**단일 파일 예 (`metadata/20260714/csr_higher_k_validation.yaml`):**

- `run_mode: FULL_RUN`, `benchmark_dir: benchmarks/PRA2017/large`,
  `ins_index_source`·`bks_table_csv_path` 동일, 위 `ins_index` subset.
- `output_dir: output/20260714_csr_higher_k_validation`.
- `instance_worker_cnt: 12`, `draw_gantt: false`, `draw_progress_plot: false`,
  `painter_thread_cnt: 1` (warning 확인 목적, plotting 불필요).
- scenarios: `csr_neh_d2wp` 블록을 3벌 복제, `output_subdir`/`name`을
  `csr_neh_d2wp_k8` / `_k16` / `_k32`, `coarsen_solve_reconstruct.factor`를
  각각 8/16/32. **나머지 solver 설정·inner solve_flow·inner TL은 K=4와 동일하게
  유지.**

**TL 정책 (중요 — 의도적으로 그대로):** inner TL(`0.00675nc` 등)·CSR
`0.0225nc`를 K와 무관하게 K=4 값 그대로 둔다. higher K = 더 작은 coarse 문제 +
같은 TL ⇒ CP-SAT가 **최적을 더 잘 증명**(`cp_obj=0` witness가 더 자주 발생)
⇒ 불변식 `obj_value <= cp_obj`를 **더 강하게** 테스트한다. (TL을 K로 줄이면
witness가 약해져 테스트가 물러짐 — 하지 말 것.)

**주의**: `idle_mode: "lookahead"`는 config에 남겨도 무방(K>1에서 무효). 굳이
지울 필요 없음.

## 검증 절차 (per K / per scenario)

```bash
RUNDIR=$(ls -dt output/20260714_csr_higher_k_validation/*/ | head -1)

# 1) 두 warning — 반드시 --glob '*.log' (복사된 config YAML의 주석 오탐 방지)
rg -rc --glob '*.log' 'left E/T on the table' "$RUNDIR"
rg -rn --glob '*.log' 'post-process objective [0-9.]+ > CP-SAT' "$RUNDIR" | wc -l

# 2) 임의 WARNING / crash
rg -rn --glob '*.log' '\[WARNING\]' "$RUNDIR" | wc -l
rg -rn --glob '*.log' 'ERROR|AssertionError|Traceback' "$RUNDIR" | wc -l

# 3) 완료 카운트 (scenario별 160)
find "$RUNDIR" -name '*_instance_result.yaml' | wc -l
```

- **오탐 주의**(K=4 때 실제 발생): run root에 복사되는 config YAML 주석이
  `post-process ... > CP-SAT` 정규식에 걸린다. 반드시 `--glob '*.log'`로 제한.
- (선택) `*_instance_result.yaml`의 `obj_value` 음수 아님·feasible 여부,
  `work_status` 분포 sanity 확인.

## Positive control (방법론 유효성)

higher-K는 **pre-fix baseline이 없다** (기존 실험은 factor=4만 사용). 따라서
K=4처럼 "27→0" delta를 직접 제시할 수 없고, 주장은 **절대적 0**이 된다. 방법론이
"경고가 있으면 잡는다"임은 K=4에서 이미 입증(195341 pre-fix run에서 csr_neh_d2wp
27건 검출). higher-K에서 이를 보강하려면:

- **(A, 권장·강함)** 동일 subset·동일 K를 **9b7ad2a의 부모 커밋(수정 전)** 으로
  실행해 baseline을 만들고 post-fix(0)와 비교.
  - **격리 필수**: 별도 `git worktree`에서 checkout — 공유 워크트리에서 subagent
    등이 `git checkout` 하면 uncommitted 작업이 날아간 전례가 있음
    (memory: subagents-no-git-shared-worktree). 반드시 분리 워크트리.
  - 부모 커밋 run은 **sw_cp의 `assert rj_obj <= inc_obj`가 higher-K에서 실제로
    터질 수 있음**(수정 전 언더슈트로 rj가 incumbent보다 좋아질 수 있어서).
    그 crash 자체가 "수정이 필요했다"의 증거 → baseline은 crash/warning 수집이
    목적이므로 실패를 허용(개별 instance 실패해도 계속).
- **(B, 가벼움)** K=4의 pre/post 비교로 방법론이 이미 검증됐다는 점 + higher-K는
  절대 0 + no-crash로 갈음. baseline run 생략.

→ 기본 **(A)** 권장(higher-K에서 pre-fix가 실제로 warning/assert를 내는지가 이
검증의 핵심 설득력). 시간 제약 시 (B).

## 리스크 / 관찰 포인트

- **윈도우 collapse (K=32)**: coarse 폭 0 윈도우 다수 → straddler 지배. 이것이
  테스트의 의도(§"왜 higher-K가 더 강한가"). degenerate 처리 오류가 있으면
  warning/assert로 드러남.
- **LB suppression**: `time_factor > 1`이면 MCF LB가 억제되어 `obj_bound=None`
  (`lb_suppressed_by_time_factor=True`). **warning 아님, 정상** — optimality
  판정에서 obj_bound=None을 crash로 오인하지 말 것.
- **sw_cp hard assert**: post-fix에서 이 assert가 터지면 그게 곧 회귀
  (magnitude-gate가 rj를 incumbent 이하로 만들지 못한 것) → 최우선 조사 대상.
- **성능**: higher K로 문제가 작아져 빠를 것으로 예상. 그래도 background 실행 +
  완료 알림 권장.

## 통과 기준 (Definition of Done)

- `K ∈ {8, 16, 32}` 전부에서: warning 0, `[WARNING]`/ERROR/Assert/Traceback 0,
  각 scenario 160/160 완료.
- (A 채택 시) 부모-커밋 baseline이 higher-K에서 warning/assert를 보이고
  post-fix가 0 → delta 입증.

## 통과 후 후속 작업

1. 본 문서에 **`## 결과 (실행 후)`** 절 append (K별 warning 카운트 표 + run
   경로 + baseline 유무).
2. `TODO.md` 정리 항목 갱신:
   - **"Drop the `idle_mode` knob and hardcode lookahead"** — higher-K 검증 완료로
     when-to-act 조건 충족. 이제 **삭제 후보**(knob은 모든 K에서 dead) 또는 실제
     knob 제거 착수(코드 변경이므로 별도 결정). Status 주석의 "higher-`K` 미검증"
     문구 해제.
   - 나머지 3개 주석 항목의 "K=4 only / higher-K 미검증" 언급도 갱신.
3. 검증 config(`metadata/20260714/csr_higher_k_validation.yaml`)·output 경로 기록.

## 참고

- 선행 검증: `plans/20260714/cpsat_reconstruct_coarse_et_gap.md` (진단 + GREEN +
  K=4 full-grid 검증).
- K=4 검증 config: `metadata/20260714/csr_neh_d2wp_full.yaml` (본 계획의 베이스).
- 수정 코드: `src/ffc_ddw_sum_et/solution/ffc_schedule.py` `insert_idle_time`
  K>1 branch (commit `9b7ad2a`).
- coarsening: `src/ffc_ddw_sum_et/algorithm/coarsen_solve_reconstruct.py`
  (`factor` = 축소 divisor, 기간·윈도우 `ceil(value/factor)`).
- brute-force property test(알고리즘 정확성, K∈{1..50}):
  `tests/solution/test_ffc_schedule.py::test_insert_idle_time_coarse_exact_random_property`.

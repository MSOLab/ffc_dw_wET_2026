# `incremental_job_contrib_cp` 파일럿 후속 (디버깅 계획)

**작성일**: 2026-07-28 · **종류**: 코드 변경 계획(사전 작성) · **상태**: **구현 완료** (rev.1 — §전용 플롯 사양 수정)
**선행 문서**: `plans/experiment/20260728/incremental_job_contrib_cp.md` (rev.3, 구현 완료)
**근거**: `output/20260728_incremental_job_contrib_cp_pilot/` 4개 런 (§0)

> **이 문서의 범위** — 파일럿에서 드러난 **2건의 수정**과 그에 딸린 **전용 progress
> report 신설**뿐이다. 알고리즘 파라미터(`jd_start` / `jd_end` /
> `destroyed_op_tl_multiplier`) 조정은 **범위 밖** — 차차 조정한다.
>
> **이 작업은 직전 커밋에 포함하지 않는다.** 선행 문서의 구현분이 먼저 커밋되고,
> 이 문서의 작업은 그 위에 별도로 올린다.

---

## 0. 파일럿 근거 (다음 대화에서 재도출하지 말 것)

4개 런 모두 인스턴스 5개(`Instance_50_5_3_0,6_0,2_10_Rep0~4`, n=50 · c=5 · mps=3),
`jd_end="0.1n"`(=5), `solver_thread_cnt: 8`, `error_if_infeasible: true`,
`log_search_progress: true`, `draw_gantt: true`, 총 예산 `0.09nc`=22.5s
(RESUME 후 스텝 잔여 **~13s**). config: `metadata/20260728/incremental_job_contrib_cp_pilot.yaml`
(각 런 디렉터리에 실행 시점 사본이 있다).

| 런 | `jd_start` | `k` | 총 개선(5개 합) | 평균 RPDf | 평균 소요 |
|---|---|---|---|---|---|
| `20260728T150655_151041` | 1 | **0.005** | — | — | **5/5 실패** |
| `20260728T150915_215146` | 1 | 0.05 | 3080 | 24.00 % | 5.64 s |
| `20260728T151052_492336` | **2** | 0.05 | **3980** | **23.47 %** | 5.38 s |
| `20260728T151106_187363` | 3 | 0.05 | 3204 | 23.93 % | 4.59 s |

(초기해 평균 RPDf 25.74 %. jd_start=2가 3 대비 **5승 0무 0패**, 1 대비 3승 2패.
다만 인스턴스 5개 · 8-worker CP라 0.5 pp 차이는 **아직 확정적이지 않다**.)

**정상 동작이 확인된 것** (수정 대상 아님):

- TL 정책이 설계대로 스케일한다 — `tl = k · jd · c` → 0.25/0.5/0.75/1.0/1.25 s,
  실제 `elapsed`는 항상 `tl + 0.02~0.03 s`.
- 15개 인스턴스-런 **전부 `exit_reason: completed`**, 소요 3.9~6.8 s 대 잔여 예산
  ~13 s → **예산의 40~70 %를 안 쓴다.** `positive_contrib_job_count`는 45/50이라
  포화도 한참 멀었다. → `jd_end` 상향 여지 (**파라미터 조정이므로 범위 밖**).
- `same_set_skips` 0~4회/런 (rows 4~8 대비). §3-(a) 사전 판정이 실제로 CP solve를
  아꼈다는 증거.

---

## 1. 작업 A — CP-SAT `UNKNOWN`을 정상 경로로

### 현상

`k=0.005` 런은 **5/5 인스턴스가 첫 회차에서 예외로 죽었다.**

```
JobContribCpDispatcher: instance=..., horizon=1608, selected=['j39'],
  eff_tl=0.015s (bounds={'cp_tl': 0.015, 'wall_clock_deadline': 12.967}), num_workers=8
JobContribCpDispatcher: no feasible solution (status=UNKNOWN) despite complete hint
RuntimeError: ... CP-SAT returned status=UNKNOWN despite complete hint; this is a bug signal.
```

- TL은 `0.005 × 1 × 5 = 0.025 s`인데 **solve에 실제로 주어진 건 0.015 s**다 —
  디스패처 setup(기여도 계산·선택)이 `cp_tl` 예산에서 먼저 차감되기 때문
  (`dispatcher.py`의 `tl_bounds["cp_tl"] = cp_tl - (now - start)`).
- 8 worker로 250-op 모델을 0.015 s에 풀 수 없다 → `UNKNOWN`.

### 판단

**구현 버그가 아니라 설정이 선행 문서 §5 ⚠의 경계 아래로 내려간 것**이다. 그러나
그 경계 아래에서 **런 전체가 죽는 것**은 잘못된 설계다 — 시간을 부족하게 주는 상황은
언제든 생길 수 있고, 그때의 올바른 동작은 "개선 없음"이지 "실패"가 아니다.

### 결정 (확정)

CP-SAT status별로 분기한다.

| status | 처리 | 로그 |
|---|---|---|
| `OPTIMAL` / `FEASIBLE` | 정상 경로 | info |
| **`UNKNOWN`** | **incumbent를 그대로 반환**, `WorkStatus.FEASIBLE`, **예외 없음** — `error_if_infeasible`와 **무관** | **warning** |
| `INFEASIBLE` | complete feasible hint가 있는데 infeasible이면 진짜 버그 신호 → `error_if_infeasible` 동작 유지 | error |
| `MODEL_INVALID` | 모델 구성 오류 → **항상 raise** | error |

- 반환 레코드는 **기존 `_incumbent_fallback_record`를 그대로 재사용**한다
  (`obj_value=incumbent_obj`, `metrics["fallback"]="incumbent"`,
  `cpsat_status=status_name`). 새 코드가 거의 필요 없다.
- **`error_if_infeasible`의 이름과 의미가 이제 정확히 일치한다.** 지금까지는
  `UNKNOWN`까지 잡고 있었다(이름이 거짓말을 하고 있었다).
- 단발 `job_contrib_cp`도 같은 동작이 된다 — **의도된 것**이다.

### 파급

`k`를 작게 준 설정에서 회차는 "개선 없음"으로 흘러 다음 `jd` 레벨로 넘어간다.
루프는 정상 종료하고 incumbent는 보존된다. **낭비 회차는 남지만 실패는 아니다.**
낭비 자체를 줄이는 것은 파라미터 조정(범위 밖)이나 D3(`min_cp_tl_seconds` floor,
선행 문서 §9에서 보류)의 몫이다.

### 부수 작업 A-2 — 요약 로그를 예외 경로에서도 남긴다

`k=0.005` 런은 `_incremental_job_contrib_cp_log.yaml`이 **0개**다. 예외가 composite를
뚫고 나가 요약 dump에 도달하지 못했기 때문이고, 그래서 사후 진단 자료가 통째로 없다.

→ composite의 루프를 `try / finally`로 감싸 **어떤 경로로 빠져나가도 요약을 쓴다.**
`exit_reason`에 `error:<ExceptionType>`을 남긴다. (A 작업 후에도 `INFEASIBLE` /
`MODEL_INVALID`는 여전히 예외로 나가므로 이 가드는 유효하다.)

---

## 2. 작업 B — 제약 모델 LB를 전역 플롯에서 빼고 전용 리포트로

### 현상

전역 `<instance>_progress_plot.png`의 LB(주황 파선)가 스텝 진입 시점에서
**전역 LB(MCF) 15149에서 29238~40217로 점프하고 회차마다 진동한다.**

`Instance_50_5_3_0,6_0,2_10_Rep3` 실측:

```
스텝 이전 LB : 15149  (평평)
스텝 도중 LB : 60점, min 29238 / max 40217
              그중 53점이 BKS(34617)를 초과   ← 유효한 전역 하한이면 불가능
```

Rep0 그림도 같은 형태다(스텝 구간에서 LB가 ~47000까지 치솟고 톱니로 진동).

### 원인

profile-fix arc를 더한 모델은 원문제의 **restriction**이므로 그 bound는 원문제의
하한이 아니다. 선행 계획서가 이 스텝의 `AlgResult.obj_bound`를 `None`으로 못박은
이유가 정확히 이것인데, rev.3에서 `ObjectiveValueRecorder`를 붙이면서
**`progress_log`의 `obj_bound`로 같은 값이 새어 나갔다.** 계약과 어긋난 것은
`progress_log` 쪽이다.

등록값은 무사하다 — `instance_result.yaml`의 `obj_bound`는 15149를 유지한다.
**오염된 것은 `_obj_log.json`과 그로부터 그려지는 전역 플롯뿐이다.**
(UB는 `_render_progress_plot`의 `_running_min`이 정리해 주지만 LB는 원본 그대로
그려진다.)

### 결정 (확정)

| # | 결정 |
|---|---|
| **B-1** | 디스패처 `progress_log` 엔트리의 `obj_bound`를 **`None`**으로 — 전역 obj_log 정화 |
| **B-2** | CP 프레임 궤적은 `metrics["cp_progress"]`로 따로 싣는다 — `[{"t": <solve 시작 기준 초>, "obj_value": …, "obj_bound": …}]` |
| **B-3** | composite가 회차별 `cp_progress`를 모아 **`_incremental_job_contrib_cp_progress.json`** 한 파일로 기록 (**step-local 시간축**) |
| **B-4** | 전용 렌더러 + artifact kind 신설 — 이 그림에서**만** 제약 모델 LB를 참조용으로 표시 |

**왜 `metrics` 경유인가**: `progress_log`는 러너가 전역 `_obj_log.json`으로 접는
**유일한 통로**다(`ffcddw_single_instance_runner.py:653` `_save_obj_log`). 거기에
값을 남겨두고 필터로 거르는 방식은 필터가 하나 빠지는 순간 다시 전역 그림을
오염시킨다. `metrics`는 이미 스텝 전용 진단 통로(`_metrics.yaml`)이므로 **경로
분리가 구조적으로 보장된다.**

**왜 step-local 시간축인가**: 러너의 controller-frame 재기준화는
`start_time = timer.elapsed_sec - report.elapsed_time`으로 **스텝 단위**로만 동작한다.
회차 내부 시각을 컨트롤러 프레임으로 옮기려면 러너를 건드려야 하는데, 전용 그림의
질문은 "이 스텝 **안에서** 무슨 일이 있었나"이므로 composite 진입을 0으로 잡는 것이
맞다. **러너는 무변경.**

### 전용 플롯 사양

| 계열 | 내용 | 범례 | 스타일 |
|---|---|---|---|
| UB | `cp_progress.obj_value` | `objValue` | 실선 step |
| CP LB | `cp_progress.obj_bound` | `restricted-model bound` | 파선 |
| 전역 LB | MCF LB (스텝 진입 시점의 `obj_bound`) | `global LB` | 수평 기준선 |

> **rev.1 (착수 후 수정)** — 원안은 UB를 "등록 UB(회차별 후처리 값)"와
> "CP UB(`cp_progress.obj_value`)" 두 계열로 나눴으나, **실측 결과 두 계열이
> 완전히 동일해 하나로 합쳤다.** 파일럿 Rep1의 97개 점 중 best-so-far 필터가
> 걸러낸 점은 0개다. 원인은 세 불변식이 사슬로 물려 수열이 이미 단조
> 비증가이기 때문이다 — (1) 회차 안에서 CP-SAT 콜백은 개선해에만 발화, (2) 다음
> 회차는 현재 incumbent를 complete hint로 받으므로 첫 기록점이 직전 회차의 후처리
> 값과 정확히 일치(= CP 모델 목적함수에 offset이 없다는 증거), (3) 후처리는 개선
> 아니면 동률. 따라서 **UB가 상승하면 그 자체가 불변식 위반 신호**이고, 이를 보려면
> best-so-far 필터를 걸지 않은 원본 궤적을 그대로 그리는 쪽이 맞다.
>
> 범례의 괄호 주석(`(NOT a global LB)`, `(MCF: …)`)도 함께 제거했다. 제약 모델
> bound가 전역 하한이 아니라는 경고는 이 문서 §2와 계열명 `restricted-model
> bound` 자체가 담당한다.

- 회차 경계 세로선 + `jd{n}/r{n}` 라벨. **`jd`가 바뀌는 경계만 굵게** — 선행 문서
  D5(주석 과밀)의 후속 조정을 여기서 먼저 적용한다.
- `same_set` 스킵은 CP solve가 없으므로 점이 없다. 필요하면 `same_set_skips`를
  제목에 표기.

### 배선

```yaml
# metadata/artifact_layout/ffc_ddw_sum_et_v1.yaml — progress_plot_png(:140) 옆
- scope: instance
  zone: progress
  kind: job_contrib_progress_json
  file_template: "{instance_name}_incremental_job_contrib_cp_progress.json"
- scope: instance
  zone: report
  kind: job_contrib_progress_plot_png
  file_template: "{instance_name}_incremental_job_contrib_cp_progress.png"
```

렌더러는 `reporting.py`의 `_render_progress_plot`(:341)과 **같은 규약**을 따른다 —
모듈 레벨 함수(`ProcessPoolExecutor` picklable), `matplotlib.use("Agg")`,
import 실패 시 warning 후 skip. 팬아웃은 `_generate_progress_plots`(:2107)를
그대로 본떠 `_generate_job_contrib_progress_plots`를 만든다.

**선례**: `plans/experiment/20260708/progress_plot_from_obj_log.md`가 전역 progress
plot을 추가할 때의 전 과정(artifact kind 등록 → 렌더러 → 팬아웃 배선)을 기록해 두었다.
**착수 전에 읽을 것.**

---

## 3. 구현 단계 (TDD)

| 단계 | 내용 | Red로 먼저 세울 테스트 |
|---|---|---|
| **P1** | `UNKNOWN` → fallback | `error_if_infeasible=True`**에서도** 예외 없음 / 반환 schedule이 incumbent와 동일 / `work_status == FEASIBLE` / `metrics["fallback"] == "incumbent"` / `cpsat_status == "UNKNOWN"` |
| **P2** | `INFEASIBLE` 분기 | `error_if_infeasible=True` → raise, `False` → fallback (기존 동작 회귀) |
| **P3** | `MODEL_INVALID` | 항상 raise (재현이 어려우면 status를 주입하는 단위 테스트로) |
| **P4** | composite 관통 | `UNKNOWN`이 계속 나오는 stub → 루프가 예외 없이 끝나고 incumbent 보존, `exit_reason` 정상 |
| **P5** | A-2 `try/finally` | 내부에서 예외 발생 시에도 요약 로그 존재 + `exit_reason == "error:RuntimeError"` |
| **P6** | B-1 | 디스패처 `progress_log`의 모든 엔트리 `obj_bound is None` |
| **P7** | B-2 | `metrics["cp_progress"]`의 형식 / `t` 단조 증가 / `obj_value`·`obj_bound` 존재 |
| **P8** | B-3 | composite가 progress json 기록 / 회차 수 일치 / step-local `t` 단조 / working dir 없으면 skip (rev.3 리뷰 1번과 같은 함정) |
| **P9** | B-4 렌더러 | json → png 생성, 예외 없음 / matplotlib 부재 시 graceful skip |
| **P10** | **회귀 (파일럿 재발 방지)** | 전역 `_obj_log.json`의 `obj_bound.data`에 **스텝 구간 점이 하나도 없다** — 이 테스트가 §2 현상의 재발을 막는 핵심 |

각 단계 후 `uv run ruff check`, 필요 시 `uv run ruff format`.
검증은 파일럿을 **작은 슬라이스로 재실행**해 전역 플롯의 LB가 평평한지,
전용 플롯이 생성되는지 눈으로 확인한다.

---

## 4. 열린 결정 (착수 시 판단)

**Q1. 전용 플롯의 게이트** — 기존 `draw_progress_plot`를 공유할지, 신규
`draw_job_contrib_progress_plot`를 둘지. 공유가 단순하지만 스텝을 안 쓰는 시나리오도
빈 작업을 돌게 된다(파일이 없으면 skip하므로 실해는 없다). **공유 권장.**

**Q2. `cp_progress`를 `metrics`에 싣는 부담** — 회차당 수십~수백 점이 붙으면
`_metrics.yaml`이 회차마다 커진다(파일럿 기준 회차당 최대 60점). 회차별 파일이
이미 회차 수만큼 있으므로 총량이 무시 못 할 수 있다. `metrics`에 싣되 컨트롤러가
`_metrics.yaml`에 dump할 때 **`cp_progress`만 빼는** 선택지도 있다(진단 통로는
composite의 progress json 하나로 통일).

**Q3. `MODEL_INVALID` 테스트 실현 가능성** — 실제로 만들기 어려우면 status 주입
단위 테스트로 대체하고 그 사실을 명시.

**Q4. `sw_cp`의 동일 문제** — `sw_cp/dispatcher.py:343`도 restricted 모델의 bound를
`progress_log`에 싣는다(offset을 더해서). 같은 성질의 오염이지만 **별건**이다.
이 작업에서 건드리지 않되, 확인되면 별도 이슈로 기록.

---

## 5. 명시적 범위 밖

- **알고리즘 파라미터 조정** (`jd_start` / `jd_end` / `destroyed_op_tl_multiplier`) —
  차차 조정한다. §0의 "예산 40~70 % 미사용", "jd_start=2 우세"는 **관측 기록일 뿐
  이 계획의 작업 항목이 아니다.**
- D3 `min_cp_tl_seconds` floor (선행 문서 §9에서 보류 — 작업 A가 실패를 없애므로
  긴급도가 낮아졌다).
- 선행 문서 §11의 다양화 정책(순위 회전 / 가중 랜덤 / adjacency).
- 파일럿 재실행 및 본 실험(선행 문서 §10).

---

## 6. 관련 문서

- 선행 계획(구현 완료): `plans/experiment/20260728/incremental_job_contrib_cp.md`
- 단발 스텝 계획: `plans/experiment/20260728/critical_ns_port_from_hfs.md`
- **전역 progress plot 추가 선례**: `plans/experiment/20260708/progress_plot_from_obj_log.md`
- subroutine step 계약: `src/ffc_ddw_sum_et/orchestration/CLAUDE.md`
- 알고리즘 계약: `docs/algorithm-principles.md`
- artifact 스키마: `docs/io/20260429_artifact_manager.md`,
  `metadata/artifact_layout/ffc_ddw_sum_et_v1.yaml`
- 파일럿 config: `metadata/20260728/incremental_job_contrib_cp_pilot.yaml`
- 파일럿 결과: `output/20260728_incremental_job_contrib_cp_pilot/` (§0의 4개 런)

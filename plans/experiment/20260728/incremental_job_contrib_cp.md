# 반복형 기여도 D&C 이웃 (`incremental_job_contrib_cp`) 설계 계획

**작성일**: 2026-07-28 · **종류**: 코드 변경 계획(사전 작성) · **상태**: **구현 완료**
(2026-07-28, 미커밋). 사양은 rev.3 확정분 그대로이고 **설계 대비 이탈 없음**;
구현 과정에서 갈린 지점만 §12 「구현 결과」에 기록했다. 실험(§10)은 미실행.
**선행 문서**: `plans/experiment/20260728/critical_ns_port_from_hfs.md`
(단발 `job_contrib_cp` 구현 계획 — 구현 완료). 이 문서는 그 §2 「반복 구조에서의
함정」에서 **파일럿 이후로 연기(Q4)** 했던 항목을 여는 문서다.
**참조 구현**: `/home/hjt/code/hybridflowshop` — Adaptive Critical Block
Order-Preserving CP-LNS (**구성 요소만 참고, 코드 이식 아님**)
**대상**: `orchestration/controller.py`(신규 composite step),
`algorithm/job_contrib_cp/`(선택 로직 분리 · TL 정책 · progress log)

> **rev.2 반영 사항** (사용자 확정): ① 루프 구조를 「시작 job 수 → 종료 job 수」
> 이중 루프로 확정 ② 시간 제약을 **파괴 op 수 × 상수**로 확정(test config `0.005`)
> ③ 시간에 따른 objective 변화 plotting 요구 추가.
> rev.1에서 제안했던 rotate / weighted-random / adjacency 다양화 정책은 **전부
> 보류**로 내렸다 (§11) — 확정된 종료 규칙이 고정점 문제를 이미 닫는다.
>
> **rev.3 반영 사항** (사용자 확정): ④ **`jd >= n`이면 최적화하지 않고 종료**
> (§3-(b)) ⑤ `jd_start=1`, `jd_end="0.1n"` ⑥ `jd_step_size: int = 1`
> (optional keyword) ⑦ rev.2의 Q3–Q6은 제안대로 확정 (§9).

---

## 0. 한 문장 정의

> `job_contrib_cp` 단발 호출(기여도 상위 job 파괴 → profile-fix된 나머지 위로
> CP-SAT 재삽입)을 **파괴 job 수 `jd`를 `jd_start`에서 `jd_end`까지
> `jd_step_size`씩 늘려가며** 반복한다. 각 `jd`에서는 개선이 이어지는 한 갱신된
> incumbent로 계속 재시도하고, **개선이 없거나 파괴 대상 집합이 직전과 같으면**
> 그 `jd`를 끝내고 다음 레벨로 넘어간다. **`jd`가 전체 job 수 `n` 이상이 되면
> 이웃 탐색의 의미가 없으므로 최적화하지 않고 전체를 종료한다.**

`sw_cp` : `incremental_sw_cp` = `job_contrib_cp` : `incremental_job_contrib_cp`.
셋 모두 **내부 스텝이 각자 `_register`하고 composite 자신은 등록하지 않는다.**

---

## 1. 참조 구현과의 대조표

| hybridflowshop | 위치 | 이 저장소 | 상태 |
|---|---|---|---|
| 반복 + stall 종료 루프 | `hfs_cp_lns.py:13325` | **`incremental_job_contrib_cp`** | **신규 (§3)** |
| 단일 CP-LNS 실행 | `hfs_cp_lns.py:13067` | `controller.job_contrib_cp` | **이미 있음** |
| 이웃 제약 구성(선택 job 자유 / 나머지 순서만 arc 보존) | `hfs_cp_lns.py:12984` 계열 | `dispatcher.py` §(2) `remove_jobs` + profile fix | **이미 있음, 동치** |
| critical block 정의(slack=0 연속 op) | `schedule_lite.py:2031` | `FFcSchedule.find_critical_blocks` (`ffc_schedule.py:1396`) | **이식돼 있으나 채택 안 함 → §2** |
| critical adjacency / 선택 정책 | `hfs_cp_lns.py:13695`, `:13723` | — | **보류 (§10)** |
| 회당 `0.05nc` | 실행 설정 YAML `:339` | **파괴 op 수 × 상수** | **§5 — 정책 자체가 다름** |
| `stop_after_no_makespan_improvement=4` | 같은 파일 | **`1`에 해당(개선 없으면 즉시 다음 `jd`)** | **§3 — 더 단순** |

**이웃 제약 구성은 참조 구현과 이미 동치**다 — 선택된 job은 전 스테이지에서 자유,
나머지는 incumbent 순서만 precedence arc로 보존하고 시작시각은 고정하지 않으며,
`remove_jobs` 경유라 `A→X→B`에서 `X`를 빼면 `A→B` arc가 자동으로 이어붙는다
(선행 문서 §1-(2)). **이 계획에서 새로 만들 것은 반복 루프·TL 정책·progress log뿐이다.**

---

## 2. makespan criticality를 가져오지 않는 이유

`FFcSchedule.calculate_slack` / `find_critical_blocks`는 이미 이식돼 있으므로
**쓸 수는 있다.** 그럼에도 채택하지 않는다:

1. **목적이 makespan이 아니다.** slack=0은 "makespan을 늘리지 않고는 못 미룬다"는
   뜻이지 "이 op이 E/T 페널티를 물고 있다"는 뜻이 아니다. 이 문제는
   $f_j(C_j)$가 job별로 **완전히 분해**되므로 criticality 대리변수가 필요 없다
   (선행 문서 §0의 결론을 유지한다).
2. **incumbent가 의도적으로 idle을 넣은 상태다.** `insert_idle_time`이 마지막
   스테이지를 due window 쪽으로 우측 이동시킨 뒤이므로 CPM slack=0인 op은
   희박하고 makespan 경로에 편중된다. hybridflowshop이 `make_semi_active` 직후
   critical block을 뽑는 것과 전제가 다르다.

인접성(adjacency) 착상 자체는 유효하지만 — job $j$를 막고 있는 job도 같이 풀어야
한다 — **확정된 루프가 그것 없이 고정점을 닫으므로 §11 보류**로 내린다.

---

## 3. 루프 명세 (확정)

### 파라미터

| 이름 | 형태 | 기본값 | 의미 |
|---|---|---|---|
| `jd_start` | `int \| str` (`1`, `"2"`, `"0.02n"`) | `1` | 시작 파괴 job 수 |
| `jd_end` | `int \| str` (`"0.1n"` 등) | `"0.1n"` | 종료 파괴 job 수 (**포함**) |
| `jd_step_size` | `int` (≥1), optional keyword | `1` | 레벨 증가폭 |

`jd_start` / `jd_end`는 `resolve_jd_count_target`(`value_resolver.py:56`)로
해석한다 — 절대 정수 / `"<ratio>n"` 비율의 **단일 표기**이고 `[1, n]`으로 포화,
`0` 계열은 `ValueError`. `jd_end < jd_start` 또는 `jd_step_size < 1`이면 `ValueError`.

**레벨 목록은 `range(jd_start, jd_end + 1, jd_step_size)`** — Python `range` 의미
그대로다. `jd_step_size > 1`이면 `jd_end`가 방문되지 않을 수 있고(예:
`1, 10, step=4` → `1, 5, 9`), **그 경우에도 `jd_end`를 억지로 덧붙이지 않는다**
(예외 케이스를 만들지 않는다 — KISS). 해석된 레벨 목록은 `log.info`로 남긴다.

### 절차

```py
n = instance.job_count
jd_start_cnt = resolve_jd_count_target(jd_start, n)
jd_end_cnt   = resolve_jd_count_target(jd_end,   n)

for jd in range(jd_start_cnt, jd_end_cnt + 1, jd_step_size):   # 바깥 루프: 파괴 규모 ramp
    if jd >= n:                                 → 전체 종료          # §3-(b) ★
    prev_selected = None
    while True:                                        # 안쪽 루프: 같은 jd로 재시도
        if self.is_stopping_condition():        → 전체 종료
        if remaining_sec() < min_remaining_sec: → 전체 종료          # §5

        incumbent = solution_manager.get_incumbent().schedule
        selected  = select_jd_jobs(incumbent, instance, jd, time_factor)   # §4

        if not selected:                        → 전체 종료          # obj == 0
        if len(selected) < jd:                  → 이 회차 후 전체 종료 # §3-(c) 포화
        if selected == prev_selected:           → break (이 jd 종료)  # 집합 반복
        prev_selected = selected

        obj_before = solution_manager.best_obj_value
        with self.temporarily_extended_context(f"jd{jd:03d}_r{rep:03d}"):
            self.job_contrib_cp(jd_target=jd, **base_kwargs)
        obj_after = solution_manager.best_obj_value

        if not (obj_before - obj_after > 0):     → break (이 jd 종료)  # 개선 없음
```

### 종료 조건 6종

| 조건 | 범위 | 근거 |
|---|---|---|
| 개선 없음 | **이 `jd`만** 종료 → 다음 레벨 | 사용자 확정 |
| 파괴 집합이 직전과 동일 | **이 `jd`만** 종료 → 다음 레벨 | 사용자 확정 |
| **`jd >= n`** | **전체** 종료 (**최적화 호출 없이**) | 사용자 확정 → §3-(b) |
| `selected`가 빔 (기여도 양수 job 0개) | **전체** 종료 | incumbent obj == 0, 손댈 것 없음 |
| `len(selected) < jd` (양수 기여도 job 수로 포화) | **전체** 종료 | §3-(c) |
| `is_stopping_condition()` / 잔여 예산 부족 | **전체** 종료 | 컨트롤러 계약 · §5 |

### 설계 노트

**(a) 집합 비교는 CP 호출 *전에* 한다.** 개선이 있었더라도 top-`jd` 집합이 그대로일
수 있는데(파괴한 job들의 페널티가 줄었지만 여전히 상위권), 이를 **호출 후**에
판정하면 그 회차의 CP solve가 통째로 낭비된다. 호출 전에 판정하면 **낭비 없이**
다음 레벨로 넘어간다. 이를 위해 선택 로직을 순수 함수로 분리한다 → §4.

> 엄밀히는 집합이 같아도 incumbent가 달라졌으면 모델이 완전히 동일하지는 않다
> (profile-fix arc와 hint가 바뀐다). 그럼에도 종료하는 것이 확정 사양이다 —
> 같은 job을 같은 골격에 재삽입하는 것이므로 개선 여지가 급격히 줄고, 그 예산을
> 더 넓은 이웃에 쓰는 편이 낫다는 판단.

**(b) `jd >= n`이면 최적화하지 않고 전체 종료.** ★ 확정 사양.
`jd == n`은 **전 job을 파괴**한다는 뜻이고, 그러면 profile-fix로 보존할 순서가
하나도 남지 않는다 — `pf_schedule.remove_jobs(전체)` 후 arc가 0개가 되어
**full CP re-solve와 정확히 동치**가 된다. 즉 이웃 탐색(neighborhood search)이
아니게 된다. `jd > n`은 그보다 더 무의미하다.

- 이 판정은 **바깥 루프 진입 시점**에 하고, `job_contrib_cp`를 **호출하지 않는다**
  (CP 모델 구축·solve 없음). `log.info` + `exit_reason="jd_ge_n"`으로 남긴다.
- `jd_start >= n`이면 **스텝 전체가 한 번도 최적화하지 않고 반환**한다. 이는
  에러가 아니라 정상 경로다 — 작은 인스턴스에서 비율 표기가 `n`에 닿을 수 있다
  (`resolve_jd_count_target`이 `[1, n]`으로 포화시키므로 `jd_end="0.1n"`이라도
  `n`이 아주 작으면 `jd_start`와 `n`이 만난다). `warning`이 아니라 `log.info`.
- **`resolve_jd_count_target`의 상한 포화(`min(·, n)`)에 기대지 않는다.** 그
  함수는 `n`을 허용값으로 보므로(선행 문서 §1-(1): "전부 파괴는 의도가 분명하다"),
  `n` 자체를 걸러내는 것은 이 composite의 책임이다. **단발 `job_contrib_cp`의
  동작은 바꾸지 않는다** — 그쪽에서 `jd_target=n`은 여전히 유효한 full re-solve다.

**(c) 양수 기여도 job 수로 포화되면 전체 종료.** `jd_count_eff`는
$\#\{j : f_j(C_j) > 0\}$로 포화되므로(선행 문서 Q5), 그 지점을 넘어선 `jd`는
**항상 같은 집합**을 만든다 — 안쪽 루프가 매번 즉시 break하며 남은 레벨을 헛돈다.
`len(selected) < jd`가 처음 관측되면 그 회차까지만 수행하고 바깥 루프를 끊는다.

> (b)와 (c)는 별개의 가드다. $\#\{f_j > 0\} \le n$ 이므로 보통은 (c)가 먼저
> 걸리지만, **전 job이 페널티를 물고 있으면** (c)는 `jd = n+1`에서야 걸리고
> 그 전에 `jd = n`이 실행돼 버린다 — 그 구멍을 (b)가 막는다.

**(d) `jd_start`/`jd_end`는 회차 수가 아니라 파괴 규모의 범위다.** 총 회차 수는
사전에 정해지지 않는다 (각 `jd`에서 개선이 이어지는 만큼 돈다). 예산 상한은
`is_stopping_condition()`과 `min_remaining_sec`이 담당한다 — `max_repeat` 같은
회차 상한은 **두지 않는다** (YAGNI; 예산 가드가 이미 종료를 보장).

---

## 4. 파괴 대상 선택의 SSOT 분리

§3-(a)의 사전 판정 때문에 **컨트롤러도 선택 결과를 알아야 한다.** 컨트롤러에
선택 로직을 복사하면 DRY 위반이므로, 현재 `dispatcher.py:58-67`에 인라인된 선택을
순수 함수로 뽑는다:

```python
# src/ffc_ddw_sum_et/algorithm/job_contrib_cp/selection.py  (신규)

def select_jd_jobs(
    incumbent: FFcSchedule,
    instance: FFcDDWParameters,
    jd_count_target: int,
    *,
    time_factor: int = 1,
) -> list[str]:
    """기여도 상위 최대 ``jd_count_target``개 job (동점은 job_id 오름차순).

    기여도가 0인 job은 후보에서 제외하므로 반환 길이는
    ``min(jd_count_target, #{j : f_j(C_j) > 0})`` — 빈 리스트면 incumbent obj == 0.
    """
```

- 디스패처는 이 함수를 호출하도록 바꾼다 (동작 무변경, 기존 33개 테스트가 회귀 가드).
- 컨트롤러 composite도 같은 함수를 호출한다 → **선택 규칙의 단일 출처.**
- $O(n \log n)$이라 회차마다 두 번 계산해도(컨트롤러 사전 판정 + 디스패처) 무시할
  수 있다. 결과를 넘겨 재사용하는 최적화는 하지 않는다 — 인자를 늘려 계약을
  복잡하게 만드는 값이 없다 (KISS).

---

## 5. 시간 제약 — 파괴 op 수 비례 (확정)

### 정책

```
destroyed_op_count = jd_count_eff × c            (c = stage_count)
cp_tl_seconds      = destroyed_op_tl_multiplier × destroyed_op_count
```

`sw_cp`의 `batch_tl_mode="proportional"` — 회당 TL = `kappa × non_time_fixed_op_count`
(`sw_cp/dispatcher.py:289-293`) — 과 **같은 꼴**이다. 파괴된 job은 전 스테이지에서
자유로우므로 자유 op 수가 정확히 `jd_count_eff × c`다.

**test config 상수: `0.005`** (초/op).

| `jd` | `c` | 파괴 op 수 | CP TL |
|---|---|---|---|
| 1 | 5 | 5 | 0.025 s |
| 5 | 5 | 25 | 0.125 s |
| 10 | 10 | 100 | 0.5 s |

**바깥 루프의 `jd` ramp와 TL이 자동으로 맞물린다** — 이웃이 넓어질수록 예산도
비례해 늘어난다. 고정 `cp_tl`은 `jd=1`에 과하고 `jd=20`에 모자라는 문제가 있었다.

### 옵션 설계

`sw_cp`의 명명·검증 패턴을 그대로 따른다 (`sw_cp/option.py:140-168`):

```python
# JobContribCpOption 추가 필드
cp_tl_mode: Literal["constant", "proportional"] = "constant"
destroyed_op_tl_multiplier: float | None = None      # kappa, 초/op
```

`__post_init__` 검증:
- `destroyed_op_tl_multiplier is not None and <= 0` → `ValueError`
- `cp_tl_mode == "proportional" and destroyed_op_tl_multiplier is None` → `ValueError`

디스패처는 `jd_count_eff`가 확정된 **직후**(early exit 판정 뒤, 모델 구축 전)에
`cp_tl_seconds`를 계산한다. 기존 `tl_bounds` 로직(`dispatcher.py:166-191`)은
그대로 — `cp_tl`과 `wall_clock_deadline_sec` 중 **작은 쪽**을 취하고, 고갈 시
`budget_exhausted_before_solve:<binding>`로 fallback한다. 즉 **반복해도 총 TL을
넘지 않는다.**

`metrics`에 `destroyed_op_count`와 해석된 `cp_tl_seconds`를 싣는다 (Phase B 자료).

### ⚠ 주의: TL이 CP-SAT 시동 비용보다 짧을 수 있다

`jd=1, c=5`면 TL이 **0.025초**다. 모델 구축(`BaseModelBuilder.build` — `n·c` 구간
변수 + cumulative 제약)과 solver 초기화가 그보다 오래 걸릴 가능성이 높다. 그러면
"TL 0.025초"는 실질적으로 **hint 검증만 하고 끝나는 회차**가 된다 — 개선이 없으니
안쪽 루프가 1회에 종료되고 곧장 다음 레벨로 넘어간다. **동작은 안전하지만 작은 `jd`
구간이 통째로 무의미해질 수 있다.**

→ **하한 floor(`min_cp_tl_seconds`)는 도입하지 않는다** (§9 D3).
Phase B에서 회차별 `elapsed` vs `cp_tl_seconds` vs `cpsat_status`를 보고 재판단한다.

---

## 6. 시간에 따른 objective 변화 plotting

### 이미 있는 것

`_obj_log.json` → `<ins>_progress_plot.png` 파이프라인이 **이미 범용**이다:

- 각 스텝의 `AlgRecord.progress_log`(`ProgressLogEntry(elapsed_sec, obj_value,
  obj_bound, note)`)를 `_save_obj_log`(`ffcddw_single_instance_runner.py:653`)가
  `start_time = timer.elapsed_sec - report.elapsed_time`으로 **컨트롤러 시간축에
  재정렬**해 접는다.
- `_render_progress_plot`(`reporting.py:341`)이 UB(실선)/LB(파선) step chart를
  그리고, 스텝 경계를 `notes`의 세로 라벨로 표시한다.

**즉 composite가 따로 할 일은 없다** — 내부 `job_contrib_cp`가 회차마다
`_register`하므로 회차별 점이 자동으로 찍힌다.

### 부족한 것 — solve 중 궤적이 없다

현재 `job_contrib_cp` 디스패처는 `progress_log`에 **최종 1점만** 넣는다
(`dispatcher.py:304-310`). 반면 `sw_cp`는 `ObjectiveValueRecorder`를
`solution_callback`으로 물려 **CP-SAT가 해를 찾을 때마다** 점을 남긴다
(`sw_cp/dispatcher.py:317-318`). 그래서 `incremental_sw_cp`의 곡선은 촘촘하고
`job_contrib_cp`의 곡선은 회차당 계단 하나다.

**변경**: 디스패처에서 `ObjectiveValueRecorder`를 붙인다.

```python
recorder = ObjectiveValueRecorder()
status = solver.solve(mdl, solution_callback=recorder)
```

- `progress_log` = recorder 항목들(**CP 프레임**) + 최종 1점(**후처리 프레임**, `post_obj`).
- `offset_sec = recorder.time_started - start`로 스텝 시작 기준으로 재기준화
  (`sw_cp/dispatcher.py:325`와 동일).
- **sw_cp와 달리 offset 보정이 필요 없다.** `sw_cp`는 sub-instance만 모델링해
  나머지 job의 E+T 상수를 더해야 하지만(`_compute_full_progress_offset`),
  `job_contrib_cp`는 **전체 인스턴스**를 모델링하므로 CP 목적값이 곧 전역 목적값이다.
- 최종점의 `post_obj`가 CP 목적값보다 **클 수 있다**(`make_semi_active`가
  earliness를 늘릴 수 있음 — 선행 문서 §1-(5)). 그래서 마지막 점에서 곡선이 위로
  튈 수 있는데, `_render_progress_plot`의 `_running_min`이 **증가 점을 버리므로**
  플롯은 단조 감소로 그려진다. **플롯이 실제보다 낙관적으로 보일 수 있다** —
  `metrics`의 `cpsat_obj` vs 등록 `obj_value` 비교가 진짜 값이다. §9 D4.

### 회차 라벨 폭주

회차마다 `temporarily_extended_context`로 다른 step label이 붙으므로 플롯에 세로
주석이 회차 수만큼 찍힌다. 수십 회면 판독 불가가 된다.
→ 라벨을 `jd{jd:03d}_r{rep:03d}` 형태로 짧게 유지하고, 그래도 과밀하면
`_render_progress_plot`에서 주석을 **`jd`가 바뀌는 경계에서만** 표시하도록
후속 조정 (§9 D5).

---

## 7. 시그니처 초안

```python
def incremental_job_contrib_cp(
    self,
    # --- 파괴 규모 범위 (확정) ---
    jd_start: int | str = 1,
    jd_end: int | str = "0.1n",
    jd_step_size: int = 1,
    # --- 시간 제약 (확정: 파괴 op 수 비례) ---
    destroyed_op_tl_multiplier: float = 0.005,
    min_remaining_sec: float | None = None,
    # --- 이하 job_contrib_cp로 그대로 전달 ---
    pf_method: PFMethod = "PF1",
    solver_thread_cnt: int = 1,
    horizon_multiplier: float = 1.25,
    error_if_infeasible: bool = False,
    log_search_progress: bool = False,
    draw_gantt: bool = False,
) -> None:
```

- `jd_step_size`는 **optional keyword, 기본 `1`** — 대부분의 시나리오는 지정하지
  않는다. `n`이 커서 레벨 수가 많을 때만 YAML에서 올린다 (`n=200`, `jd_end="0.1n"`
  이면 레벨이 20개).
- `cp_tl`(고정 초)은 **composite에 노출하지 않는다** — 이 스텝의 TL 정책은
  파괴 op 비례로 확정됐다. 단발 `job_contrib_cp`는 기존 `cp_tl`을 유지한다
  (두 모드가 옵션 층에서 `cp_tl_mode`로 공존, §5).
- `min_remaining_sec` 기본값(`None`)은 "직전 회차의 `cp_tl_seconds`의 절반"으로
  동적 결정 — 고정 초를 config에 박게 하지 않는다.
- `draw_gantt` / `log_search_progress`는 회차마다 산출물을 만들므로 **반복
  스텝에서는 기본 `False`**, 디버깅 시에만 켠다.

### 아티팩트

- 회차별 `_metrics.yaml`은 `temporarily_extended_context` 덕에 충돌하지 않는다
  (`incremental_sw_cp`의 `batch_{n:03d}` / `reps_{n:03d}`와 동형).
- **다만 회차 × 1440 인스턴스면 파일이 폭증한다.** composite가 루프 종료 후
  회차별 한 줄 요약(`jd`, `rep`, `jd_count_eff`, `destroyed_op_count`,
  `cp_tl_seconds`, `cpsat_status`, `obj_before`, `obj_after`, `elapsed`,
  `exit_reason`)을 **`_incremental_job_contrib_cp_log.yaml` 한 파일**로 떨어뜨린다
  (`sw_cp`가 `_step_log.yaml`을 쓰는 것과 같은 패턴). Phase B의 1차 자료다.
- 요약 파일은 **상위 키 3개**를 갖는다:

  | 키 | 값 |
  |---|---|
  | `exit_reason` | 루프 **전체**의 종료 사유 — `completed` / `jd_ge_n` / `zero_obj` / `saturated` / `budget` / `stopping_condition` |
  | `same_set_skips` | 파괴 집합 반복으로 **CP 없이 건너뛴** 회차 수 (§3-(a)) |
  | `rows` | **CP solve 1회당 정확히 1행** |

- 행별 `exit_reason`은 그 회차의 결과 — `improved` / `no_improvement` / `saturated`.
- **`same_set`은 전체 종료 사유가 아니다.** 안쪽 루프만 끊고 바깥 루프는 계속되므로
  `exit_reason`에 나타나지 않고, **행도 남기지 않는다**(행 = CP solve 1회 불변식).
  대신 `same_set_skips` 카운터로 관측한다.
- **`jd_ge_n`과 `zero_obj`는 회차 없이 종료하므로 `rows`가 빈 리스트일 수 있다** —
  그 경우에도 파일은 쓰고 상위 키를 남긴다 (Phase B의 분모가 깨지지 않게).

### 시나리오 YAML 예시 (test config)

```yaml
- method: incremental_job_contrib_cp
  jd_start: 1
  jd_end: "0.1n"
  # jd_step_size: 1        # optional, 기본 1 — 큰 n에서만 올린다
  destroyed_op_tl_multiplier: 0.005
  pf_method: PF1
  solver_thread_cnt: 1
```

---

## 8. 구현 단계 (TDD)

| 단계 | 내용 | Red로 먼저 세울 테스트 |
|---|---|---|
| **P1** | `select_jd_jobs` 분리 (§4) | 기존 디스패처 동작 불변(회귀) / 기여도 0인 job 제외 / 동점 `job_id` 결정론 / 전 job obj=0이면 `[]` |
| **P2** | TL 정책 (§5) | `cp_tl_mode="proportional"` → `cp_tl == kappa × jd_count_eff × c` / kappa 없이 proportional → `ValueError` / kappa ≤ 0 → `ValueError` / `metrics["destroyed_op_count"]` 기록 / `wall_clock_deadline` 이 더 작으면 그쪽이 binding |
| **P3** | progress log (§6) | `log_search_progress=False`에서도 recorder 점이 ≥1개 / 마지막 점이 `post_obj` / `elapsed_sec`가 스텝 시작 기준 단조 증가 |
| **P4** | composite 골격 | composite 자신 `_register` **0회**, 내부 스텝이 회차당 1회 (history 길이로 검증) / `jd_end < jd_start` → `ValueError` / `jd_step_size < 1` → `ValueError` |
| **P5** | 안쪽 루프 종료 — 개선 없음 | 개선 없는 스텝 stub → 해당 `jd`에서 정확히 1회 후 다음 레벨로 이동 |
| **P6** | 안쪽 루프 종료 — 집합 반복 | 개선은 있으나 `select_jd_jobs` 결과가 직전과 동일 → **CP를 호출하지 않고** 다음 레벨로 이동 (호출 횟수로 검증 — §3-(a)의 핵심) |
| **P7** | 바깥 루프 · `jd_step_size` | `jd_start=1, jd_end=3` 개선 지속 시 `jd` 1→2→3 순회 후 종료 / `jd_step_size=4`, `[1,10]` → 레벨이 정확히 `[1,5,9]` (`jd_end` 미방문을 **정상**으로 고정) |
| **P8** | **`jd >= n` 가드 (§3-(b))** | `jd_end`가 `n`에 닿는 인스턴스 → `jd == n` 레벨에서 **`job_contrib_cp`가 호출되지 않음**(spy) / `jd_start >= n` → **CP 0회, `_register` 0회, 예외 없이 반환**, `exit_reason == "jd_ge_n"` / 단발 `job_contrib_cp(jd_target=n)`은 **여전히 동작**(회귀 가드) |
| **P8b** | 포화 종료 (§3-(c)) | `len(selected) < jd` 관측 시 그 회차까지 수행 후 전체 종료, `exit_reason == "saturated"` |
| **P9a** | 전체 조기 종료 — obj 0 | 전 job이 due window 안인 incumbent → CP 0회 호출 후 즉시 종료, `exit_reason == "zero_obj"` |
| **P9** | 예산 가드 | `min_remaining_sec` 미만이면 회차 진입 안 함 / **총 소요가 컨트롤러 `timelimit`을 넘지 않음** |
| **P10** | 요약 로그 | `_incremental_job_contrib_cp_log.yaml` 행 수 == 실제 회차 수 / numpy 스칼라 없음(`dump_yaml` 회귀 — 선행 문서의 `makespan` 사고와 같은 함정) |
| **P11** | plot 파이프라인 | 회차 여러 번 돈 `_obj_log.json`에서 회차별 점이 시간 순으로 나오고 `_render_progress_plot`이 예외 없이 PNG를 만든다 |

각 단계 후 `uv run ruff check`, 필요 시 `uv run ruff format`.

---

## 9. 결정 사항 (전부 확정)

| # | 항목 | 결정 |
|---|---|---|
| **D1** | `jd_start` / `jd_end` 기본값 | **`1` / `"0.1n"`** (rev.2 초안 `"0.2n"`에서 좁힘) |
| **D2** | `jd` 증가폭 | **`jd_step_size: int`, optional keyword, 기본 `1`** |
| **D3** | `min_cp_tl_seconds` floor | **도입하지 않는다.** Phase B 진단(`elapsed` vs `cp_tl_seconds`)을 보고 재판단 |
| **D4** | 후처리 목적값 역전과 플롯 | **현행 유지.** 등록값은 정확하고 `metrics["cpsat_obj"]`가 남는다. 역전 빈도를 Phase B에서 먼저 센다 |
| **D5** | 플롯 주석 과밀 | **짧은 라벨(`jd{jd:03d}_r{rep:03d}`)로 1차 진행.** 판독 불가하면 `jd` 경계에서만 표시하도록 후속 조정 |
| **D6** | `incremental_sw_cp`와의 배치 | **(a) `incremental_sw_cp` 뒤에 마무리 이웃으로.** 스모크와 같고 비교 기준선이 이미 있다 |
| **D7** | `jd >= n` | **최적화하지 않고 전체 종료** (§3-(b)). composite 책임이며 단발 `job_contrib_cp`는 무변경 |

### D1 근거

이 저장소의 총 예산은 `0.09nc`
(`metadata/20260728/job_contrib_cp_resume_smoke.yaml:68`), `job_contrib_cp`
잔여는 `~0.054nc`다. `n=100, c=5`면 잔여 `27초`이고
`jd_end="0.1n"=10` 레벨의 한 회차가 `0.005 × 10 × 5 = 0.25초`이므로 범위 전체를
여유 있게 돈다. `"0.2n"`도 예산상 불가능하진 않지만, **큰 `jd`에서 개선이 실제로
나오는지를 Phase B로 먼저 확인**한 뒤 넓히는 편이 낫다 (§10 스윕 축에 `"0.2n"`을
남겨 둔다).

---

## 10. 실험 계획

### Phase A — 반복이 단발보다 나은가 (본론)

- **baseline-0**: 현행 최고 조합 (`calc_mcf_lb_and_derive_full_sch` →
  `incremental_sw_cp`), 총 TL `0.09nc`
- **baseline-1**: 위 + **단발** `job_contrib_cp` (스모크와 동일 설정, 총 TL 동일)
- **arm**: 위 + **`incremental_job_contrib_cp`**, **총 TL 동일 (`0.09nc`)**
- **스윕 축**: `destroyed_op_tl_multiplier ∈ {0.002, 0.005, 0.01}`
  × `jd_end ∈ {"0.05n", "0.1n", "0.2n"}` (기본은 `"0.1n"` — D1)
- `jd_step_size`는 스윕하지 않는다 (`1` 고정). 레벨 수가 예산을 압박한다는 증거가
  Phase B에서 나오면 그때 축으로 올린다.
- **비교 지표**: paired per-instance dRPDf 부호 검정 (**baseline-1 대비**).
  단발 대비 **반복의 순증분**이 이 실험의 질문이다.
- **`solver_thread_cnt=1`** — 파일럿은 결정론에 가깝게 유지
  (메모리: 8-worker wall-clock CP-SAT는 비결정적, 1440 그리드 평균 obj ±350이 잡음 바닥).
- **인스턴스**: PRA2017 층화 슬라이스. 1차는 스모크와 같은 8개 →
  이상 없으면 ~160개 → 확정 후 1440 전량.
  층화 기준은 `pra2017-instance-params` skill.
- 산출물: `output/20260728_incremental_job_contrib_cp/<timestamp>/` (repo 안, `/tmp` 금지)

### Phase B — 루프가 실제로 무엇을 했나 (저비용, 병행)

1차 자료는 `_incremental_job_contrib_cp_log.yaml` (§7) 한 파일이다.

- **`exit_reason` 분포** — `completed` / `jd_ge_n` / `zero_obj` / `saturated` /
  `budget` / `stopping_condition`.
  `jd_ge_n`은 **작은 인스턴스에서만** 나와야 한다 — 큰 `n`에서 나오면 설정 오류다.
  `budget`이 지배적이면 `destroyed_op_tl_multiplier`가 예산 대비 과하다.
- **`same_set_skips` / `len(rows)` 비율** — §3-(a) 판정이 실제로 예산을 아끼고
  있는지에 대한 직접 증거. 이 비율이 높으면 보류한 다양화 정책(§11)을 다시 볼 신호다.
- **`jd`별 개선량** — 큰 `jd`에서 개선이 안 나오면 `jd_end`를 낮춰 예산을 앞
  스텝에 돌린다. 반대로 마지막 `jd`까지 개선이 이어지면 `jd_end`를 `"0.2n"`으로
  올린다 (D1의 재판단 지점).
- **`elapsed` vs `cp_tl_seconds` vs `cpsat_status`** — Q3(TL floor)의 판단 자료.
  작은 `jd`에서 `elapsed >> cp_tl_seconds`면 시동 비용이 지배한다는 뜻.
- **`cpsat_obj` vs 등록 `obj_value`** — Q4(후처리 역전)의 빈도.
- `positive_contrib_job_count` vs `jd_count_eff` — 포화 시점 (§3-(b) 검증).

---

## 11. 보류 (rev.1에서 내려온 항목)

확정된 종료 규칙(개선 없음 / 집합 반복)이 고정점 문제를 닫으므로 아래는 **하지
않는다.** Phase B에서 `same_set_skips`가 지배적이고 그 시점의 목적값이
좋지 않을 때만 다시 연다.

| 항목 | 내용 | 다시 볼 조건 |
|---|---|---|
| 순위 회전 (`jd_rank_offset`) | 개선 없을 때 순위 창을 이동해 같은 크기의 다른 집합 선택 | `same_set_skips` 비율이 높음 |
| 가중 랜덤 선택 | $f_j$ 가중 비복원 추출 (hybridflowshop `weighted_random`) | 위 + 회전으로도 부족할 때. **이 저장소 최초의 알고리즘 층 난수**가 되므로 `rng_seed`를 옵션·`metrics`·YAML에 명시 노출해야 하고 A/B 잡음 판정이 어려워진다 |
| tight-arc adjacency | tardy job은 역방향, early job은 정방향으로 간극 0 machine arc를 BFS 확장 (§2의 E/T판 critical adjacency) | 위 + "서로 무관한 top-k"가 한계라는 증거가 있을 때 |

TODO.md 등재 대상은 아니다 — 이 문서가 보류 사유와 재개 조건을 갖고 있다.

---

## 12. 구현 결과 (2026-07-28)

| 항목 | 값 |
|---|---|
| 신규 테스트 | **18** — `tests/orchestration/test_incremental_job_contrib_cp.py` |
| 전체 스위트 | **768 passed** (구현 전 738 → 단발 스텝 회귀 포함 +30) |
| lint | `uv run ruff check src/ tests/` clean, `ruff format --check` clean |
| 실험 | **미실행** (§10은 그대로 열려 있다) |

### 변경 파일

| 파일 | 내용 |
|---|---|
| `algorithm/job_contrib_cp/selection.py` | **신규** — `select_jd_jobs` (§4 SSOT) |
| `algorithm/job_contrib_cp/__init__.py` | `select_jd_jobs` 재수출 |
| `algorithm/job_contrib_cp/option.py` | `cp_tl_mode` / `destroyed_op_tl_multiplier` + 검증 (§5) |
| `algorithm/job_contrib_cp/dispatcher.py` | `select_jd_jobs` 사용, proportional TL, `ObjectiveValueRecorder` progress log, `metrics`에 `destroyed_op_count` / `cp_tl_seconds` |
| `orchestration/controller.py` | `job_contrib_cp`에 TL 모드 인자 2개 추가, **`incremental_job_contrib_cp` 신규**, `_dump_incremental_job_contrib_cp_log` 헬퍼 |

### 설계 대비 갈린 지점

1. **`job_contrib_cp_last_metrics` stash를 만들지 않았다.** rev.1이 제안했던
   "컨트롤러가 `_register` 이후 `metrics`를 stash해 회차 간 `selected_jobs`를
   비교한다"는 장치는 **§4의 `select_jd_jobs` 분리로 불필요해졌다** — composite가
   같은 순수 함수를 직접 호출해 **CP 호출 전에** 집합을 얻는다. 컨트롤러에 상태
   필드가 하나도 늘지 않았다. (§3-(a)가 요구한 "호출 전 판정"이 이 쪽을 강제했다.)
2. **요약 파일에 `same_set_skips` 상위 키를 추가했다.** 최초 구현은 건너뛴 회차도
   `rows`에 (`elapsed: 0.0`, `cp_tl_seconds: null`) 행으로 남겼는데, 이러면
   "행 = CP solve 1회"라는 P10 불변식이 깨지고 Phase B의 회차 수가 실제 solve 수보다
   부풀려진다. 행을 없애는 대신 카운터로 관측성을 유지했다 (§7).
3. **`cp_tl_mode`는 `job_contrib_cp`에도 노출했다.** composite는 항상
   `"proportional"`을 고정으로 넘기지만(§7), 단발 스텝에서 이 정책을 직접 쓰고
   싶을 수 있어 인자를 막지 않았다. 단발 스텝의 기본값은 `"constant"`이므로
   **기존 시나리오 YAML의 동작은 불변**이다.

### 리뷰에서 잡아 고친 것 (전부 테스트로 고정)

1. **`get_file_path_for_subroutine`(예외 발생) 사용** → `try_get_...`(없으면
   `None`)으로 교체. working dir이 없는 실행에서 **모든 작업을 마친 뒤** 마지막에
   `AttributeError`로 터지던 경로였다. `sw_cp`의 `_step_log.yaml`,
   `job_contrib_cp`의 `_metrics.yaml`과 같은 관례로 맞췄다. 정상 경로와
   `jd_ge_n` 조기 반환 경로 **양쪽에** 회귀 테스트를 넣었다.
2. **검증 순서** — `jd_end < jd_start` 검사가 `jd_start >= n` 조기 반환보다
   뒤에 있어, 두 조건이 겹치면 설정 오류가 `ValueError` 없이 조용히 반환됐다.
   앞으로 옮겼다. **이 수정이 기존 테스트 하나를 깨뜨렸는데**, 그 테스트가
   `jd_start=4, jd_end="0.1n"`(=1)이라는 **무효한 범위**를 쓰면서 조기 반환에
   가려 통과하던 것이었다 — 정확히 이 수정이 겨냥한 문제다.
3. **`metrics["makespan"]`의 `int()` 근거 주석 삭제** → 복원. 선행 문서 §4에
   기록된 실제 사고(numpy 스칼라 → `dump_yaml`의 `RepresenterError`로 스텝 전체
   실패)의 재발 방지 근거다.
4. **`same_set` 행 제거** (위 「갈린 지점」 2번).

---

## 13. 관련 문서

- 선행 계획(단발 스텝): `plans/experiment/20260728/critical_ns_port_from_hfs.md`
- subroutine step 계약: `src/ffc_ddw_sum_et/orchestration/CLAUDE.md`
- 문제 정의: `docs/problem-description.md`
- 알고리즘 계약: `docs/algorithm-principles.md`
- 같은 골격의 선례: `orchestration/controller.py:2489` (`incremental_sw_cp`)
- TL 비례 정책 선례: `algorithm/sw_cp/dispatcher.py:289`, `algorithm/sw_cp/option.py:140`
- progress log 선례: `algorithm/sw_cp/dispatcher.py:317`,
  `algorithm/cpsat_callbacks/obj_value_recorder.py`
- 플롯 파이프라인: `orchestration/ffcddw_single_instance_runner.py:653` (`_save_obj_log`),
  `orchestration/reporting.py:341` (`_render_progress_plot`)
- 인스턴스 파라미터: `.claude/skills/pra2017-instance-params/SKILL.md`
- 참조 구현: `/home/hjt/code/hybridflowshop/hybridflowshop/controller/hfs_cp_lns.py`
  (반복 `:13325` / 단발 `:13067` / 이웃 구성 `:12984`),
  `hybridflowshop/schedule_lite.py:2031` (critical block)

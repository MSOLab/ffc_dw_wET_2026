# Plan: wxd6 / wxd7 dispatch priority (two-center group sorting)

> 작성일: 2026-06-25
> 선행: `wxd2`(`ffc_ddw_params.py:816-877`), `wxd3/wxd4`(`:879-969`),
>       `wxd5`(`:971-1027`), 관찰 노트 `plans/experiment/20260625/wxd5_dbar_completion_observation.md`
> 동기: instance 60 에서 wxd5 가 50 job 전부 early 로 partition → 단일 d̄(중점평균
>       지배 or ×2 하한) center 로 두 그룹을 정렬하는 것이 완료시점 분포와 괴리.
>       **partition 은 그대로 두고, 그룹별 정렬 center 를 분리**한다.

---

## 0. 핵심 아이디어 (확정)

partition(early/late 배정)은 **wxd5 와 동일**하게 유지하고, **그룹 내 정렬에
쓰는 center 만 그룹별로 분리**한다.

- **early group** — 앞쪽에 몰려 schedule 되어 사실상 makespan 부근까지 완료가
  퍼진다. 따라서 정렬 center = **approximate makespan**
  `early_center = min_j r_j + Σ_j p_last_j / m_last` (×2 **안 함**, floor 없음).
- **late group** — 뒤로 밀리지만, "가장 일찍(min r_j) 놓였을 때의 penalty" 가
  그룹 내 순서를 가른다. 정렬 center = `late_center = min_j r_j` (raw).

이 center 분리는 **두 rule 이 공유**한다. 차이는 **정렬 키의 형태**뿐:

| | 정렬 키 형태 | early center | late center |
|---|---|---|---|
| **wxd6** | wxd2/wxd5 곱셈형 aversion 키 | approx makespan | min r_j |
| **wxd7** | wxd3/wxd4 쌩 weighted penalty 키 | approx makespan | min r_j |

확정된 결정(질의응답):

| 항목 | 결정 |
|---|---|
| `Σ p_last` 범위 (early_center) | **전체 job** (wxd5 d̄ 와 동일 범위) |
| early_center floor | **없음** — raw `min r_j + Σp_last/m_last` 그대로 (max(midpoint,…) 안 씌움) |
| late_center | **raw `min r_j`** |
| partition 기준 | **wxd5 방식** — `d̄₅ = max(중점평균, min r_j + Σp_last/(m_last·2))` 로 aversion score, tie(`>=`)→late |
| wxd7 구조 | 새 2-center + 쌩 penalty 키 (단일 center wxd3/4 복제 아님) |

> 주의: partition 에 들어가는 d̄₅ 는 wxd5 의 ×2 짜리 그대로 유지. **정렬 center**
> 만 새 값(early=÷1 makespan, late=min r_j)으로 교체한다. 즉 한 rule 안에서
> partition center(d̄₅)와 정렬 center(early/late)가 서로 다르다 — 의도된 분리.

---

## 1. 공통 사전계산

```python
ewt = self._job_2_ewt_map
twt = self._job_2_twt_map
ddw = self._job_2_due_window_map               # ddw[j] = (d⁻_j, d⁺_j)
job_2_pos = {j: pos for pos, j in enumerate(self._job_id_list)}

d_mid = {j: (ddw[j][0] + ddw[j][1]) / 2 for j in self._job_id_list}
mean_midpoint = sum(d_mid.values()) / len(d_mid)

last_stage_id = self.stage_id_list[-1]
p_last = self.get_job_2_p_map_for_stage(last_stage_id)     # dict[j → p_last_j]
r_j    = self.get_job_2_p_sum_except_last_stage()          # dict[j → Σ_{i≠last} p_ij]
p_last_total = sum(p_last.values())
min_r  = min(r_j.values())
m_last = self.last_stage_mc_count

# partition center (wxd5 d̄, ×2 유지)
d_bar = max(mean_midpoint, min_r + p_last_total / (m_last * 2))

# 정렬 center (신규, 그룹별)
early_center = min_r + p_last_total / m_last     # approx makespan, ÷2 없음, floor 없음
late_center  = min_r                             # raw

# partition (wxd5 와 100% 동일: aversion score + tie>=→late)
earliness_aversion = {j: ewt[j] + (ddw[j][0] - d_bar) for j in self._job_id_list}
tardiness_aversion = {j: twt[j] + (d_bar - ddw[j][1]) for j in self._job_id_list}
early = [j for j in self._job_id_list if earliness_aversion[j] <  tardiness_aversion[j]]
late  = [j for j in self._job_id_list if earliness_aversion[j] >= tardiness_aversion[j]]
```

맵 접근은 전부 `m[j]` 직접 인덱싱 ([[feedback_no_defensive_get]]).

---

## 2. wxd6 — 곱셈형 키 + 2-center

`ew_max = max(ewt.values())`, `tw_max = max(twt.values())` 추가.

```python
def early_key(j):   # wxd2/5 형태, d̄ 자리에 early_center
    return ((twt[j] - 2*ewt[j] + 2*ew_max) * (ddw[j][0] - early_center), job_2_pos[j])

def late_key(j):    # wxd2/5 형태, d̄ 자리에 late_center
    return ((ewt[j] - 2*twt[j] + 2*tw_max) * (ddw[j][1] - late_center), job_2_pos[j])

return sorted(early, key=early_key) + sorted(late, key=late_key)
```

wxd2/5 와의 유일한 차이: early_key 의 center 가 `d̄ → early_center`,
late_key 의 center 가 `d̄ → late_center`. partition·tie 동일.

---

## 3. wxd7 — 쌩 weighted penalty 키 + 2-center

`ew_max/tw_max` 불필요.

```python
def early_key(j):   # wxd3/4 형태, center=early_center 에서의 tardiness penalty
    tp = twt[j] * max(early_center - ddw[j][1], 0)
    return (-tp, job_2_pos[j])

def late_key(j):    # wxd3/4 형태, center=late_center 에서의 earliness penalty
    ep = ewt[j] * max(ddw[j][0] - late_center, 0)
    return (ep, job_2_pos[j])

return sorted(early, key=early_key) + sorted(late, key=late_key)
```

wxd3/4 와의 차이: (a) partition 이 wxd3/4 의 `<=`→early 가 아니라 **wxd5 의
`>=`→late** (partition 을 wxd5 로 통일했으므로), (b) 단일 center 가 아니라
early=approx makespan / late=min r_j 의 **2-center**.

> 방향 sanity: early 는 `-tp` 오름차순 = tardiness penalty 큰 job 을 앞으로
> (makespan 부근 완료 시 가장 늦을 위험이 큰 것부터 rush). late 는 `ep` 오름차순
> = min r_j 에 놓았을 때 earliness penalty 큰(=가장 일찍 끝나기 싫은) job 을
> 그룹 맨 뒤로. wxd3/4 의 그룹 내 방향과 동일.

---

## 4. 구현 위치

### 4.1 `parameters/ffc_ddw_params.py`
`get_wxd5_job_sequence`(`:1027`) 직후에 `get_wxd6_job_sequence`,
`get_wxd7_job_sequence` 두 메서드 추가. §1 사전계산을 각자 인라인(자족 작성
컨벤션 — wxd1~5 모두 인라인). docstring 에 center 분리 의도 명시.

### 4.2 `parameters/sorter.py`
- `DispatchSeqKey` Literal 에 `"wxd6"`, `"wxd7"` 추가 (`"wxd5"` 다음, `:46`).
- `dispatch_seq_job_sequence` 의 `direct` dict 에 두 줄 (`:85` 다음):
  ```python
  "wxd6": instance.get_wxd6_job_sequence,
  "wxd7": instance.get_wxd7_job_sequence,
  ```
- `ParamSortKey` / `param_sort_job_sequence` 불변.

### 4.3 검증
- `uv run ruff check` / `uv run ruff format`.
- sanity: 작은 인스턴스에서 `dispatch_seq_job_sequence(inst, "wxd6")`,
  `"wxd7"` 가 전체 job permutation(중복/누락 없음)인지.
- instance 60 재현: `early_center` (÷1) 가 d̄₅(559.5, ÷2) 대비 약 2배
  (≈ 132 + 2565/3 = 132 + 855 = 987) 로 makespan(1283)에 더 근접하는지 확인.
  partition 은 wxd5 와 동일(50 early / 0 late)이어야 함 — d̄₅ 그대로이므로.

---

## 5. config (승인 후, 별도 단계 가능)

`metadata/20260625/dispatch_sequence_full_sweep_config.yaml` 에 wxd5 와 동형으로
`sd_wxd6`/`sd_wxd7`(simple) + `rd_wxd6`/`rd_wxd7`(reversed) 4 scenario 추가:

```yaml
  - name: sd_wxd6 # wxd2 골격 + 정렬 center 분리(early=approx makespan, late=min r_j)
    timelimit: "0.09nc"
    output_subdir: sd_wxd6
    subroutine_flow: [{ method: initialize_by_simple_dispatch, sequence: wxd6 }]
  - name: sd_wxd7 # 쌩 weighted penalty 정렬 + 동일 2-center
    timelimit: "0.09nc"
    output_subdir: sd_wxd7
    subroutine_flow: [{ method: initialize_by_simple_dispatch, sequence: wxd7 }]
```
(reversed 쪽도 `rd_wxd6`/`rd_wxd7` 동형.) 실행용 subset config
(`wxd67_only_config.yaml`)는 필요 시 `wxd5_only_config.yaml` 패턴으로 별도 생성.
실행 worker 수는 `instance_worker_cnt: 48` ([[feedback_fast_experiments]]).

---

## 6. 범위 밖
- `controller.py` 전용 step 메서드 추가 안 함 (wxd3/4/5 와 동일, generic sweep 만).
- inspector(`render_priority_inspector.py`) 의 partition 오버레이는 wxd2/wxd5
  전용 경로. wxd6/7 은 generic `T-E@center` 경로로 그려짐. 필요 시 별도 작업.

---

## 7. 체크리스트
- [ ] `early_center = min r_j + Σ_all p_last / m_last` (×2 없음, floor 없음)?
- [ ] `late_center = min r_j` (raw)?
- [ ] partition d̄ = wxd5 의 `max(중점평균, min r_j + Σp_last/(m_last·2))`, tie`>=`→late?
- [ ] wxd6 = 곱셈형 키(`ew_max/tw_max`) + early/late center 교체?
- [ ] wxd7 = 쌩 penalty 키(`-tp`/`ep`) + early/late center 교체?
- [ ] `Σ p_last` 전체 job 합?
- [ ] sorter `DispatchSeqKey` 에 wxd6/wxd7 추가 + `direct` 등록, `ParamSortKey` 불변?
- [ ] 맵 접근 전부 `m[j]` 직접 인덱싱?
- [ ] permutation sanity (중복/누락 없음)?
```

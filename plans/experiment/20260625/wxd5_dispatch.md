# Plan: wxd5 dispatch priority

> 작성일: 2026-06-25
> 선행: `wxd2` (`ffc_ddw_params.py:816-877`), wxd3/wxd4
>       (`:879-969`, `plans/experiment/20260625/wxd3_wxd4_dispatch.md`)
> 관련 rule: `wxd2` (구조 동일), `wxd4` (baseline 헬퍼 동일)

---

## 0. 요약

`wxd5` 는 **wxd2 와 완전히 동일**하다 — partition 기준식, 그룹 내 정렬식
(`(w⁺−2w⁻+2·ew_max)(d⁻−d̄)` / `(w⁻−2w⁺+2·tw_max)(d⁺−d̄)`), tie-breaking(`>=`→late)
모두 그대로. **유일한 차이는 `d̄`(d_bar) 의 정의**다.

wxd2 의 `d̄` = 단순 윈도우 중점 평균. wxd5 는 여기에 "마지막 stage 완료
추정 하한"을 `max` 로 씌운다:

```python
d̄_wxd5 = max(
    mean_midpoint,                                       # = wxd2 의 기존 d̄
    min_j r_j + Σ_j p_last_j / (m_last × 2),
)
```

새 `d̄` 는 **wxd2 가 d̄ 를 쓰던 모든 곳**(partition aversion score + 양 그룹
정렬 키)에 그대로 들어간다. (확정: "전부 교체".)

---

## 1. 새 d_bar 정의 (확정된 해석)

```python
d_mid[j]      = (d⁻_j + d⁺_j) / 2
mean_midpoint = mean_j(d_mid[j])                 # wxd2 의 기존 d̄

last_stage_id = self.stage_id_list[-1]
p_last        = self.get_job_2_p_map_for_stage(last_stage_id)   # dict[j → p_last_j]
r_j           = self.get_job_2_p_sum_except_last_stage()        # dict[j → Σ_{i≠last} p_ij]
p_last_total  = Σ_j p_last[j]                    # 전체 job 의 마지막 stage 처리시간 합

d_bar = max(
    mean_midpoint,
    min(r_j.values()) + p_last_total / (self.last_stage_mc_count * 2),
)
```

확정 사항:

| 항목 | 결정 |
|---|---|
| `Σ p_last` 의 job 범위 | **전체 job** (early group 아님). wxd2 는 partition *전*에 d̄ 를 계산하므로 early 에 의존 불가 |
| `r_j` | `get_job_2_p_sum_except_last_stage()` — 마지막 stage 제외 처리시간 합 (release proxy) |
| machine 수 | `last_stage_mc_count` (마지막 stage 병렬 기계 수), 거기에 **×2** |
| d̄ 적용 범위 | partition aversion score + 양 그룹 정렬 키 — wxd2 d̄ 사용처 **전부** |

> wxd4 와 대비: wxd4 는 baseline 을 **정렬에만** 쓰고 partition 은 평범한 d̄ 로
> 유지하며, `Σ p_last` 도 early group 한정 + `/m_last`(×2 없음). wxd5 는
> 정반대로 **d̄ 자체를 바꿔 partition·정렬에 동시 반영**하고, 전체 job 합을
> `/(m_last×2)` 로 나눈다. → 별개 rule.

---

## 2. 구현

### 2.1 `parameters/ffc_ddw_params.py`

`get_wxd4_job_sequence` 바로 뒤(현재 `:969` 이후)에 신규 메서드 추가.
wxd2 본문을 그대로 복제하되 `d_bar = ...` 한 줄만 위 §1 의 정의로 교체.
(wxd1/2/3/4 모두 partition 을 각자 인라인 → 자족 작성 컨벤션 유지.)

```python
def get_wxd5_job_sequence(self) -> list[str]:
    """wxd2 와 동일(partition·정렬·tie 모두)하나 d̄ 만 교체.

    d̄ = max(윈도우 중점 평균,
             min_j r_j + Σ_j p_last_j / (m_last × 2))
    — 마지막 stage 완료 추정 하한으로 center 를 뒤로 민다. 이 d̄ 가
    wxd2 의 partition aversion score 와 양 그룹 정렬식에 모두 들어간다.
    """
    ewt = self._job_2_ewt_map
    twt = self._job_2_twt_map
    ddw = self._job_2_due_window_map
    job_2_pos = {j: pos for pos, j in enumerate(self._job_id_list)}

    d_mid = {j: (ddw[j][0] + ddw[j][1]) / 2 for j in self._job_id_list}
    mean_midpoint = sum(d_mid.values()) / len(d_mid)
    last_stage_id = self.stage_id_list[-1]
    p_last = self.get_job_2_p_map_for_stage(last_stage_id)
    r_j = self.get_job_2_p_sum_except_last_stage()
    p_last_total = sum(p_last.values())
    d_bar = max(
        mean_midpoint,
        min(r_j.values()) + p_last_total / (self.last_stage_mc_count * 2),
    )
    ew_max = max(ewt.values())
    tw_max = max(twt.values())

    earliness_aversion_score = {
        j: ewt[j] + (ddw[j][0] - d_bar) for j in self._job_id_list
    }
    tardiness_aversion_score = {
        j: twt[j] + (d_bar - ddw[j][1]) for j in self._job_id_list
    }

    early = [
        j
        for j in self._job_id_list
        if earliness_aversion_score[j] < tardiness_aversion_score[j]
    ]
    late = [
        j
        for j in self._job_id_list
        if earliness_aversion_score[j] >= tardiness_aversion_score[j]
    ]

    def early_key(j: str) -> tuple[float, int]:
        return (
            (twt[j] - 2 * ewt[j] + 2 * ew_max) * (ddw[j][0] - d_bar),
            job_2_pos[j],
        )

    def late_key(j: str) -> tuple[float, int]:
        return (
            (ewt[j] - 2 * twt[j] + 2 * tw_max) * (ddw[j][1] - d_bar),
            job_2_pos[j],
        )

    return sorted(early, key=early_key) + sorted(late, key=late_key)
```

맵 접근은 전부 `m[j]` 직접 인덱싱 ([[feedback_no_defensive_get]]).
tie(`>=`→late) 는 wxd2 그대로 유지 (wxd3/4 의 `<=`→early 와 다름).

### 2.2 `parameters/sorter.py`

- `DispatchSeqKey` Literal 에 `"wxd5"` 추가 (`"wxd4"` 다음, `:45`).
- `dispatch_seq_job_sequence` 의 `direct` dict 에 한 줄 (`:83` 다음):
  ```python
  "wxd5": instance.get_wxd5_job_sequence,
  ```
- `ParamSortKey` / `param_sort_job_sequence` 는 **불변**.

### 2.3 검증

- `uv run ruff check` / `uv run ruff format`
- sanity: 작은 인스턴스에서 `dispatch_seq_job_sequence(instance, "wxd5")` 가
  전체 job permutation(중복/누락 없음)인지 확인.
- d̄ 비교: `min_j r_j + Σ p_last/(2·m_last) <= mean_midpoint` 인 인스턴스에서는
  `d̄_wxd5 == d̄_wxd2` → 시퀀스가 wxd2 와 동일해야 함. 그 반대(하한이 더 큰)
  인스턴스에서는 d̄ 가 뒤로 밀려 partition/정렬이 달라짐.

---

## 3. config (선택, 승인 후)

`metadata/20260625/dispatch_sequence_full_sweep_config.yaml` 에 wxd2/3/4 와
동형으로 `sd_wxd5` / `rd_wxd5` 2개 scenario 추가:

```yaml
  - name: sd_wxd5
    timelimit: "0.09nc"
    output_subdir: sd_wxd5
    subroutine_flow: [{ method: initialize_by_simple_dispatch, sequence: wxd5 }]
```
(reversed 쪽도 동일하게 `rd_wxd5`.)
**이번 작업 범위 포함 여부는 승인 시 확정.**

---

## 4. 범위 밖 (이번엔 건드리지 않음)

- `orchestration/controller.py` 의 `initialize_by_wxd2` 같은 전용 step 메서드는
  wxd3/wxd4 도 추가하지 않았음 → wxd5 도 generic sweep(`initialize_by_simple_dispatch`
  / `initialize_by_reversed_dispatch`)으로만 노출. (YAGNI)
- viz inspector(`scripts/render_priority_inspector.py`) 의 wxd2 partition 오버레이는
  `rule_key == "wxd2"` 전용. wxd5 는 generic `T-E@d̄` 경로로 그려지며 partition
  오버레이는 안 나옴 (wxd3/wxd4 와 동일 상태). 필요 시 별도 작업.

---

## 5. 체크리스트

- [ ] d̄ = `max(mean_midpoint, min_j r_j + Σ_j p_last / (m_last × 2))`?
- [ ] `Σ p_last` 가 **전체 job** 합인가 (early group 아님)?
- [ ] machine 수가 `last_stage_mc_count`, 거기에 **×2** 인가?
- [ ] 새 d̄ 가 partition aversion score + 양 그룹 정렬식 **모두**에 적용?
- [ ] partition/정렬식/tie(`>=`→late) 는 wxd2 와 100% 동일한가?
- [ ] sorter `DispatchSeqKey` 에 `"wxd5"` 추가 + `direct` 등록, `ParamSortKey` 불변?
- [ ] 맵 접근 전부 `m[j]` 직접 인덱싱?

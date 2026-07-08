# Plan: wxd3 / wxd4 dispatch priorities

> 작성일: 2026-06-25
> 선행: `plans/20260624/center_penalty_dispatch.md` (cpd family), wxd2
>       (`ffc_ddw_params.py:816-877`), wxd1 (`:776-814`)
> 관련 rule: `wxd2` (현재 paired best), `cpd_*`

---

## 0. 요약

`wxd2` 의 **이진 분할(앞/뒤 group)** 골격을 유지하되, 그룹 내 정렬을
wxd2 의 magic-constant 근사식이 아니라 **실제 가중 penalty (w⁺·T, w⁻·E)** 로
교체한 두 rule 을 추가한다.

- **wxd3**: 그룹 내 penalty 를 **center `d̄`** 에 놓였을 때로 측정.
- **wxd4**: wxd3 와 전부 동일, 단 penalty 측정 기준선을 `d̄` 대신
  앞 group 의 **마지막 stage 완료 추정치**로 대체.

둘 다 **parameterless** — cpd 와 동일하게 literal key 로 sweep 경로에 그대로 등록.

---

## 1. 공통 골격 (wxd2 에서 상속)

1단계 partition (wxd2 와 동일, `d̄` 사용):

```python
d_mid[j] = (d⁻_j + d⁺_j) / 2
d̄       = mean(d_mid)
earliness_aversion[j]  = w⁻_j + (d⁻_j − d̄)
tardiness_aversion[j]  = w⁺_j + (d̄  − d⁺_j)
```

**차이점 (1) — tie-breaking**: 두 aversion 이 같을 때(`==`) wxd2 는 **late** group 으로
보내지만, wxd3/wxd4 는 **early(앞)** group 으로 보낸다.

| rule | early 조건 | late 조건 |
|---|---|---|
| wxd2 | `earliness_aversion <  tardiness_aversion` | `>=` |
| wxd3/wxd4 | `earliness_aversion <= tardiness_aversion` | `>` |

**차이점 (2) — 그룹 내 정렬**: 기준선 `c` 에 놓였을 때의 **가중 penalty** 로 정렬
(weight 곱함, objective 항과 일치):

```python
tp_j(c) = w⁺_j × max(c − d⁺_j, 0)    # 지체 penalty
ep_j(c) = w⁻_j × max(d⁻_j − c, 0)    # 조기 penalty
```

- **early group**: `tp_j(c)` **내림차순** (center 에서 지체 penalty 큰 job 일수록 앞)
  → key `(−tp_j(c), native pos)` 오름차순
- **late group**: `ep_j(c)` **오름차순** (center 에서 조기 penalty 큰 job 일수록 뒤)
  → key `(ep_j(c), native pos)` 오름차순

반환: `sorted(early) ++ sorted(late)`.

> wxd2 와 비교: 정렬 식 `(w⁺−2w⁻+2w_max)(d⁻−d̄)` / `(w⁻−2w⁺+2w_max)(d⁺−d̄)` 의
> magic-constant 가 사라지고, 키가 곧 objective penalty 항이 된다.
> cpd 와 비교: cpd 는 전 job 단일 lexicographic 키; wxd3/4 는 wxd2 의 hard
> partition 을 유지한 채 그룹별로 penalty 정렬 → 별개 rule.

---

## 2. rule 별 기준선 `c`

### wxd3 — `c = d̄`

partition 도 정렬도 모두 `d̄` 사용.

### wxd4 — `c = baseline`

partition 은 `d̄` 로 그대로 하고(앞/뒤 group 확정), 그 후 정렬 기준선만 교체:

```python
baseline = max( min_j r_j + (Σ_{j∈early} p_last_j / m_last),  d̄ )
```

- `r_j` = `get_job_2_p_sum_except_last_stage()` (마지막 stage 도달 release proxy),
  `min_j r_j` = 그 최소값.
- `Σ_{j∈early} p_last_j` = **앞 group** job 들의 **마지막 stage** 처리시간 합
  (`get_job_2_p_map_for_stage(last_stage)`).
- `m_last` = `last_stage_mc_count` (마지막 stage 병렬 기계 수).

→ 앞 group 이 마지막 stage 를 비우는 완료시점 추정. 그룹 내 정렬 penalty 를
center `d̄` 가 아닌 이 현실적 완료창에서 측정한다. (확정: "마지막 stage 기준".)

> 주의: baseline 은 **앞 group 확정 후** 계산 (앞 group 합이 필요). 양 group 모두
> 같은 baseline 으로 penalty 측정 (앞 group 은 tp, 뒤 group 은 ep).

---

## 3. 구현

### 3.1 `parameters/ffc_ddw_params.py`

`get_wxd2_job_sequence` 바로 뒤에 두 메서드 신규. 공통부(partition)가 거의 같지만
KISS/가독성 우선으로 각 메서드 자족 작성 (wxd1/wxd2 도 partition 을 각자 인라인).

```python
def get_wxd3_job_sequence(self) -> list[str]:
    """wxd2 partition (tie→early) + 그룹 내 d̄-center 가중 penalty 정렬."""
    ewt = self._job_2_ewt_map
    twt = self._job_2_twt_map
    ddw = self._job_2_due_window_map
    job_2_pos = {j: pos for pos, j in enumerate(self._job_id_list)}

    d_mid = {j: (ddw[j][0] + ddw[j][1]) / 2 for j in self._job_id_list}
    d_bar = sum(d_mid.values()) / len(d_mid)

    earliness_aversion_score = {
        j: ewt[j] + (ddw[j][0] - d_bar) for j in self._job_id_list
    }
    tardiness_aversion_score = {
        j: twt[j] + (d_bar - ddw[j][1]) for j in self._job_id_list
    }
    early = [
        j for j in self._job_id_list
        if earliness_aversion_score[j] <= tardiness_aversion_score[j]
    ]
    late = [
        j for j in self._job_id_list
        if earliness_aversion_score[j] > tardiness_aversion_score[j]
    ]

    def early_key(j: str) -> tuple[float, int]:
        tp = twt[j] * max(d_bar - ddw[j][1], 0)
        return (-tp, job_2_pos[j])

    def late_key(j: str) -> tuple[float, int]:
        ep = ewt[j] * max(ddw[j][0] - d_bar, 0)
        return (ep, job_2_pos[j])

    return sorted(early, key=early_key) + sorted(late, key=late_key)


def get_wxd4_job_sequence(self) -> list[str]:
    """wxd3 와 동일하나 그룹 내 penalty 를 앞-group 마지막-stage 완료 추정
    baseline = max(min_j r_j + Σ_{early} p_last_j / m_last, d̄) 에서 측정."""
    ewt = self._job_2_ewt_map
    twt = self._job_2_twt_map
    ddw = self._job_2_due_window_map
    job_2_pos = {j: pos for pos, j in enumerate(self._job_id_list)}

    d_mid = {j: (ddw[j][0] + ddw[j][1]) / 2 for j in self._job_id_list}
    d_bar = sum(d_mid.values()) / len(d_mid)

    earliness_aversion_score = {
        j: ewt[j] + (ddw[j][0] - d_bar) for j in self._job_id_list
    }
    tardiness_aversion_score = {
        j: twt[j] + (d_bar - ddw[j][1]) for j in self._job_id_list
    }
    early = [
        j for j in self._job_id_list
        if earliness_aversion_score[j] <= tardiness_aversion_score[j]
    ]
    late = [
        j for j in self._job_id_list
        if earliness_aversion_score[j] > tardiness_aversion_score[j]
    ]

    last_stage_id = self.stage_id_list[-1]
    p_last = self.get_job_2_p_map_for_stage(last_stage_id)
    r_j = self.get_job_2_p_sum_except_last_stage()
    early_p_last_sum = sum(p_last[j] for j in early)
    baseline = max(
        min(r_j.values()) + early_p_last_sum / self.last_stage_mc_count,
        d_bar,
    )

    def early_key(j: str) -> tuple[float, int]:
        tp = twt[j] * max(baseline - ddw[j][1], 0)
        return (-tp, job_2_pos[j])

    def late_key(j: str) -> tuple[float, int]:
        ep = ewt[j] * max(ddw[j][0] - baseline, 0)
        return (ep, job_2_pos[j])

    return sorted(early, key=early_key) + sorted(late, key=late_key)
```

맵 접근은 전부 `m[j]` 직접 인덱싱 (`feedback_no_defensive_get`).

### 3.2 `parameters/sorter.py`

- `DispatchSeqKey` Literal 에 `"wxd3"`, `"wxd4"` 추가.
- `dispatch_seq_job_sequence` 의 `direct` dict 에:
  ```python
  "wxd3": instance.get_wxd3_job_sequence,
  "wxd4": instance.get_wxd4_job_sequence,
  ```
- `ParamSortKey` / `param_sort_job_sequence` 는 **불변** (sweep 은 DispatchSeqKey 만 사용).

### 3.3 검증

- `uv run ruff check` / `uv run ruff format`
- 간단 sanity: 작은 인스턴스에서 두 시퀀스가 전체 job permutation(중복/누락 없음)인지,
  wxd3 와 wxd2 가 tie/penalty 차이만큼만 다른지 확인.

---

## 4. config (선택, 승인 후)

`metadata/20260624/` (또는 신규 20260625) simple/reversed dispatch sweep 에
`sd_wxd3`/`rd_wxd3`, `sd_wxd4`/`rd_wxd4` scenario 추가 + baseline(wxd2) 동반.
cpd plan §4 와 동형. **이번 PR 범위에 포함할지는 승인 시 확정.**

---

## 5. 체크리스트

- [ ] tie(`==`) → early group (wxd2 와 반대) 적용?
- [ ] early=tp 내림차순(`-tp`), late=ep 오름차순(`+ep`)?
- [ ] penalty 가 weight 곱한 `w⁺·T` / `w⁻·E` 인가? (확정)
- [ ] wxd4 baseline 이 마지막 stage 기준 `min r_j + Σ_early p_last/m_last`, `d̄` 와 max?
- [ ] wxd4 partition 은 여전히 `d̄` 기준(baseline 아님)?
- [ ] baseline 계산이 early 확정 *후*인가?
- [ ] sorter `DispatchSeqKey` 2개 추가 + `direct` 등록, `ParamSortKey` 불변?
- [ ] 맵 접근 전부 `m[j]` 직접 인덱싱?

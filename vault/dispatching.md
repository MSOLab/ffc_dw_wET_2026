# Dispatching / Job-Priority Rule Reference

> Branch `20260624_more_init_dispatch` 에서 실험된 모든 job-priority &
> dispatching rule 정리. 정렬 키와 의도를 한곳에 모은 single source of truth.
> 코드: `src/ffc_ddw_sum_et/parameters/ffc_ddw_params.py`,
> 등록: `src/ffc_ddw_sum_et/parameters/sorter.py` (`DispatchSeqKey`).
> 작성일: 2026-06-25

---

## 0. 표기 (symbols)

| 기호 | 코드 | 의미 |
|---|---|---|
| `w⁻_j` | `ewt` (`_job_2_ewt_map`) | earliness weight (조기 가중치) |
| `w⁺_j` | `twt` (`_job_2_twt_map`) | tardiness weight (지체 가중치) |
| `d⁻_j` | `ddw[j][0]` | due window 하한 |
| `d⁺_j` | `ddw[j][1]` | due window 상한 |
| `p_{i,j}` | `job_2_stage_2_p_map` | stage `i` 처리시간 |
| `P_j` | `sum p_{i,j}` | 전 stage 처리시간 합 |
| `p_last_j` | `get_job_2_p_map_for_stage(last)` | 마지막 stage 처리시간 |
| `r_j` | `get_job_2_p_sum_except_last_stage()` | 마지막 stage 제외 합 (release proxy) |
| `d̄` | `mean( (d⁻+d⁺)/2 )` | 전 job midpoint 평균 (center) |
| `d*_j` | `get_job_2_due_date_star_map()` | `(w⁻d⁻ + w⁺d⁺)/(w⁻+w⁺)` = 가중 due |
| pos | `job_2_pos` | native `job_id_list` 위치 (안정 tie-break) |

목적함수는 **weighted E+T** = `Σ_j [ w⁻_j·max(d⁻_j−C_j,0) + w⁺_j·max(C_j−d⁺_j,0) ]`.
모든 rule은 이 목적을 겨냥한 **초기 dispatch 순서**(완료시각 `C_j`를 알기 전 tie-break)를 만든다.
모든 키는 **오름차순 sort**, 마지막 성분은 항상 `pos`(deterministic).

### Decode 방향: simple(`sd_`) vs reverse(`rd_`)

각 priority 시퀀스는 두 방향으로 디코드된다 (analysis: paired/direction-symmetric).

- **`sd_` simple**: 고정 job 순열을 FAM(First-Available-Machine,
  `select_machine_by_earliest_start_then_idle`)으로 forward 디코드 + weighted E+T용
  idle-time insertion(IIT) 보정. Pan et al. (2017) decode와 동일 골격.
- **`rd_` reverse**: 같은 IIT를 공유하되 역방향 후보를 makespan 기준 best로 선택하는
  별도 pipeline. 같은 priority라도 `sd`/`rd` 결과가 다름.

---

## 1. 단순 due/slack rule (Pan et al. 2017 계열)

| key | 정렬 키 (asc) | 의도 |
|---|---|---|
| `edd` (`get_eddub`) | `(d⁺_j, pos)` | EDD = due 상한 빠른 순. 2017 baseline의 EDD. |
| `eddub_twt` | `(d⁺_j, −w⁺_j, pos)` | EDD + 동률 시 지체가중치 큰 job 우선. |
| `lsl` | `(d⁺_j − p_last_j, pos)` | Least Slack(last machine). 2017 baseline LSL. clamp 없음. |
| `osl` | `(d⁺_j − P_j, pos)` | Overall Slack. LSL의 전-stage 일반화. 2017 baseline OSL. |

> 2017 baseline = `(sd) × {edd, lsl, osl}` (forward FAM only). 비교 기준선.

---

## 2. weight × due 복합 lexicographic rule

| key | 정렬 키 (asc) | 의도 |
|---|---|---|
| `weight_due_pos` | `(−max(w⁻,w⁺), −(w⁻+w⁺), d⁺−d⁻, pos)` | 가중치 큰 job·좁은 window 우선. |
| `due_weight_pos` | `(max(0, d⁺−p_last), d⁺, d⁻, −(w⁻+w⁺), pos)` | slack(클램프)→due→가중치. |
| `due2_weight_pos` | `(max(r_j, d⁺−p_last), d⁺, d⁻, −(w⁻+w⁺), pos)` | `due_weight_pos`의 slack을 release proxy `r_j`로 하한. |
| `due_star_weight_pos` | `(d*_j, d⁺, −(w⁻+w⁺), pos)` | 가중 due `d*` = 비대칭 가중치를 반영한 단일 due. |

---

## 3. weight-only rule

| key | 정렬 키 | 의도 |
|---|---|---|
| `w1` | desc `(w⁺_j − w⁻_j)` | tardiness가 earliness보다 비싼 job을 앞으로. `p` 무시. |
| `wspt_twt` | desc `w⁺_j / P_j` | 고전 WSPT. 혼잡(tight·narrow, `T=0.6,R=0.2`)해 대부분 지체될 때 single-machine 최적. `w1`이 무시한 `p`를 복원. |

---

## 4. wxd 계열 — 2-way partition + 그룹 내 정렬

공통 1단계 partition (additive aversion score, `d̄` 사용):

```
earliness_aversion[j] = w⁻_j + (d⁻_j − d̄)     # 클수록 조기 완료를 싫어함
tardiness_aversion[j] = w⁺_j + (d̄  − d⁺_j)     # 클수록 지체 완료를 싫어함
```

`tardiness_aversion`가 큰 쪽 = **early group**(앞으로), 작은 쪽 = **late group**(뒤로).
반환 = `sorted(early) ++ sorted(late)`.

| key | tie(`==`) 처리 | early 그룹 내 정렬 (asc) | late 그룹 내 정렬 (asc) |
|---|---|---|---|
| `wxd1` | midpoint split (`d_mid<d̄`) | `(w⁺−2w⁻+2w_max)(d_mid−d̄)` | `(w⁻−2w⁺+2w_max)(d_mid−d̄)` |
| `wxd2` | tie→late | `(w⁺−2w⁻+2·ew_max)(d⁻−d̄)` | `(w⁻−2w⁺+2·tw_max)(d⁺−d̄)` |
| `wxd3` | tie→early | `−tp_j(d̄)` = `−w⁺·max(d̄−d⁺,0)` | `ep_j(d̄)` = `w⁻·max(d⁻−d̄,0)` |
| `wxd4` | tie→early | `−tp_j(baseline)` | `ep_j(baseline)` |

- `ew_max = max w⁻`, `tw_max = max w⁺`, `w_max = max(ew_max, tw_max)`.
- `wxd1`은 partition을 midpoint 부호로, `wxd2`는 aversion-score 비교로 한다.
- **`wxd4` baseline** = `max( min_j r_j + Σ_{early} p_last_j / m_last , d̄ )`
  (앞 group이 마지막 stage를 비우는 완료시점 추정; partition은 여전히 `d̄` 사용).

### wxd2의 magic constant `2` (핵심)

정렬키 = `C_e · (d⁻−d̄)`, `C_e = w⁺ − 2w⁻ + 2·ew_max = w⁺ + 2(ew_max − w⁻) ≥ 0`.

1. **`+2·ew_max` 는 강제된 최소 부호안정자**: 곱의 부호가 `sign(d⁻−d̄)`로 깨끗이
   결정되려면 `C_e ≥ 0` 이어야 한다. 최악(`w⁻=ew_max, w⁺=0`)에서
   `(k−2)ew_max ≥ 0 ⟺ k ≥ 2`. `k=1`이면 계수가 음수로 뒤집혀 정렬이 역전됨.
2. **`−2w⁻` 는 earliness:tardiness = 2:1 환율**: early group은 partition에서 이미
   "net tardiness-averse"로 선발됐으므로, 그룹 내 순서는 잔여 risk인 earliness가
   지배해야 한다. `2`는 earliness가 *엄격히* 지배하게 하는 최소 정수.
   → 결과: earliness 싼 job을 맨 앞, earliness 비싼 job을 block 뒤(center 근접 =
   block 내 가장 늦게 완료)로 보내는 옳은 정책.
3. **곱셈형 `(d⁻−d̄)`(ungated)가 wxd3/4의 gated penalty를 이긴다**: wxd3/4의
   `max(·,0)` penalty는 window가 `d̄`를 걸치는 dense block에서 0으로 무너져
   순서 정보를 버림. wxd2의 affine surrogate는 연속 rank를 유지.

> 상세 정당화: 본 branch 대화 / `analysis/20260624_dispatch_init_justification_2.md`.
> **현 paired-best single rule = `wxd2`** (analysis v2, m=1).

---

## 5. cpd 계열 — center-penalty 단일 lexicographic (parameterless)

전 job을 하나의 키로 정렬 (`_center_penalty_job_sequence(center)`):

```
ep_j = w⁻_j · max(d⁻_j − center, 0)      # 조기 penalty
tp_j = w⁺_j · max(center − d⁺_j, 0)      # 지체 penalty
key  = (−tp_j, ep_j, d⁺_j, pos)          # 오름차순
```

- **tardiness가 키를 선도**(`−tp` desc): 지체는 비가역이지만 earliness는
  `insert_idle_time`이 회복 가능 → 지체 무거운 job을 무조건 앞으로.
- earliness가 tie-break, 그다음 EDD⁺(`d⁺`)가 central block(`tp=ep=0`, window가
  center를 straddle) 정렬. wxd의 hard partition을 단일 키로 부드럽게 일반화.

| key | center |
|---|---|
| `cpd_mean` | `d̄` = midpoint 평균 (= wxd2의 d̄) |
| `cpd_wmean` | penalty 가중 midpoint 평균 `Σ(w⁻+w⁺)·mid / Σ(w⁻+w⁺)` |
| `cpd_median` | midpoint 중앙값 (outlier-robust) |

---

## 6. 요약 표 (전체 `DispatchSeqKey`)

| 그룹 | keys |
|---|---|
| 2017 단순 slack/due | `edd`, `eddub_twt`, `lsl`, `osl` |
| weight×due 복합 | `weight_due_pos`, `due_weight_pos`, `due2_weight_pos`, `due_star_weight_pos` |
| weight-only | `w1`, `wspt_twt` |
| wxd partition | `wxd1`, `wxd2`, `wxd3`, `wxd4` |
| cpd center-penalty | `cpd_mean`, `cpd_wmean`, `cpd_median` |

각 key는 `sd_`/`rd_` 양방향으로 sweep된다.
`ParamSortKey`(`weight-due-pos`, `due-weight-pos`, `due*-weight-pos`,
`due2-weight-pos`, `wxd1`, `wxd2`)는 heatmap 등에서 쓰이는 별칭 surface로,
`dispatch_seq_job_sequence`가 동일 getter로 위임한다.

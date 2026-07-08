# Plan: center-penalty dispatch — parameterless priority family

> 작성일: 2026-06-24
> 선행 토의: `analysis/20260624_dispatch_init_justification_2.md` (v2 분석),
>            폐기된 `plans/20260624/wxd3_multiplicative_priority_sweep.md` (β-sweep 접근)
> 관련 rule: `wxd2` (현재 k=1 paired best, rpdf 0.9600), `wxd1`, `w1`

---

## 0. 요약

각 job의 **center 기준 tardiness / earliness penalty** 를 lexicographic 정렬 키로
삼아 **parameterless** priority를 만든다. tardiness 가 1차 키(비가역 penalty 라 우선).
center를 여러 방식으로 정의하면 priority가 **family** 로 나오고, dispatch 한 번은
싸므로 멤버를 전부 만들어 paired-oracle로 평가한다.

```python
ep_j(c) = w⁻_j × max(d⁻_j − c, 0)                   # 조기 penalty (가역)
tp_j(c) = w⁺_j × max(c − d⁺_j, 0)                   # 지각 penalty (비가역)
sort key = (−tp_j(c), ep_j(c), d⁺_j, native pos)    # lexicographic 오름차순
```

center set = {c_A(단순평균), c_C(penalty-weighted), c_D(중앙값)}.
이번 라운드는 **given due window만** 사용 (processing time 미사용).

---

## 1. 배경: 왜 이 rule 인가?

### wxd2 의 의도 (design intent)

`wxd2` (`ffc_ddw_params.py:794-855`) 의 출발점은 각 job 이 이렇게 "말한다"고 보는 것:

> "나는 due window 의 **center of mass** 근처에 놓이는 게 이만큼 싫다.
> 그러니 되도록 **앞**(또는 **뒤**)에 배치해 달라."

- **center of mass `d̄`** = 모든 job 의 due window 중간점 `m_j=(d⁻_j+d⁺_j)/2` 의 평균.
  due window 가 몰리는 지점이자, 자원 경합이 가장 심해 penalty 가 터지기 쉬운 지점.
  (R 이 커서 window 가 넓게 퍼질수록 알고리즘 성능이 좋다는 v2 관측이 이 center
  기반 해석을 뒷받침.)
- 각 job 은 이 혼잡한 center 에서 "어느 쪽으로, 얼마나 세게 도망가고 싶은지"를
  점수화한다. 이걸 두 단계로 구현한다.

**1단계 — partition: 어느 쪽으로 도망갈지**

job 이 center `d̄` 에 놓였다고 가정하고 두 "회피 욕구"를 비교한다:

| 욕구 | 점수 | 의미 |
|---|---|---|
| 앞에 놓이기 싫은 정도 (조기 회피) | `w⁻_j + (d⁻_j − d̄)` | `w⁻` 큼=조기 penalty 민감, `d⁻−d̄` 큼=window 하한이 center 보다 뒤 → 앞에 가면 손해 |
| 뒤에 놓이기 싫은 정도 (지체 회피) | `w⁺_j + (d̄ − d⁺_j)` | `w⁺` 큼=지체 penalty 민감, `d̄−d⁺` 큼=window 상한이 center 보다 앞 → 뒤에 가면 손해 |

→ **지체 회피 > 조기 회피 이면 앞(early) 그룹, 아니면 뒤(late) 그룹.**
지각이 더 무서운 job 을 먼저 끝낸다는 직관과 일치.

**2단계 — 그룹 내 정렬: 얼마나 극단으로**

도망갈 방향이 정해지면, "center 에 놓였을 때 입을 penalty 가 큰 job 일수록 더
극단(맨 앞/맨 뒤)으로" 보낸다. 이때 penalty 를 다음 근사식으로 매긴다:

- **앞 그룹**: `d̄` 에 놓였을 때의 **지체 penalty** 가 큰 job 을 더 앞으로
  → `(w⁺_j − 2w⁻_j + 2·max_k w⁻_k) × (d⁻_j − d̄)`
- **뒤 그룹**: `d̄` 에 놓였을 때의 **조기 penalty** 가 큰 job 을 더 뒤로
  → `(w⁻_j − 2w⁺_j + 2·max_k w⁺_k) × (d⁺_j − d̄)`

(`+2·max w` 항은 계수를 양수로 유지하려는 정규화, `−2w∓` 항은 반대쪽 민감도가
높은 job 의 우선순위를 깎으려는 보정.)

### wxd2 의 한계

- **magic constant** (`±2w∓`, `+2·max w`) 의 정당화가 약하다 — 2단계의 penalty
  근사가 objective 항(`w⁻·E`, `w⁺·T`)으로 깔끔히 환원되지 않는다.
- 앞 그룹 정렬에 binding 경계가 아닌 `d⁻` 사용 — 지각 거리는 본래 `d⁺` 기준이어야
  한다 (window 상한을 넘은 만큼이 지각).
- **이진 분할 + 그룹별 별도 키** → 분할 경계에서 우선순위가 불연속이고, "가운데에
  만족하는 job"(window 가 `d̄` 를 걸치는 job)을 억지로 한쪽으로 보낸다.

### center-penalty dispatch 의 아이디어

partition + magic-constant 정렬을 **단일 lexicographic 키**로 녹인다:

> job 의 완료가 center `c` 에 놓인다고 가정했을 때 **실제로 발생하는 가중 penalty**
> 로 정렬한다. 비가역 penalty 인 tardiness(`tp`)를 1차 키로 두어 지각 위험이 큰
> job 을 무조건 앞에 두고, 가역 penalty 인 earliness(`ep`)는 tie-break 로만 쓴다.

- **parameterless**: β/τ/η 같은 튜닝 인자 없음. center 정의만 바꿔 family 생성.
- **magic constant 없음**: 키가 곧 objective 항 (`w⁻·E`, `w⁺·T`).
- **가역/비가역 비대칭 반영**: `insert_idle_time` 이 회수 가능한 earliness 보다
  회수 불가능한 tardiness 를 우선 (lexicographic 1차 키).
- **wxd2 의 일반화**: 이진 분할이 `sign(ep−tp)` 로, 그룹별 키가 연속 키로 통합.

---

## 2. 수식 상세

### 2.1 정렬 키 (모든 center 공통)

given window `[d⁻_j, d⁺_j]`, 가중치 `w⁻_j`(earliness), `w⁺_j`(tardiness), center `c`:

```python
ep_j(c) = w⁻_j × max(d⁻_j − c, 0)    # c 가 window 아래 → 조기 penalty
tp_j(c) = w⁺_j × max(c − d⁺_j, 0)    # c 가 window 위  → 지각 penalty
```

정렬: **lexicographic 오름차순** `(−tp_j(c), ep_j(c), d⁺_j, native position)`.

**왜 ep−tp 선형결합이 아니라 lexicographic 인가:**
tardiness 는 dispatch 가 늦어지면 `insert_idle_time` 으로도 회수 불가능한 **비가역**
penalty 인 반면, earliness 는 start 를 늦춰 회수 가능한 **가역** penalty 다. 따라서
지각 위험이 조기 위험에 의해 순위가 뒤집히면 안 된다. 선형결합 `ep−tp` 는 조기
penalty 가 큰 job 이 지각 penalty 가 큰 job 을 앞지를 수 있으므로(예: `tp=10,ep=8`
→ −2 가 `tp=3,ep=0` → −3 보다 뒤), tardiness 를 **1차 키**로 두어 무조건 우선한다.

방향 검증:
- 1차 `−tp_j` 오름차순 ⟺ `tp_j` 내림차순 → center 에서 지각 penalty 가 큰 job 일수록
  **맨 앞** (가장 급히 끝내야 하는, 비가역 위험이 큰 job 먼저)
- `tp_j=0` 인 job 들끼리는 2차 `ep_j` 오름차순:
  - `ep_j=0` (c 가 window 안, 가운데 만족) → 지각 블록 바로 뒤 = **중앙 블록**
  - `ep_j>0` (c 가 window 보다 앞, 더 늦게 끝나고 싶음) → ep 클수록 **뒤**
- 3차 `d⁺_j` (EDD⁺): **중앙 블록**(`tp=ep=0`, window 가 center 를 걸치는 job)을 due
  상한 오름차순으로 정렬 — 가운데에서는 마감이 이른 job 을 먼저. (이 tie-break 의
  존재 이유가 곧 중앙 블록.)
- 최종 동점은 native position 으로 안정 정렬

### 2.2 processing-time 구조적 지각 job 은 키가 자동 처리

`Σ_i p_ij ≥ d⁺_j` 인 job(upper due 전 완료 불가 → 무조건 지각)은 `d⁺_j` 가 작아
center 보다 한참 뒤 → `tp_j` 큼 → 1차 키 `−tp_j` 가 작아 **자동으로 맨 앞**,
자기들끼리도 `tp_j = w⁺_j(c−d⁺_j)` 내림차순으로 정렬됨. **명시적 grouping 불필요.**
(이번 라운드는 effective window·start-space 등 processing-time 보정을 도입하지 않음.)

### 2.3 center set

`m_j = (d⁻_j + d⁺_j) / 2` 일 때:

| key | center 정의 | 성격 |
|---|---|---|
| `cpd_mean`   | `c_A = mean(m_j)` (= wxd2 의 d̄) | 모든 job 균등 1표 |
| `cpd_wmean`  | `c_C = Σ_j (w⁻_j+w⁺_j)·m_j / Σ_j (w⁻_j+w⁺_j)` | penalty-weighted, 비싼 job 쪽으로 끌림 |
| `cpd_median` | `c_D = median(m_j)` | outlier robust |

`c_C` 직관: 경합 비용은 job 마다 다르다. 고-weight job 이 몰린 구간이 objective
비용이 터지는 곳이므로, center 를 비싼 job 쪽으로 옮겨 앞/뒤 분할이 비용 큰 구간에
민감해지게 한다.

---

## 3. 구현 설계

### 3.1 변경 범위

| 파일 | 변경 내용 |
|---|---|
| `parameters/ffc_ddw_params.py` | `get_cpd_mean_job_sequence`, `get_cpd_wmean_job_sequence`, `get_cpd_median_job_sequence` 신규 (전부 parameterless) |
| `parameters/sorter.py` | `DispatchSeqKey` literal 에 `"cpd_mean"`, `"cpd_wmean"`, `"cpd_median"` 추가 + `dispatch_seq_job_sequence` 의 `direct` 매핑 등록 |
| `metadata/20260624/` | simple / reversed dispatch sweep config 에 3개 scenario 추가 |

**핵심: parameterless 라서 kwarg threading 불필요.** 기존
`initialize_by_simple_dispatch(sequence)` / `initialize_by_reversed_dispatch(sequence)`
+ `dispatch_seq_job_sequence` 경로에 literal key 로 그대로 등록된다. (폐기된 wxd3
plan 이 β kwarg 를 dispatcher 까지 흘려야 했던 부담이 전부 사라짐.)

### 3.2 공통 헬퍼 + getter (DRY)

세 getter 가 center 계산만 다르고 정렬 키는 동일하므로, 내부 정렬 로직을 private
헬퍼로 공유한다:

```python
def _center_penalty_job_sequence(self, center: float) -> list[str]:
    ewt = self._job_2_ewt_map        # w⁻
    twt = self._job_2_twt_map        # w⁺
    ddw = self._job_2_due_window_map # (d⁻, d⁺)
    job_2_pos = {j: pos for pos, j in enumerate(self._job_id_list)}

    def key(j: str) -> tuple[float, float, int, int]:
        ep = ewt[j] * max(ddw[j][0] - center, 0)
        tp = twt[j] * max(center - ddw[j][1], 0)
        return (-tp, ep, ddw[j][1], job_2_pos[j])  # tardiness(비가역) 1차, ep tie, EDD⁺(중앙블록), pos

    return sorted(self._job_id_list, key=key)

def get_cpd_mean_job_sequence(self) -> list[str]:
    """center = mean of midpoints (= wxd2's d̄)."""
    ddw = self._job_2_due_window_map
    mids = [(ddw[j][0] + ddw[j][1]) / 2 for j in self._job_id_list]
    return self._center_penalty_job_sequence(sum(mids) / len(mids))

def get_cpd_wmean_job_sequence(self) -> list[str]:
    """center = penalty-weight-weighted mean of midpoints."""
    ewt, twt, ddw = self._job_2_ewt_map, self._job_2_twt_map, self._job_2_due_window_map
    num = sum((ewt[j] + twt[j]) * (ddw[j][0] + ddw[j][1]) / 2 for j in self._job_id_list)
    den = sum(ewt[j] + twt[j] for j in self._job_id_list)
    return self._center_penalty_job_sequence(num / den)

def get_cpd_median_job_sequence(self) -> list[str]:
    """center = median of midpoints (outlier-robust)."""
    import statistics
    ddw = self._job_2_due_window_map
    mids = [(ddw[j][0] + ddw[j][1]) / 2 for j in self._job_id_list]
    return self._center_penalty_job_sequence(statistics.median(mids))
```

(`statistics` import 는 모듈 상단으로 끌어올림. 맵 접근은
`feedback_no_defensive_get` 에 따라 `m[j]` 직접 인덱싱.)

### 3.3 sorter.py 등록

```python
DispatchSeqKey = Literal[
    ...,
    "cpd_mean",
    "cpd_wmean",
    "cpd_median",
]
```

`dispatch_seq_job_sequence` 의 `direct` dict 에:

```python
"cpd_mean":   instance.get_cpd_mean_job_sequence,
"cpd_wmean":  instance.get_cpd_wmean_job_sequence,
"cpd_median": instance.get_cpd_median_job_sequence,
```

> 주의: `ParamSortKey` / `param_sort_job_sequence` 는 **건드리지 않는다**.
> sweep 경로는 `DispatchSeqKey` 만 사용 (controller 의 simple/reversed dispatch).

### 3.4 decode 방향

getter 하나가 forward priority(list[str]) 를 반환하고, 두 decode 모두 같은 getter 를
소비한다:
- `initialize_by_simple_dispatch(sequence)` → forward job-centric decode (`sd_*`)
- `initialize_by_reversed_dispatch(sequence)` → reverse-instance + IIT pipeline (`rd_*`)

→ 멤버당 sd/rd 두 방향을 모두 돌려 "택1" 한다 (v2 의 paired 평가와 동일).

---

## 4. 실험 설계

### 4.1 config

기존 `metadata/20260624/simple_dispatch_sequence_sweep_config.yaml` 과
`reversed_dispatch_sequence_sweep_config.yaml` 에 baseline 과 함께 3개 멤버 추가.
(먼저 10-instance smoke subset 으로 검증 후 FULL_RUN.)

simple-dispatch scenario 예:

```yaml
  - name: sd_cpd_mean
    timelimit: "0.09nc"
    output_subdir: sd_cpd_mean
    subroutine_flow: [{ method: initialize_by_simple_dispatch, sequence: cpd_mean }]
  - name: sd_cpd_wmean
    timelimit: "0.09nc"
    output_subdir: sd_cpd_wmean
    subroutine_flow: [{ method: initialize_by_simple_dispatch, sequence: cpd_wmean }]
  - name: sd_cpd_median
    timelimit: "0.09nc"
    output_subdir: sd_cpd_median
    subroutine_flow: [{ method: initialize_by_simple_dispatch, sequence: cpd_median }]
```

reversed-dispatch 는 `method: initialize_by_reversed_dispatch`, `name: rd_cpd_*` 로 동형.

baseline (반드시 동일 run 에 포함): `sd_wxd2`/`rd_wxd2` (현재 best), `sd_wxd1`,
`sd_w1`, `sd_edd` — 별도 scenario 로 둔다 (sweep 끝점에 의존하지 않음).

### 4.2 분석 지표 (v2 방법론 정합)

폐기된 plan 의 "standalone min-mean" 대신, v2 와 동일하게 **set 기여** 를 primary 로:

1. **k=1 paired oracle** (`--unit priority --combo-size 1`): 각 멤버의 (sd,rd)
   oracle-mean rpdf 가 **wxd2 의 0.9600 을 이기는가?**
2. **set 대체**: `{edd, w1, cpd_*}` 가 채택안 `{edd, w1, wxd2}` 를 대체/개선하는가?
   (rpdf primary, obj secondary)
3. **single-method ranking** (`--unit scenario`): 보조 지표 (size-dominated bias 확인)

분석 커맨드:

```bash
RUN=output/<run>/
uv run python scripts/analyze_dispatch_sweep.py $RUN --metric rpdf
uv run python scripts/analyze_dispatch_sweep.py $RUN --metric rpdf --unit priority --combo-size 1
```

### 4.3 expected outcomes

| 멤버 | 예상 | 근거 |
|---|---|---|
| `cpd_mean` | wxd2 와 근접 (≈0.96) | 같은 center `d̄`, magic-constant 만 objective 항으로 교체 |
| `cpd_wmean` | mean 대비 개선 가능 | center 가 고-weight 구간으로 이동 → 비싼 job 우선 배치 |
| `cpd_median` | outlier 많은 instance 에서 mean 보다 안정 | robust center |

핵심 질문: parameterless 연속 키가 wxd2 의 magic-constant 분할을 **유지하거나
능가** 하는가? `cpd_wmean` 이 wxd2 를 넘으면 family 채택, 아니어도 `{edd,w1,wxd2}`
set 에 complementary 하게 들어가는지 확인.

---

## 5. 구현 순서

1. `ffc_ddw_params.py`: `_center_penalty_job_sequence` 헬퍼 + 3 getter, `statistics` import
2. `sorter.py`: `DispatchSeqKey` 3개 키 추가 + `direct` 매핑
3. `uv run ruff check` / `uv run ruff format`
4. config: simple/reversed sweep 에 `*_cpd_*` scenario 추가 (먼저 smoke subset)
5. smoke run → `cpd_mean` 이 wxd2 와 같은 center 인데 결과 합리적인지 sanity 확인
6. FULL_RUN (sd + rd × 3 멤버 + baseline)
7. 분석: k=1 paired oracle, set 대체 비교
8. 채택 여부 확정 → 논문 문장

---

## 6. 검토 체크리스트

- [ ] 키 방향: lexicographic `(−tp, ep, d⁺, pos)` → 지각-penalty 큰 job 이 1차로 앞에 가는가?
- [ ] tie-break: `tp=0` job 들이 `ep` 오름차순(가운데→뒤), 중앙 블록(`tp=ep=0`)이 `d⁺` 오름차순(EDD⁺)인가?
- [ ] 구조적 지각 job(`Σp ≥ d⁺`)이 명시 분기 없이 자동 front 인가? (단위 테스트)
- [ ] `cpd_mean` 의 center 가 wxd2 의 `d̄` 와 **동일** 한가? (center 계산 일치 확인)
- [ ] `cpd_wmean` 분모 `Σ(w⁻+w⁺) > 0` 보장 (weight 양수 → 항상 성립, 단 확인)
- [ ] `cpd_median` 짝수 n 처리 (`statistics.median` = 중앙 두 값 평균)
- [ ] 맵 접근이 전부 `m[j]` 직접 인덱싱인가? (`feedback_no_defensive_get`)
- [ ] sweep 경로가 `DispatchSeqKey` 만 쓰고 `ParamSortKey` 는 불변인가?
- [ ] simple/reversed 두 config 모두에 멤버 + baseline(wxd2) 포함?

---

## 7. 향후 확장 (YAGNI, 이번 라운드 제외)

- **processing time 보정**: effective due window `[max(d⁻, Σp), d⁺]` 또는
  start-space shift (`−Σp`). 이번엔 decode 방향(reverse/simple) 택1 로 대체.
  긴-빡빡 job 의 완료공간 vs 시작공간 비대칭은 별도 라운드에서 검토.
- **center 후보 확장**: `mean(d⁻)`, `mean(d⁺)`, `d*` 평균 등.
- 둘 다 당장 필요 시점 아님 — fixed 3-멤버 family 로 먼저 평가.

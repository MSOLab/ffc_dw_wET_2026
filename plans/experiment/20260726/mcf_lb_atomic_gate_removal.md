# MCF-LB 스텝의 stop gate 제거 후 CSR crossover ladder 재측정 (사전 작성)

**작성일**: 2026-07-26 · **종류**: 코드 변경 + 실험 실행 계획(사전 작성)
**상태**: 코드 변경·테스트 완료 · 재실행 진행 중 — §4 판정 미완.
**발단**: `plans/analysis/20260726/coarsening_short_budget_crossover.md` §4.4
**재측정 대상 런 (before)**: `output/20260725_crossover_ladder/20260726T002619_971440`
**재실행 런 (after)**: `output/20260725_crossover_ladder/20260726T173841_347539` (2026-07-26 17:38:41 시작)
**config (재사용)**: `metadata/20260725/coarsening_crossover.yaml`

> ⚠️ **두 런이 같은 base dir을 공유한다.** config를 무수정 재사용했으므로
> `output_dir`도 그대로다 — before/after는 **timestamp로만** 구분된다. 이 문서와
> 후속 분석에서 런을 지칭할 때 반드시 timestamp까지 적는다.

> ⚠️ 이 문서는 **버그 수정 + 그 수정이 결론을 바꾸는지 확인하는 재실행**을 다룬다.
> 성능 개선이나 알고리즘 변경이 아니다. 성공 기준은 "더 좋아지는 것"이 아니라
> **"모든 인스턴스에서 해가 나오고, 그 조건에서 결론을 다시 읽는 것"**이다.

---

## 1. 문제

`calc_mcf_lb_and_derive_full_sch`는 incumbent를 만들어 내는 **생산자** 스텝인데,
현재 구현은 예산이 초과되면 **해를 하나도 남기지 않고 끝날 수 있다.**

20260726 crossover ladder 런에서 `m1_k1_f01`은 160개 중 **20개**
((n=150,c=5) 10개 + (n=200,c=5) 10개)에서 `obj_value: null`로 끝났다.

### 1.1 메커니즘 — 예산 부족이 아니라 게이트 위치

라운드 1(`src/ffc_ddw_sum_et/algorithm/mcf_lb/mcf_lb_pipeline.py:253-313`)은
3개 스테이지와 그 사이의 3개 게이트로 이루어져 있고, **각 스테이지는 시작하면
중단할 수 없다**:

```
_stop_check()                                 ← 게이트 1 (253행, 진입)
apply_lb_by_mcf(...)                          ← MCF LP. 중단 불가
_stop_check()                                 ← 게이트 2 (270행)
heuristic_last_stage_only_from_mcf_lb(...)    ← stop_predicate 안 받음
_stop_check()                                 ← 게이트 3 (287행)
build_full_sch_from_last_stage_only_sch(...)  ← dispatch. stop_predicate 안 받음
```

MCF LP의 계약이 코드에 명시돼 있다
(`src/ffc_ddw_sum_et/algorithm/mcf_lb/lb_last_stage_pmtn.py:81-84`):

> `stop_predicate`: Checked **once before `mcf.solve()`** ... **The MCF LP itself
> is not interruptible mid-solve**, so post-solve termination is left to the caller.

**MCF-LB에는 시간제한을 걸 수단이 없다.** 그런데 게이트 2·3은 마치 걸 수 있는
것처럼 중간에서 컷을 시도하고, 그 결과가 "해 없음"이다.

`src/ffc_ddw_sum_et/orchestration/controller.py:1400-1403`:

```python
if result.r1_build_full is None:
    c_diag.elapsed_sec = time.monotonic() - start_elapsed
    return self._make_stop_report(start_elapsed)   # ← register 없음
```

### 1.2 실측 — 해가 나온 경우도 전부 예산을 초과한다

| scenario | n,c | child budget | 실제 elapsed | 결과 |
|---|---|---|---|---|
| m1_k1 f=1% | 150,5 | 0.675s | 0.78s | 해 없음 |
| m1_k1 f=1% | 200,5 | 0.900s | 1.44s | 해 없음 |
| m1_k1 f=2% | 150,5 | 1.350s | 1.88s | **해 있음** |
| m1_k1 f=2% | 200,5 | 1.800s | 3.53s | **해 있음** |
| m1_k1 f=3% | 200,5 | 2.700s | 3.47s | **해 있음** |

예산은 어차피 강제되지 않으므로, 해가 나오냐 마느냐는 **데드라인이 어느 게이트에
걸렸는가**로만 갈린다. 게이트 3에서 버려지는 `heuristic.schedule`은 이미
last-stage-only 스케줄을 손에 쥔 상태이고, `build_full`은 그것을 역방향
dispatch로 펼치는 마지막 한 스텝이다. **이미 초과한 예산을 조금 더 아끼려다
결과를 통째로 0으로 만든다.**

### 1.3 요구되는 동작

> `calc_mcf_lb_and_derive_full_sch`는 **시작했으면 반드시 해를 내고 끝난다.**
> MCF-LB에 시간제한을 걸 방법이 없으므로, 라운드 1은 **원자적(atomic)**이어야 한다.

---

## 2. 코드 변경

### 2.1 범위

| 대상 | 변경 |
|---|---|
| `mcf_lb_pipeline.py` 270행 (게이트 2) | **제거** |
| `mcf_lb_pipeline.py` 287행 (게이트 3) | **제거** |
| `mcf_lb_pipeline.py` 262행 `apply_lb_by_mcf(stop_predicate=...)` | `None` 전달 |
| `mcf_lb_pipeline.py` 253행 (게이트 1, 진입) | **유지** — 아래 근거 |
| 라운드 2 게이트 (436/484/492/525행) | **유지** |
| 복합 orchestrator 게이트 (750/803행) | **유지** |

**게이트 1을 남기는 이유**: 이것만이 "아직 아무것도 시작하지 않았다"를 뜻하는
정당한 컷이다. 라운드 1을 원자적으로 만든다는 것은 *시작 여부만 판단하고,
시작하면 끝까지 간다*는 뜻이지 *시간을 아예 보지 않는다*는 뜻이 아니다. 현재
config들에서 mcf_lb는 flow의 1번 스텝이고 child 타이머가 새로 시작하므로 게이트
1은 실제로 발화하지 않는다(발화했다면 elapsed가 LP 시간보다 짧았을 것). 다만
"incumbent가 하나도 없는데 게이트 1이 발화하면 여전히 해가 0"이라는 잔여
리스크는 남으므로 §5에 확인 항목으로 둔다.

**라운드 2 게이트를 남기는 이유**: r2는 r1 결과를 **개선**하는 라운드이고, r1이
이미 등록을 마친 뒤다. r2가 잘려도 r1 해가 남으므로 "해가 없다" 문제와 무관하다.

### 2.2 TDD

Red → Green 순서로 간다. 먼저 **실패하는 테스트**를 쓴다.

1. **Red**: `stop_predicate`가 항상 `True`를 반환하는 상태로
   `calc_mcf_lb_r1_and_derive_full_sch`를 호출하면, 현재 구현은
   `build_full is None`을 반환한다. 기대: `build_full is not None` 이고
   `build_full.schedule is not None`. → 실패 확인.
2. **Green**: 게이트 2·3 제거, `apply_lb_by_mcf(stop_predicate=None)`.
3. **회귀**: 게이트 1이 발화하는 경우(진입 시 이미 stop) 여전히
   `apply is None` + `stop_reason="stop_guard"`로 반환하는지.
4. **회귀**: 라운드 2는 stop 시 여전히 스킵되고(`r2_skip_reason="stop_guard"`)
   r1 해가 `best_schedule`로 남는지.

기존 테스트는 `tests/algorithm/mcf_lb/test_mcf_lb_pipeline.py`(+ 인접
`test_time_factor.py`)에 있고, CSR child flow 쪽은
`tests/orchestration/test_csr_solve_flow.py`다. 새 케이스는 전자에 넣고,
"child flow가 후보 0개로 끝나지 않는다"는 통합 성격의 확인은 후자에 넣는다.

### 2.3 부수 효과 (사전 인지)

게이트 제거로 mcf_lb는 **항상 완주**하므로 child budget을 더 소모한다. 짧은 f에서
downstream(flip / neh / isw)에 남는 예산이 줄어들며, **K=1이 더 큰 영향을 받는다**
(coarsened 인스턴스의 LP가 더 빠르므로). 즉 이 수정은 K=1에 유리하기만 한 변경이
아니다 — 그래서 재실행이 필요하다.

---

## 3. 재실행

### 3.1 원칙: 동일 config, 동일 grid

`metadata/20260725/coarsening_crossover.yaml`을 **그대로** 재사용한다
(210 scenarios × 160 instances). 재생성하지 않는다 — config가 바뀌면 원인 분리가
불가능해진다.

```bash
# 코드 변경 + 테스트 통과 후
uv run python main.py --config metadata/20260725/coarsening_crossover.yaml
uv run ruff check
```

**예상 소요**: calop4에서 약 3.5시간 (원 런과 동일 규모).

### 3.2 arm별 기대 — a/b가 negative control이다

| arm | mcf_lb 호출 | 예산 binding | CP 사용 | 기대 |
|---|---|---|---|---|
| `a` | **안 함** (dispatch-only, `solve: false`) | — | 없음 | **bit-identical** |
| `b` | 함 | 안 됨 (`0.09nc` 캡, 실측 2.06s@150,5 / 3.73s@200,5 ≪ 캡) | 없음 | **bit-identical** |
| `c` | 함 | 안 됨 (동일) | flip CP (8스레드) | CP 노이즈 이내 |
| `m1` | 함 | **됨** (`0.0009fnc`) | 전체 flow | **변화 대상** |

이 설계의 값어치: `a`는 코드 변경이 mcf_lb 밖으로 새지 않았음을, `b`는 게이트가
원래 발화한 적 없는 곳에서 아무것도 바뀌지 않았음을 각각 증명한다. **둘 중 하나라도
달라지면 변경 범위가 의도보다 넓다는 뜻이므로 즉시 중단하고 원인부터 찾는다.**

`c`는 CP 비결정성 때문에 bit-identical을 요구할 수 없다 — 기존에 정립된 노이즈
바닥(1440 그리드에서 mean obj ±350) 기준으로 판정한다.

---

## 4. 판정 기준

### 4.1 1차 게이트 (버그가 고쳐졌는가)

- **G1 필수**: `m1_k1_f01`이 **160/160** 해를 낸다. `obj_value: null`이 런 전체에서
  0건이다.
  ```bash
  uv run python scripts/20260726/analyze_crossover_ladder.py <new_run_dir>
  # "feasibility asymmetry" 블록이 "none" 이어야 한다
  ```
- **G2 필수**: arm `a`·`b`의 `bestObj`가 원 런과 **전 인스턴스 일치**.
- **G3**: arm `c`의 mean obj 차이가 노이즈 바닥 이내.

G1이 실패하면 게이트 1이 발화했거나 다른 경로가 있다는 뜻이다 (§5 참조).
G2가 실패하면 **재실행 결과를 해석하지 말고** 코드 변경부터 다시 본다.

### 4.2 2차 (결론이 바뀌었는가) — m1 arm

원 런의 결론은 두 축이었다. 각각 다시 읽는다.

1. **목적함수 crossover 부재** — 200개 (arm, f, k, mode) 조합 전부에서
   dRPDf > 0 AND win < loss 였다. 재실행 후에도 성립하는가?
   - 성립 → 원 결론이 버그와 무관하게 유효함이 확정된다.
   - 불성립 → **어느 (f, k, mode)에서 뒤집혔는지**가 새로운 발견이다.
     f=1~2%에서 K=1이 mcf_lb 완주에 예산을 더 쓰게 되므로 이쪽이 후보다.
2. **f=1% feasibility crossover 소멸** — §4.4가 알고리즘이 아니라 결함의 기록이라는
   해석이 맞다면, K≥4의 "20/20 승리"는 사라져야 한다.

### 4.3 부수적으로 반드시 볼 것

- **m1 K=1의 depth 이동**: mcf_lb가 항상 완주하면 winner_source가 step-1에
  더 몰릴 수 있다. `scripts/20260725/analyze_csr_winner_source.py`로 원 런과
  나란히 비교한다. 깊이가 얕아졌는데 RPDf가 좋아졌다면 "깊이 ≠ 품질"의
  추가 증거다.
- **m1 elapsed의 예산 초과폭**: 게이트 제거로 초과가 커진다. `0.0009fnc` 대비
  실제 elapsed 비율을 f별로 기록한다 — 예산 설계 자체를 다시 볼 근거가 된다.

---

## 5. 미결 / 확인 필요

- **게이트 1 잔여 리스크**: incumbent가 하나도 없는 상태에서 게이트 1이 발화하면
  여전히 해가 0이다. 현 config에선 발화하지 않을 것으로 보이나, G1이 실패하면
  이 지점을 먼저 의심한다. 필요하면 "incumbent가 없으면 게이트 1도 무시"로
  좁혀서 수정한다 (그 경우 §2.1 표를 갱신).
- **다른 스텝에도 같은 패턴이 있는가**: `_make_stop_report`를 register 없이
  반환하는 다른 생산자 스텝이 있는지 훑는다
  (`rg -n "_make_stop_report" src/ffc_ddw_sum_et/orchestration/controller.py`
  → 603, 1402, 1956, 2180, 2343, 2766행). 소비자 스텝(incumbent를 받아 개선하는
  쪽)은 문제없지만, **생산자 스텝이 더 있다면 같은 결함**이다. 이번 수정 범위에는
  넣지 않되 결과를 기록한다.
- **과거 실험의 파급 범위**: child budget이 mcf_lb 완주 시간에 근접한 모든
  짧은-budget 실험이 같은 컷에 노출됐을 수 있다 (20260714 budget sweep의 f=5%,
  20260724 f=5% 등). 본 재실행 결과를 보고 어디까지 재측정할지 판단한다 —
  **선제적으로 다 돌리지 않는다.**
- **결론이 바뀌면 문서 처리**: `plans/analysis/20260726/...`는 이미 §4.4에 결함
  해석을 담고 있다. 재실행 결과는 **새 analysis 문서**로 쓰고 원 문서는 남긴다
  (버그 있는 상태의 관측 기록으로서 가치가 있다).

---

## 6. 산출물

- 코드: `mcf_lb_pipeline.py` 변경 + 테스트 — **완료** (라운드 1 원자화, §2.1 표대로)
- 런: `output/20260725_crossover_ladder/20260726T173841_347539` (run setting 커밋) —
  **진행 중**. §6 초안은 `output/<date>_mcf_lb_atomic/`을 예정했으나, config를
  무수정 재사용하기로 한 §3.1 원칙에 따라 `output_dir`도 원 런과 동일하게 두었다.
- 분석: 재실행이 결론을 바꾼 경우에만 `plans/analysis/<date>/` 문서 신설,
  아니면 본 계획서 하단에 "재실행 결과: 결론 불변" 한 줄로 마감

## 7. 중간 관측 (재실행 진행 중)

- **G1 잠정 통과**: 중단된 선행 런(`20260726T171938_069293`, 동일 코드·동일 config,
  약 20% 진행 후 중단)에서 `m1_k1_f01`이 **160/160 완주**했고 `obj_value: null`이
  **0건**이었다. 원 런의 결측 20개가 사라진 것이 확인된다. 정식 판정은
  `20260726T173841_347539` 완주 후 §4.1 커맨드로 다시 낸다.
- **§2.2 잔여 항목**: `tests/orchestration/test_csr_solve_flow.py`의 "child flow가
  `candidates=0`으로 끝나지 않는다" 통합 테스트는 아직 미작성이다.
- **Red 테스트의 겨냥점**: `test_stop_after_r1_entry_still_produces_full_schedule`은
  stop_predicate의 **첫 호출(= 진입 게이트)만 통과시키고 이후 전부 발화**시킨다.
  §1.3의 "진입 게이트를 통과했으면 반드시 해를 낸다"를 그대로 검증하며, 게이트
  개수에 의존하지 않는다. 초안은 `call_count >= 3`으로 3번째 호출을 겨냥했는데,
  이는 게이트 제거 **전** 코드에서만 게이트 3이었고(호출 1·2·3 = 진입/post-MCF/
  post-heuristic) 제거 후에는 복합 게이트(`mcf_lb_pipeline.py:799`)로 옮겨갔다 —
  게이트를 하나 넣고 빼는 것만으로 테스트가 조용히 다른 지점을 보게 되는 결합이라
  순서 독립적으로 바꿨다.

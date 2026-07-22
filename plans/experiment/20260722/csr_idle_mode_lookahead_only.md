# `idle_mode` 완전 제거 — idle-time insertion을 lookahead 단일 규칙으로

**작성일**: 2026-07-22 · **종류**: 코드 변경 계획 + 실행 결과 · **최종본**
**관련 TODO**: `TODO.md` §"Drop the `idle_mode` knob and hardcode `\"lookahead\"`"
— 본 작업으로 **해당 항목의 `idle_mode` 부분은 종료**된다. 남는 것은 `K == 1`
heuristic 자체를 없애는 별개 과제뿐이다(§8).

> 이 문서는 세 차례 범위 조정을 거쳤다. 최종 결론만 필요하면 §1·§4·§7을 읽으면
> 된다. 왜 처음부터 최종 범위가 아니었는지는 §9(개정 이력)에 남긴다.

---

## 1. 목표 (최종)

**`idle_mode`를 파라미터로 받는 함수가 코드베이스에 하나도 남지 않게 한다.**
idle-time insertion의 규칙은 lookahead 하나뿐이며, 선택지가 아니다.

- `CoarsenSolveReconstructOption.idle_mode` / `SwCpOption.idle_mode` 삭제
- controller step 3개(`coarsen_solve_reconstruct`, `sw_cp`,
  `incremental_sw_cp`)의 파라미터 삭제
- `dispatcher/paired.py` 빌더 4개의 통과 파라미터 삭제
- **`FFcSchedule.insert_idle_time`의 파라미터 삭제 + flooring/ceiling 분기 삭제**
  (`K == 1` 경로에 lookahead 규칙만 남김)
- YAML에 `idle_mode` 키가 남아 있으면 **run 시작 전 preflight에서 `ValueError`**
  (값이 `lookahead`여도 키 자체를 거부)

## 2. 근거

### 2.1 `K > 1`에서 세 모드는 이미 완전히 동일

`9b7ad2a`(coarse-exact `insert_idle_time`) 이후 `if K > 1:` 분기가 세 heuristic을
전부 우회한다(`continue`). TODO.md 2026-07-19 status의 결정론적 1440-instance
재도출:

- `factor ∈ {2,4,8,16}` 전 구간에서 세 모드의 `coarse_obj`가 **1440/1440 동일**
- lookahead는 새 exact gate와 **byte-identical (`max|diff| = 0`)**

### 2.2 `K == 1`에서 lookahead는 flooring을 지배한다

`K == 1`에서 두 규칙의 차이는 tie-break뿐이다. 후보는
`Δ_a = min(Δ₁, Δ₂)`(flooring이 고르는 값)와 `Δ_b = Δ_a + 1`이고,
`block_obj(Δ_b) <= block_obj(Δ_a)`일 때만 큰 쪽을 택한다 ⇒ **그 블록의 목적함수
기여는 flooring 대비 절대 나빠지지 않는다**(동률일 때만 오른쪽으로 민다).
20260702 전수 실험(`docs/reviews/20260702_csr_idle_modes.md`)에서도 coarse
objective 기준 lookahead가 5760/5760 지배(위반 0건)였다.

블록 간 상호작용(`Δ₂` 병합)과 downstream CP 전파까지 포함한 총합은 증명 대상이
아니므로 §7에서 측정했다.

### 2.3 flooring은 "선택"된 적이 없다

2026-07-13 이후 작성된 모든 config가 `idle_mode: lookahead`를 명시한다.
flooring/ceiling을 쓴 config는 3-mode 비교 실험용
`metadata/20260702/csr_idle_modes_v4_config.yaml` 하나뿐이다.

나머지 호출부가 flooring이었던 것은 **기본값을 지정하지 않아 생긴 사고**이지
설계 결정이 아니다. 실제로 `metadata/20260722/csr_b30_vs_a_v1_v2_pp.yaml`을
파싱해 보면 한 시나리오 안에서:

| step | `idle_mode` | 개수 |
|---|---|---|
| `coarsen_solve_reconstruct` (top) | `lookahead` 명시 | 2 |
| `incremental_sw_cp` (CSR `solve_flow` 내부) | `lookahead` 명시 | 2 |
| **`incremental_sw_cp` (top-level)** | **미지정 → flooring** | **4** |

CSR 안쪽 sw_cp는 lookahead, 바깥 sw_cp는 flooring — 값이 하나뿐이면 이런 어긋남
자체가 불가능해진다.

## 3. 변경 대상

| 파일 | 내용 |
|---|---|
| `solution/ffc_schedule.py` | `insert_idle_time` 파라미터 삭제, `K == 1` 경로에서 lookahead 분기만 남기고 flooring/ceiling 삭제, docstring 재작성 |
| `algorithm/coarsen_solve_reconstruct.py` | option 필드·검증 삭제, private helper 3개 파라미터 삭제 |
| `algorithm/sw_cp/option.py` | 필드 + 검증 삭제 |
| `algorithm/sw_cp/dispatcher.py` | 2개 호출부에서 인자 제거 |
| `algorithm/dispatcher/paired.py` | 시그니처 4개의 파라미터 + 통과 인자 삭제 |
| `orchestration/controller.py` | step 3개의 파라미터·전달부·docstring |
| `main.py` | `DEPRECATED_STEP_KWARGS` + `_reject_deprecated_step_kwargs` preflight |
| `tests/` | `test_coarsen_solve_reconstruct.py`, `sw_cp/test_option.py`, `solution/test_ffc_schedule.py`, 신규 `test_deprecated_step_kwargs.py` |
| `scripts/` | `dump_csr_coarse_obj.py`, `analyze_csr_idle_modes.py` **삭제** (§6) |
| `TODO.md` | status 갱신 + 삭제된 스크립트 경고 |

### 3.1 동작이 바뀌는 호출부

`idle_mode`를 넘기지 않아 flooring이던 **11개 호출부**가 `K == 1`에서 lookahead로
바뀐다:

- `solution/schedule_build.py:176` — `reconstruct_coarse_schedule`
  (**CSR 자신의 최종 original-scale 후처리**)
- `orchestration/controller.py:399`, `algorithm/cpsat_adapter.py:212`
- `algorithm/neh_cp/dispatcher.py:215, 352, 556`
- `algorithm/flip_makespan_cp/dispatcher.py:287`
- `algorithm/mcf_lb/full_sch_builder.py:267`,
  `algorithm/mcf_lb/last_stage_sch_builder.py:173`
- `algorithm/dispatcher/bn2d.py:94`, `algorithm/cumulative_heuristic.py:110`
- (`paired.py` 경유) `controller.initialize_by_dispatch_v3` / `_v4`

즉 mcf_lb → flip → neh_cp → sw_cp → base CP로 이어지는 **주 파이프라인 전체**가
영향을 받는다. §7의 A/B는 이 범위를 커버하도록 설계했다.

## 4. 설계 결정

### D1. 필드를 남기지 않고 삭제한다

deprecation shim으로 한 값짜리 필드를 남기는 안(초기 rev 2)은 폐기했다. 값이
하나면 그것은 knob이 아니라 상수이고, 필드를 남기면 "설정 가능하다"는 오해가
그대로 남는다.

### D2. 잃어버린 deprecated 메시지는 launch-time preflight로 되살린다

필드를 지우면 YAML의 `idle_mode` 키는
`_call_method(method_name, **kwargs)`(`routix/subroutine_controller.py:178`)에서
**raw `TypeError`** 로, 그것도 worker 안에서 run 도중에 터진다.

`main.py`에 시나리오 flow를 재귀 스캔하는 preflight를 두었다:

```python
DEPRECATED_STEP_KWARGS: dict[str, str] = {
    "idle_mode": (
        "removed 2026-07-22 — CSR and sw_cp always use 'lookahead'. Delete the "
        "key (see plans/experiment/20260722/csr_idle_mode_lookahead_only.md)"
    ),
}
```

- **launcher에서** 실행 — worker를 띄우기 전에 실패해야 96-worker 실행이
  낭비되지 않는다.
- **값 무관하게 키 존재만으로 거부.** `lookahead`만 통과시키면 삭제 목적이 무산.
- 재귀 스캔이라 CSR `solve_flow` 안의 중첩 step 키도 잡힌다.
- 메시지에 시나리오 이름과 step 이름을 함께 낸다.

### D3. `metadata/`의 기존 config는 수정하지 않는다

`metadata/**.yaml`은 "run setting" provenance 커밋의 대상 — 그 run이 실제로 어떤
설정으로 돌았는지의 기록이다. 특히
`metadata/20260702/csr_idle_modes_v4_config.yaml`은 3-mode 비교가 실험 목적이었
으므로 `idle_mode: flooring`을 지우면 **기록이 거짓이 된다.**

⇒ `idle_mode` 키를 가진 22개 config는 그대로 두고 preflight가 거부하게 둔다
(= "이 config는 이 코드로는 재현되지 않는다"는 사실을 정확히 알려주는 동작).
다음 실행용 config는 새 날짜 디렉터리에 키 없이 쓴다.

### D4. `insert_idle_time`의 `K` 인자 일반화는 그대로 둔다

`K == 1` 경로에 남은 `d_lo // K`, `K * c` 표현은 그 경로가 `K == 1`에서만
도달하므로 실질적으로 항등이다. 지금 정리하면 diff와 위험만 커지고, 어차피 §8의
후속 과제가 이 경로 전체를 없앤다.

## 5. 작업 순서 (TDD, 실행 완료)

1. **Red** — 필드 부재 테스트(`TypeError`) 2개, preflight 테스트 5개 작성 후
   실패 확인
2. **Green** — option/controller/preflight 구현
3. **범위 확대(rev 3)** — `insert_idle_time`·`paired.py` 파라미터 및
   flooring/ceiling 분기 삭제, rev 2에서 도입했던 `_SEED_IDLE_MODE` /
   `_IDLE_MODE` 상수도 함께 제거(넘길 대상이 사라졌으므로)
4. **검증** — `uv run ruff check` / `uv run ruff format` / `uv run pytest`
5. **A/B 측정** — §7
6. **문서·스크립트 정리** — §6, `TODO.md`

## 6. 삭제한 스크립트

`scripts/dump_csr_coarse_obj.py` 와 `scripts/analyze_csr_idle_modes.py` 를
**삭제**했다.

- 전자는 `CoarsenSolveReconstructOption(idle_mode=mode)`로 3-mode를 돌리는
  덤퍼, 후자는 그 결과의 3-mode 피벗 전용 분석기다. 둘 다 `idle_mode` 없이는
  성립하지 않는다.
- **"과거 분석 재현용 보존"이라는 명분이 성립하지 않는다**: 본 변경으로
  `reconstruct_coarse_schedule`의 idle insertion까지 lookahead가 되었으므로,
  설령 스크립트를 살려도 `recon_obj`가 2026-07-02 baseline CSV와 일치하지
  않는다(lookahead 행조차도).
- 3-mode 비교를 다시 하려면 **본 변경 이전 커밋에서 flooring/ceiling 분기를
  복원**해야 한다. 결과와 결론은 `docs/reviews/20260702_csr_idle_modes.md`,
  TODO.md 2026-07-19 status에, CSV는 `analysis/20260702T013931_438875/`에,
  코드는 git 히스토리에 남아 있다. `TODO.md`의 재현 명령 블록에 이 경고를
  달아 두었다.

### 6.1 함께 고친 스크립트

`scripts/20260720/analyze_csr_surrogate_fidelity.py`는 `insert_idle_time`을
**직접** 호출하며 `idle_mode=`를 넘기고 있었다(`:164`). rev 2 시점에는 무관했지만
rev 3에서 파라미터가 사라지면서 `TypeError`로 깨진다. `--idle-mode` CLI 인자와
`project_and_score` / `_rows_for_instance`의 인자 통과를 제거했다.

> 이 스크립트는 테스트가 없어 `pytest`로는 잡히지 않았다. `insert_idle_time`
> 시그니처를 바꿀 때는 `grep -rn "insert_idle_time" scripts/`를 반드시 함께
> 볼 것.

영향 없음이 확인된 것:

- `controller.initialize_by_dispatch_v3` / `_v4` — 인자 없이 호출하므로
  시그니처 변경과 무관(§3.1대로 동작만 lookahead로 이동).

## 7. 결과

### 7.1 구현·테스트

`uv run ruff check` 통과. `uv run pytest` **623 passed**.
`rg "idle_mode" src/` 결과는 docstring 설명 2건뿐 — 파라미터·필드는 0건.

테스트 하나가 실제로 깨졌고 그것은 **진짜 동작 변화**였다:
`test_insert_idle_time_tf_effective_window`는 "K=2 결과 == K=1(유효 window) 결과"를
end-time 동일성으로 검사했는데, `K == 1`이 lookahead가 되면서 tie에서 한 칸 더
오른쪽에 놓는다(8→9, 10→11). 두 배치 모두 window 안이라 **E/T는 0으로 동일**
하므로 불변식을 "비용 동일"로 바꾸고, byte-동일이 왜 더는 성립하지 않는지
docstring에 명시했다.

### 7.2 A/B 스모크

- **Config**: `metadata/20260722/idle_mode_ab_smoke.yaml`
  — 시나리오 A(`a_c5_batch_m`: mcf_lb → flip → neh_cp → ISW-CP → base CP,
  전 구간 `K = 1`) / B30(`b30_csr_k1_f30_batch_m_plus_2`: CSR κ=1 init + outer
  ISW-CP), `(n, c, totalMcCount)` 16셀 × 2 = **32 instance**, `idle_mode` 키 없음
- **Run** (`output/20260722_idle_mode_ab/`):

  | run | 코드 상태 | timestamp |
  |---|---|---|
  | before | 전 구간 flooring (변경 전 HEAD와 동치) | `20260722T205720_905189` |
  | rev2a / rev2b | sw_cp + CSR seed만 lookahead | `20260722T204803_957736` / `20260722T210636_752903` |
  | rev3a / rev3b | **전 호출부 lookahead (최종 코드)** | `20260722T224145_299117` / `20260722T225117_187579` |

  `before`는 워크트리를 따로 파지 않고 `_SEED_IDLE_MODE` / `_IDLE_MODE` 두 상수만
  임시로 `"flooring"`으로 바꿔 실행했다. config에 `idle_mode` 키가 없으므로 이는
  삭제 전 코드의 default 경로와 동치다.

- **노이즈 바닥** (같은 코드, 두 run): mean ΔRPDf가 A에서 +0.0028(rev2 쌍) /
  **+0.0059**(rev3 쌍), B30에서 +0.0028 / +0.0010.
  ⇒ 이 하네스의 run-to-run 편차는 **±0.006 수준**이다.
- **효과** (before → rev3, 2-run 평균):

  | 시나리오 | better / worse | mean ΔRPDf | median ΔRPDf |
  |---|---:|---:|---:|
  | A `a_c5_batch_m` | 17 / 15 | +0.00057 | **−0.00009** |
  | B30 `b30_csr_k1_f30…` | 20 / 12 | +0.00505 | **−0.00090** |

**mean과 count/median이 어긋나는 이유**: B30의 +0.00505는 단일 인스턴스의
`ΔRPDf = +0.287`이 끌어올린 값이다. 절댓값 상위 2개를 빼면 −0.00263, median은
−0.00090으로 개선 쪽이다. A도 같은 구조(상위 2개 제외 시 −0.00053).

**판정: 회귀 근거 없음, 개선 주장도 불가.** 모든 효과 크기가 노이즈 바닥
(±0.006)보다 작다. CP-SAT가 wall-clock TL + 8-thread로 돌아 비결정적이므로
32-instance 표본으로는 ±0.005 규모를 분해할 수 없다. 승인 조건("유의한 악화
없음")은 충족한다.

**후속(선택)**: 부호를 확정하려면 전 1440-grid A/B가 필요하다. 본 변경의
전제가 §2.2의 "블록 단위로 절대 나빠지지 않는다"이므로 필수는 아니다.

## 8. 남은 과제 (deferred)

`insert_idle_time`의 exact gate를 `if K > 1:` → `K >= 1`로 넓혀 `K == 1`
heuristic 경로 자체를 없애는 건 여전히 별개 과제다. 조건:

- (a) `K == 1`에서 exact gate가 진짜 최적인지
- (b) `n = 200, K = 1`에서 `O(n²)` unit-stepping이 감당되는지

부수 효과로 `K == 1`의 `sum_e > sum_t` **weight-sum** gate가 magnitude 비교로
바뀐다 — `9b7ad2a`의 논거상 후자가 더 강한 규칙이다(TODO.md 2026-07-19 status).

## 9. 개정 이력

| rev | 범위 | 왜 바뀌었나 |
|---|---|---|
| 1 | CSR option만. 필드는 남기고 flooring/ceiling에 deprecated error | 최초 요청 |
| 2 | + sw_cp. 필드를 **삭제**하고 preflight로 대체 | 한 값짜리 필드는 knob이 아니다; sw_cp도 같은 문제 |
| 3(최종) | + `insert_idle_time` / `paired.py`. 파라미터를 받는 함수 0개 | rev 1–2는 "다른 caller가 flooring default에 의존한다"를 **유지해야 할 계약**으로 오독했다. 실제로는 기본값을 안 준 사고였고, 의도는 lookahead 로직만 남기는 것이었다 |

rev 2까지의 "`insert_idle_time`·`paired.py`는 비대상" 서술은 **철회**되었다.

## 10. 롤백

`git revert` 한 번으로 원복된다. `metadata/`를 손대지 않으므로(§4 D3) config
쪽 되돌릴 것은 없다. 삭제한 스크립트(§6)도 revert로 함께 복원된다.

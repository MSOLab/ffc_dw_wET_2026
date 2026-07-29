# `insert_idle_time` 논문 충실화 — K==1 Pan flooring 복원 + K>1 CSR 정합

**작성일**: 2026-07-23 · **종류**: 코드 변경 계획(사후 작성) · **최종본**
**선행 문서**: `plans/experiment/20260722/csr_idle_mode_lookahead_only.md`
— 본 작업은 그 변경(`b971761`)이 `K == 1`에 남긴 **lookahead 규칙을 되돌린다**.

> 사후 작성이다. 코드·테스트는 이미 반영되어 있고(§7 검증 완료), 이 문서는
> "왜 이렇게 바꿨는가"를 남기기 위한 기록이다. 결론만 필요하면 §1·§4·§7.

---

## 1. 목표

`FFcSchedule.insert_idle_time`의 두 시간 스케일 동작을 **각 출처 알고리즘과
정확히 일치**시키고, 그 과정에서 함수를 읽기 쉬운 형태로 재구성한다.

- `tau == 1` (fine grid) → **Pan et al. (2017), Fig. 3 그대로** (plain flooring)
- `tau > 1` (coarse grid, CSR) → **CSR idle-time insertion 의사코드 그대로**
  (S_E1/S_E2/S_T1/S_T2 gate)

부수 목표: 거대 단일 메서드를 blocks-sweep 골격 + 규칙별 순수 함수 2개로 분리.

## 2. 근거

### 2.0 계기 — "lookahead만 남겨라"가 K==1 경로를 잘못 건드렸다

이 작업의 직접적 계기는 `b971761`의 착오다. `9b7ad2a`가 coarse-exact gate를
추가한 뒤 `insert_idle_time`의 구조는 이랬다(pre-`b971761`):

```text
while j >= 0:
    ...
    if K > 1:
        <CSR coarse-exact gate>
        continue          # ← K > 1은 여기서 전부 처리되고 빠져나간다
    if sum_e > sum_t:      # ← 이 아래는 K == 1에서만 도달한다
        if idle_mode == "flooring": ...
        elif idle_mode == "ceiling": ...
        elif idle_mode == "lookahead": ...
```

즉 flooring/ceiling/**lookahead** 세 분기는 **이미 `if K > 1: … continue`에
의해 K == 1 전용 경로**였다. lookahead는 원래 coarse(K>1) 처리를 위해 도입된
계보였지만, coarse는 exact gate가 `continue`로 가로채므로 lookahead는 K>1에서
**도달 불가능한 죽은 분기**였고 실제로는 K==1에서만 살아 있었다.

`b971761`("idle_mode 제거, lookahead만 남김")은 이 사실을 놓쳤다. 남긴 규칙이
**K==1 경로**임을 인지하지 못한 채, 목적함수 지배성(선행 문서
`csr_idle_mode_lookahead_only.md` §2.2 = lookahead가 flooring을 obj 기준으로
지배)만 근거로 lookahead를 택했다. 그러나 K==1의 출처는 Pan et al.
(2017)이고 그 규칙은 **flooring**이다. 결과적으로 "lookahead만 남김"은 **K==1을
논문과 어긋나게 만든 의도치 않은 부작용**이었다. 본 작업은 그 부작용을 되돌린다.

### 2.1 `K == 1` lookahead는 Pan 논문과 다른 스케줄을 낸다

lookahead는 flooring 후보 `Δ_a = min(Δ₁, Δ₂)` 외에 `Δ_b = Δ_a + 1`을 두고
`block_obj(Δ_b) <= block_obj(Δ_a)`이면(동률 포함) 큰 쪽을 택한다.

- 목적함수(가중 E/T)는 **절대 나빠지지 않는다** — 이는 `csr_idle_mode_lookahead_only.md`
  §2.2에서 이미 확인된 사실이고, 본 세션 재확인에서도 **20만 인스턴스 obj 차이
  0건**이었다.
- 그러나 **스케줄(완료시각)은 Pan과 달라진다**: tie(평평한 목적함수 구간)에서
  블록을 한 칸 더 오른쪽으로 민다. 랜덤 20만 인스턴스 기준 **완료시각이
  37,839~63,962건(≈38–64%)에서 상이**. 즉 lookahead는 "cost-neutral tie-break
  variant"일 뿐 Pan 논문의 idle insertion이 아니다.

사용자 요구사항은 **`K == 1`에서 Pan et al. (2017)과 스케줄까지 동일**해야 한다는
것이므로 lookahead는 부적합하다. Pan의 `Δ = min(Δ₁, Δ₂)` flooring으로 되돌린다.

### 2.2 `K > 1`은 CSR IIT 알고리즘과 이미 일치한다

`9b7ad2a`의 coarse-exact gate는 CSR idle-time insertion 의사코드
(S_E1/S_E2 partial-benefit, S_T1/S_T2 partial-penalty gate)와 동일한 규칙이다.
본 세션에서 의사코드를 충실히 별도 구현해 대조한 결과 **전 K(2,3,5,10,50) ×
narrow/wide window 30만+ 인스턴스에서 최종 스케줄 byte-동일(0건 차이)**.

- 단, 과거 `insert_idle_time`의 jump 루프에는 미세한 내부 뉘앙스가 있었다:
  `elif K*c <= d_hi`가 `τC' == d⁺` 경계 job(의사코드상 S_T1)을 0으로 만들어
  `Δ₁ = 1`(단위 이동)을 강제했다. sweep 재평가로 **결과는 같지만** 의사코드와
  문자적으로 어긋난다.
- 재작성한 `_iit_csr_shift`는 S_T1/S_T2를 명시적으로 분기하고 `has_partial`
  플래그로 `Δ₁ = 1` 조건을 판정하므로 **이 뉘앙스가 사라지고 의사코드와 문자
  단위로 일치**한다.

## 3. 변경 대상

| 파일                                  | 내용                                                                                                                                                                                                      |
| ------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `solution/ffc_schedule.py`            | `insert_idle_time`을 blocks-sweep 골격 + 순수 함수 2개(`_iit_pan_shift`, `_iit_csr_shift`)로 재구성. `K == 1`을 lookahead → **Pan flooring**으로 복원. `Δ₂`/`Δ₁`의 ∞ sentinel은 `delta_big_M = self.makespan` 사용(§4 D4). docstring 재작성(§4 D2) |
| `tests/solution/test_ffc_schedule.py` | `test_insert_idle_time_tf_effective_window`의 docstring을 현재 동작에 맞게 수정(§5)                                                                                                                       |

공개 시그니처 `(due_window_map, ewt_map, twt_map, *, time_factor=1)`는 **불변**.
호출부 10곳(`schedule_build`, `controller`, `cpsat_adapter`, `neh_cp`,
`flip_makespan_cp`, `coarsen_solve_reconstruct`, `cumulative_heuristic`, …)은
수정 불필요.

### 3.1 동작이 바뀌는 범위

`b971761`이 flooring → lookahead로 옮겼던 **`K == 1` 호출부 전체가 다시
flooring(Pan)으로** 돌아온다. `K > 1` 경로는 동작 변화 없음(§2.2). 즉 net으로는
`b971761`의 `K == 1` 행동 변경만 되돌리고, idle_mode 파라미터 제거와 K>1
exact gate는 그대로 유지한다.

## 4. 설계 결정

### D1. 규칙별 순수 함수 2개 + 통일된 caller

메서드 본문은 backward block sweep 골격만 담고, 블록당 이동량은 `tau == 1`이면
`_iit_pan_shift(...)`, 아니면 `_iit_csr_shift(tau, ...)`가 반환한다(둘 다
`(block, job_ids, ends, due_window_map, ewt_map, twt_map, delta2, delta_big_M)`
꼬리 인자를 공유). caller는 규칙과 무관하게 `delta > 0 → shift, else → j -= 1`
하나로 통일. 두 규칙 모두 "이동 안 함"을 `return 0`으로 표현한다.

- `_iit_pan_shift` (tau==1): S_E/S_D/S_T 분류 → `Σw⁻_E > Σw⁺_T`이면
  `min(Δ₁, Δ₂)` 반환, 아니면 0. lookahead 후보(`Δ_a+1`) 없음.
- `_iit_csr_shift` (tau>1): S_E1/S_E2/S_T1/S_T2 gate → `saved > added`이면
  partial 존재 시 `Δ₁=1`, 아니면 `min ⌊d/τ⌋ − C'`; `min(Δ₁, Δ₂)` 반환, 아니면 0.

두 규칙 모두 `saved/added`(또는 `sum_e/sum_t`)가 gate를 넘으면 `Δ ≥ 1`이 보장되어
caller의 `delta == 0 → j -= 1`은 gate 미충족일 때만 도달한다(무한루프 방지).

### D2. docstring은 vault PDF 파일 경로를 참조하지 않는다

두 참조 PDF(`(2017 COR) Pan et al. …`, `20260723_P3_csr_iit.pdf`)는 `vault/`에서
삭제되고 **git에 포함되지 않는다**. 따라서 docstring이 그 파일 경로를 가리키면
dangling 참조가 된다.

- 내부 파일명(`vault/20260723_P3_csr_iit.pdf`) 참조는 제거하고 알고리즘 내용을
  docstring 본문에 수식으로 온전히 남긴다.
- **Pan et al. (2017), Fig. 3 인용은 유지** — 공개 논문 출처라 vault 사본과
  무관하게 유효하고, `K == 1` 규칙의 출처를 명시하는 게 문서 가치가 크다.
- 변수명은 코드의 `tau`에 맞춰 `K`/`τ` 표기를 통일.

### D3. lookahead 로직은 완전히 삭제한다

`b971761` §8(deferred)은 "K==1 heuristic 경로를 exact gate로 흡수"를 별개 과제로
남겼지만, 본 변경은 **반대 방향**(논문 flooring 복원)을 택했다. 두 후보가 아니라
Pan flooring 단일 규칙만 남기므로 lookahead의 `Δ_a/Δ_b/block_obj` 블록은 삭제한다.
"cost는 같지만 스케줄이 논문과 다르다"는 이유(§2.1)로 유지할 근거가 없다.

### D4. ∞ sentinel은 magic `10**9` 대신 `self.makespan`

`Δ₂`(오른쪽 블록이 없을 때)와 `Δ₁`의 초기값은 "충분히 큰 값"이 필요하다. magic
`10**9` 대신 `delta_big_M = self.makespan`(마지막 stage 최대 완료시각)을 쓴다.

- **정당성**: 오른쪽 블록이 있으면 `Δ₂ = starts[next] − ends[cur] ≤ makespan`이라
  절대 capping되지 않는다. `Δ₁` 후보(`d − C`, `⌊d/τ⌋ − C'`)가 makespan을 넘는
  경우(due window가 현재 makespan 밖)에만 capping되는데, 이때는 outer `while j`가
  같은 `j`를 재평가하며 makespan씩 나눠 이동해 **동일 fixpoint로 수렴**한다(gate가
  위치에만 의존하고 단조 우측 이동이므로 overshoot 없음). K=50·먼 window
  overshoot/termination 테스트가 이를 커버한다.
- **주의**: 따라서 `delta_big_M`은 진짜 ∞가 아니라 "재평가로 수렴을 보장하는 하한
  스텝"이다. makespan은 비어있지 않은 스케줄에서 `≥ 1`이므로 무한루프는 없다.

## 5. 테스트 문서 정정

`test_insert_idle_time_tf_effective_window`의 docstring은 `b971761` 시점에 "두
경로는 다른 ends에 안착한다(K==1 lookahead의 tie-break) … flooring rule은
2026-07-22에 제거됨"이라고 적혀 있었다. flooring 복원으로 **이 서술이 사실과
반대**가 되었다(현재 K==1은 flooring, 두 경로가 같은 grid ends `(8,10)`에 안착).

- 단언(assert)은 원래도 **in-window E/T 비용**만 검사하므로 코드 변경 불필요.
- docstring만 현재 동작에 맞게 재작성: 불변식은 "각 job이 (스케일된) window 안에
  안착 → E/T=0", 부수적으로 두 경로가 같은 ends에 놓이지만 그건 단언 대상 아님.

같은 파일 K>1 섹션의 `flooring/ceiling/lookahead` 언급(coarse-exact 판별력 근거)은
**현재 동작을 틀리게 서술한 게 아니라** "그런 대안 규칙이면 이 케이스가 RED가
된다"는 설명이고 K>1 분기는 안 바뀌었으므로 **그대로 둔다**.

## 6. 작업 순서 (실행 완료)

1. lookahead vs flooring 차이 실측 — 스케줄 ≈56% 상이, obj 0건 차이 확인
2. 두 PDF 텍스트 추출 → Pan Fig.3 / CSR IIT 의사코드 확정
3. PDF 충실 참조 구현을 standalone으로 작성해 코드와 대조 (§7)
4. `insert_idle_time` 재구성 + `K == 1` flooring 복원 + 헬퍼 2개 분리
5. docstring 정리(§4 D2), 테스트 docstring 정정(§5)
6. `uv run ruff format` / `uv run ruff check` / `uv run pytest`

## 7. 검증

- **의사코드 대조** (standalone PDF-충실 구현 vs 코드):
  - `K == 1` vs Pan: 20만 인스턴스 — **obj 차이 0**, 코드가 나빠진 적 0,
    스케줄 차이 37,839~63,962(lookahead와의 차이 = 예상된 우측정렬 제거).
  - `K > 1` vs CSR IIT: K∈{2,3,5,10,50} × narrow/wide, 각 6만 — **스케줄 차이 0**.
- **실제 `FFcSchedule` 객체** 4만 인스턴스: 재작성 결과가 PDF-충실 참조와
  **0 mismatch**.
- `uv run ruff check` 통과. `uv run pytest tests/solution tests/algorithm`
  **316 passed**.
- 리포 전체(vault 제외)에 삭제 PDF/구 심볼(`20260723_P3_csr_iit`,
  `insert_idle_time_2/_3`, 구 헬퍼명) dangling 참조 0건.

## 8. 롤백

`solution/ffc_schedule.py`와 `tests/solution/test_ffc_schedule.py` 두 파일
변경뿐이라 `git revert`(또는 해당 커밋 되돌림) 한 번으로 원복된다. 공개
시그니처가 불변이라 호출부 영향 없음.

## 9. 남은 과제 (deferred)

`b971761` §8의 "exact gate를 `K >= 1`로 넓혀 `K == 1` 경로 자체를 제거"는
본 변경으로 **명시적으로 포기**되었다. `K == 1`은 Pan 논문 재현이 요구사항이므로
exact-gate 흡수는 더 이상 목표가 아니다. `K == 1` 경로의 `d_lo // tau`, `tau * c`
표현은 `tau == 1`에서만 도달하므로 실질 항등이지만, Pan 표기와의 대조 가독성을
위해 남겨 둔다.

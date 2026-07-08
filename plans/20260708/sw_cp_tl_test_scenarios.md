# Plan — SW-CP 크기비례 per-CP TL: capture-분위 시나리오 스윕 (p25–p75)

**Date:** 2026-07-08 · **Branch:** `20260708_more_timelimit`
**대상 파일:** `metadata/20260708/sw_cp_tl_test.yaml` (git staged, `A`)
**진입점:** `main.py` `CONFIG_PATH` → 이 파일을 가리킴
**관련:** `plans/20260707/sw_cp_proportional_tl_code_and_scenarios.md`,
`plans/20260705/sw_cp_tl_policy_investigation.md` §3.3,
`scripts/20260706/k_for_capture.py`, `scripts/20260706/k_capture_methods.md`

---

## 0. 목표 / 배경

`incremental_sw_cp`의 per-CP 시간제약을 고정 120 s가 아닌
`TL = κ · non_time_fixed_op_count` (s/op)로 주는 **크기비례 정책**을
end-to-end로 검증한다. p25/p50 결과를 본 뒤 **시간제약을 더 주는 방향**을
테스트하되, **p75(κ=0.031593)는 계수가 과함**을 확인. 따라서 p25~p75를
촘촘히 덮는 **7개 capture-분위 시나리오**를 한 run에서 비교한다.

---

## 1. 확정된 κ 도출 근거 (재계산으로 검증 완료)

`pXX` 시나리오의 κ = **k-for-capture, basis B2 (per-window 포착비율의
단순평균), capture target = XX%**, **270개 u2_pf2 window 풀링** 값.
`k_capture_methods.md` §B2 참조. 기존 p25/p50/p75가 아래 재현 명령으로
**정확히** 재현됨을 확인하고, 같은 방식으로 p30/p40/p60/p70을 산출함.

- 풀링 대상 3개 run (모두 thread=8, `--scenario u2_pf2`):
  - `output/20260705_sw_cp_tl_profile_t8/20260706T015554_738214/` (n=50 rep0, 68)
  - `output/20260705_sw_cp_tl_profile_t8_bigN/20260707T022259_167411/` (n≥100 rep0, 190)
  - `output/20260707_sw_cp_tl_profile_t8_rescued12/20260707T150918_895893/` (rescued-12)
- 재현: `k_for_capture.py`의 `P_LEVELS`에 해당 p를 넣고 위 3개 run +
  `--scenario u2_pf2`로 실행, **B2 k** 열을 읽음. (I>0 window 7627개,
  median_ntf=150.) B1 분위(t_p_abs)는 25/30/40/60/70에서 미리 계산돼 있지
  않으나 B2 k는 bisection으로 실시간 산출되어 무관.

| 시나리오 | capture p | κ (s/op) | ≈TL@median window (ntf=150) | 상태 |
|---|---:|---:|---:|---|
| p25 | 25% | 0.000311 | 0.05 s | 기존 (재현 ✓) |
| p30 | 30% | 0.000388 | 0.06 s | 신규 |
| p40 | 40% | 0.000773 | 0.12 s | 신규 |
| p50 | 50% | 0.001811 | 0.27 s | 기존 (재현 ✓) |
| p60 | 60% | 0.004570 | 0.69 s | 신규 |
| p70 | 70% | 0.015762 | 2.36 s | 신규 |
| p75 | 75% | 0.031593 | 4.74 s | 기존 (재현 ✓) |

κ 단조증가·p25<p30<p40<p50<p60<p70<p75 순서 정상. 소수점 6자리 표기(기존 κ
표기 관례)에 맞춤.

> ⚠️ 모두 **OFFLINE replay** 근사(sequential coupling 무시).
> `k_capture_methods.md` §한계 1 참조. 실제 end-to-end 효과는 이 run으로 확인.

---

## 2. 실험 세팅 (현재 파일 반영)

- **파일/출력 이름:** metadata dir이 날짜를 가지므로(형제 파일 무접두 관례)
  - metadata: `metadata/20260708/sw_cp_tl_test.yaml`
  - `output_dir`: `output/20260708_sw_cp_tl_test`
- **인스턴스:** `ins_index: [60, 61, 63, 64, 68, 150, 152, 155, 246, 248]`
  — 10개 smoke set (전체 1440 아님). 정책 스윕을 빠르게 훑기 위한 소규모 셋.
- **시나리오 7개:** p25 / p30 / p40 / p50 / p60 / p70 / p75 (capture 오름차순).
  각 시나리오는 서로 **완전히 동일**하되 딱 두 곳만 다름:
  1. `name` / `output_subdir` = `pXX`
  2. `incremental_sw_cp.non_time_fixed_op_time_limit_multiplier` = 위 표의 κ
- **공통 필드:** 모든 시나리오 `timelimit: "0.09nc"` 유지. 없애면 마지막
  `solve_base_model_cpsat`이 예산(`min(timelimit, remaining)`)을 못 받아
  무한정 돎 (`sw_cp_proportional_tl_code_and_scenarios.md` §0).
- **per-pass 시간제약 미적용:** 각 CP에 `κ·ntf`를 직접 부여
  (`batch_tl_mode: "proportional"`).

> 참고: κ가 큰 p60/p70/p75는 window별 `κ·ntf`가 커져 scenario
> `timelimit="0.09nc"` clamp가 걸릴 수 있으나, clamp 여부는 인스턴스 크기에
> 따라 다름 — 별도 조정 없이 동일 관례를 따른다.

---

## 3. 검증 (완료)

- YAML 로드해 assert: 7개 시나리오(p25/p30/p40/p50/p60/p70/p75), 각
  `output_subdir` 유일, 모든 `timelimit="0.09nc"`, 각 κ가 §1 표와 일치,
  κ 단조증가 — 전부 통과.
- 실제 실험 실행/결과 분석은 **이 계획 범위 밖** — 사용자가 직접 실행.

---

## 4. 범위 밖 / 하지 않는 것

- `k_for_capture.py`(tracked) 수정하지 않음 — `P_LEVELS` 변경은 scratchpad
  복사본에서만 수행했고 §1 값 도출/검증 용도로 끝남.
- 다른 metadata 파일 편집 없음. κ 재조정 없음(값 확정).

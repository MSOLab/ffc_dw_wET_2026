# Plan: `_insert_jobs_at_desired_starts` desired-start floor를 release time으로

## Context

`docs/TODO.md`의 "`_insert_jobs_at_desired_starts` desired-start floor" 항목.

`src/ffc_ddw_sum_et/algorithm/mcf_lb/last_stage_only.py:518`은 midpoint
warm-start의 `desired_start`를 `0`으로 클램프한다:

```python
desired_start = max((t_min + t_max - p_j) // 2, 0)
```

job은 어차피 upstream-stage release time 이전에 시작할 수 없으므로
`job_2_release[job_id]`가 더 타이트한 floor다. 현재 코드는 다음 두 단계로 이를
"사후 보정" 한다:

- placement 직후의 `make_semi_active(job_2_release_map=…)`가 release 이전에
  배치된 op을 release 시점으로 끌어올림.
- 또는 single-pass / NEH-CP 경로에서는 profile-fix CP solve가 placement 결과를
  hint로만 사용하면서 release 제약을 다시 강제함.

floor를 release로 바꾸면 midpoint placement 단계에서 곧장 release 이후 구간만
탐색하므로 위 사후 보정에 의존하지 않아도 된다.

## Behavioural ramifications

midpoint ≥ release인 job(즉 `(t_min+t_max-p_j)//2 ≥ release`인 job)은 거동에
변화가 없다 — 새 floor가 미발동.

midpoint < release인 job은 변경 후 `desired_start = release`,
`desired_end = release + p_j`가 된다. 이 케이스에서 함수의 결정은 다음과 같이
정리된다:

1. **`_interval_free` short path**가 `[release, release+p_j)`를 검사.
   비어 있는 첫 머신이 있으면 거기에 배치 (fallback 미진입).
2. short path가 모든 머신에서 실패한 경우 fallback 진입:
   - `es_best`: `max(avail_m, release)`의 머신 간 최솟값. 항상 존재.
   - `le_best`: `le_start ≥ release` 필터(line 563)와 `le_end ≤ desired_end =
     release+p_j` 제약, `le_end - le_start = p_j`를 동시에 만족하려면
     `(le_start, le_end) = (release, release+p_j)`가 유일한 후보. 그런데
     이 구간은 short path가 모든 머신에서 실패했다는 사실 자체로 어떤 머신에서도
     비어 있지 않음 → **`le_best`는 항상 `None`**.
   - line 577-578에 의해 자동으로 es 후보 채택.

따라서 midpoint < release인 job은 변경 후 **항상 es 쪽 (= release 시점 또는
그 이후 가장 빠른 자유 슬롯)으로 결정**된다. 이는 의도와 일치한다 — 어차피
release 이전 시작이 불가능하므로 latest-end 후보는 무의미하고, 가장 이른
release-respecting 슬롯에 박는 것이 정답.

추가 효과:

- **`make_semi_active`의 일이 줄어든다.** 새 placement가 이미 release 이후이므로
  끌어올릴 op이 없음.
- **midpoint ≥ release인 job끼리의 동률 처리는 그대로**. 코드의 `<=` 비교
  (line 589)에 의해 contrib/dist 완전 동률 시 좌측(es) 승은 deterministic.

## Algorithm

`desired_start` 계산 한 줄을 다음과 같이 바꾼다:

```python
desired_start = max((t_min + t_max - p_j) // 2, job_2_release[job_id])
```

`max(..., 0)` 보호는 떨어진다. `job_2_release[job_id] >= 0`이 invariant이므로
(release time은 sum of upstream processing times, 비음수) 안전.

`job_2_release`는 함수의 시그니처에서 이미 받고 있으므로 (line 446) 추가 인자
변경 없음.

## Implementation

**대상 파일**: `src/ffc_ddw_sum_et/algorithm/mcf_lb/last_stage_only.py` (단일 파일,
2곳 수정 — 코드 1줄 + docstring 1줄)

**변경 1** — line 518 (코드):

```python
# 기존
desired_start = max((t_min + t_max - p_j) // 2, 0)

# 신규
desired_start = max((t_min + t_max - p_j) // 2, job_2_release[job_id])
```

**변경 2** — line 461 (docstring 안의 동일 공식):

```python
# 기존
desired_start = max((t_min + t_max - p_j) // 2, 0)

# 신규
desired_start = max((t_min + t_max - p_j) // 2, job_2_release[job_id])
```

callers 시그니처/인자 추가 없음. `_insert_jobs_at_desired_starts`의 모든 호출
지점은 이미 `job_2_release` 매핑을 정상적으로 넘기고 있음 — line 161, 247, 349, 409.

## Critical files

- `src/ffc_ddw_sum_et/algorithm/mcf_lb/last_stage_only.py` — 유일한 수정 대상.
- `tests/orchestration/test_controller.py` — `_insert_jobs_at_desired_starts`를
  간접적으로 타고 가는 통합 테스트. midpoint < release인 job이 들어 있다면
  머신 라벨이나 시작 시각이 달라질 수 있음. 우선 그대로 돌려보고 실패 시
  업데이트 필요 여부 판단.

## Verification

1. 정적 검증
   - `uv run ruff check src/ffc_ddw_sum_et/algorithm/mcf_lb/last_stage_only.py`
   - `uv run ruff format src/ffc_ddw_sum_et/algorithm/mcf_lb/last_stage_only.py`
2. 통합 테스트
   - `uv run pytest tests/orchestration/test_controller.py -v`
3. 벤치마크 sanity check
   - `metadata/20260504/mcf_lb_init_36_config.yaml`을 신구로 한 번씩 실행하여
     - `last_stage_only_obj` (top-level) 의 분포 비교
     - `mcf_lb_diagnostic` 의 elapsed_time 비교 (make_semi_active 부담 감소
       기대 효과)
   - 기대: `last_stage_only_obj`는 평균적으로 같거나 약간 더 좋아진다
     (동일한 또는 더 적은 사후 보정으로 동등한 placement 도달).
     크게 나빠지면 인스턴스별 분석 필요.

## Safety note

이 변경은 lower bound 무결성에 영향 없다 — `_insert_jobs_at_desired_starts`는
upper-bound (feasible schedule) 생성기이고, `desired_start`를 release로 올리는
것은 시작 영역만 좁힐 뿐 release 제약을 깨지 않는다.

거동 분석상 midpoint < release인 모든 job은 (short path 또는 fallback의 자동
es 채택을 통해) 결정적으로 release-respecting 슬롯에 배치되므로 의도되지 않은
side effect의 여지가 작다.

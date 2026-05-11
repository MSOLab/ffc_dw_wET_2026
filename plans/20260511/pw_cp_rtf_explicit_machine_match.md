# PW-CP: RTF explicit machine matching (hybridflowshop Phase 3 port)

## 목표

`build_full_schedule_from_cp`의 RTF 처리를 hybridflowshop의
`cpsat_model_2/pw_cp.py:create_pw_cp_schedule` Phase 3 방식 — **source-mc → target-mc
명시적 매칭 + `add_ops_times_2_mc`로 시간·머신 강제** — 으로 강화한다.
hybridflowshop의 `AssertionError`는 채택하지 않고 divergence 카운터만 유지한다.

## 배경

직전 refactor (`refactor(pw-cp): free RTF machine in merge replay`, 2e88664) 에서 RTF를
`(LPF + unfixed + RPF + RTF)` 통합 cp_start 정렬 + `add_operation_2_stage(release_t=cp_start)`
greedy로 함께 replay하도록 바꿨다. 3 시나리오 (n=50/m, n=50/2m, n=200/2m) 에서 divergence 0건
달성. 다만:

- `add_operation_2_stage`는 `release_t`를 **하한**으로만 받기에 이론적으로 RTF가 cp_end 이후로
  미끄러질 가능성이 0이 아님.
- hybridflowshop은 RTF에 한해 별도 phase에서 명시적 매칭 + 시간 강제 (`add_ops_times_2_mc`)로
  슬라이드 자체를 막아둠. 우리도 같은 견고성을 채택.

## 범위

- 변경 파일
  - `src/ffc_ddw_sum_et/algorithm/pw_cp/cp_model.py` — `build_full_schedule_from_cp` 함수 +
    헬퍼 2개로 분리.
  - `src/ffc_ddw_sum_et/algorithm/pw_cp/step_log.py` — `cp_divergence_count` docstring 한 줄 갱신.
- 호출부 (`pw_cp/dispatcher.py:219`) 영향 없음 — 시그니처 유지.
- 새 단위 테스트는 작성하지 않음. 기존 dispatcher-level 테스트 (`tests/algorithm/pw_cp/`)로 회귀
  확인.

## 설계

### Baseline (변경 없음)

`rj_schedule.deepcopy()` 후 `non_time_fixed + right_time_fixed`를 모두 제거. LTF만 남는다.

### Phase A — non-time-fixed replay (`_replay_non_time_fixed`)

`(LPF + unfixed + RPF)`를 `(cp_start asc, cp_end desc, j)` 정렬 후 `add_operation_2_stage(release_t=cp_start)`
greedy 배치. 직전 refactor의 cp_ops 루프에서 RTF만 분리한 형태이므로 동작은 그대로 보존된다.

- realised end ≠ cp_end → divergence++.

### Phase B — RTF explicit matching (`_replay_right_time_fixed`, 신규)

각 stage `i`별로:

1. `partition.right_time_fixed`를 **source machine `k`**별로 그룹화. 그룹별 ops는
   `rj_schedule`의 `(start, end, j)`로 채움.
2. Source machine을 **그룹 내 최소 start time 오름차순**으로 정렬 (hybridflowshop의
   `right_bar_init_start` 동치 — schedule에서 직접 계산).
3. 각 source 그룹에 대해:
   - 아직 매칭되지 않은 `result.machines_per_stage[i]` 후보 중 `get_machine_latest_end_time`이
     최소인 머신을 **target**으로 선택.
   - 그룹의 RTF ops를 시작시간 오름차순으로 target에 `add_ops_times_2_mc(target, j, start, end)`로
     강제 배치.
   - 1:1 매칭 — 매칭된 target은 `dispatched` 셋에 추가.

정상 경로에서는 시간 자체가 어긋날 수 없음 (`add_ops_times_2_mc`는 시간을 받아 그대로 박음).

### Fallback 정책 (`ValueError` 처리)

`add_ops_times_2_mc`는 prev/next op과 overlap이면 `ValueError`. matching 휴리스틱이 cumulative
보장 아래에서도 100% 무충돌을 보장하지는 않으므로 (m=3인 좁은 인스턴스 등에서 충돌 가능):

- try-except로 잡고 → 동일 op을 `add_operation_2_stage(release_t=cp_start)`로 fallback 배치.
- fallback 결과 realised end ≠ cp_end → divergence++.

매칭이 끝났는데 후보 target이 없는 경우 (그룹 수 > 머신 수 — 정상 케이스에선 발생 안 함):
그룹의 모든 op을 greedy fallback으로 보내고 동일하게 divergence 카운트.

### Divergence counter 시맨틱

- Phase A: realised end ≠ cp_end인 NTF 수.
- Phase B: explicit RTF 매칭이 충돌해 greedy fallback이 발동했고, 그 결과 cp_start를 못 맞춘 RTF 수.
- 합산하여 step_log의 `cp_divergence_count`에 기록.

step_log docstring 한 줄 추가: "RTF는 explicit matching이 충돌해 greedy fallback으로 떨어졌을
때만 카운트됨".

### 결정 사항

- Source 정렬 키: 그룹 내 RTF의 최소 start time (hybridflowshop의 `right_bar_init_start` 동치).
- Target 매칭: 1:1, `min(get_machine_latest_end_time)` 기준.
- 그룹 내 RTF 시작시간이 다를 수 있음 → target 머신에 시작시간 오름차순으로 박음.
- `right_bar_init_start`는 `PwCpBuildResult`에 추가하지 않음 (rj_schedule에서 직접 계산).

## 의사 코드

```python
def build_full_schedule_from_cp(...) -> tuple[FFcSchedule, int]:
    result = rj_schedule.deepcopy()
    # remove non_time_fixed + right_time_fixed
    ...
    divergence = 0
    divergence += _replay_non_time_fixed(result, stage_2_partition, op_vars, solver)
    divergence += _replay_right_time_fixed(
        result, full_instance, stage_2_partition, rj_schedule
    )
    return result, divergence


def _replay_non_time_fixed(result, stage_2_partition, op_vars, solver) -> int:
    """현재 cp_ops 루프 (RTF 제외 버전)."""
    ...


def _replay_right_time_fixed(result, full_instance, stage_2_partition, rj_schedule) -> int:
    start_map = rj_schedule.get_jik_2_start_time_map()
    end_map = rj_schedule.get_jik_2_end_time_map()
    divergence = 0
    for i in full_instance.stage_id_list:
        partition = stage_2_partition.get(i)
        if partition is None or not partition.right_time_fixed:
            continue
        # group by source mc k
        src_groups: dict[McIdType, list[tuple[int, int, JobIdType]]] = {}
        for j, k in partition.right_time_fixed:
            s = int(start_map[(j, i, k)])
            e = int(end_map[(j, i, k)])
            src_groups.setdefault(k, []).append((s, e, j))
        for k in src_groups:
            src_groups[k].sort()
        # source order: by min start within group
        src_order = sorted(src_groups, key=lambda k: src_groups[k][0][0])
        dispatched: set[McIdType] = set()
        for src_k in src_order:
            target = min(
                (m for m in result.machines_per_stage[i] if m not in dispatched),
                key=lambda m: result.get_machine_latest_end_time(i, m),
                default=None,
            )
            if target is None:
                for s, e, j in src_groups[src_k]:
                    result.add_operation_2_stage(i, j, e - s, release_t=s)
                    if result.get_job_end_time(i, j) != e:
                        divergence += 1
                continue
            for s, e, j in src_groups[src_k]:
                try:
                    result.add_ops_times_2_mc(i, target, j, s, e)
                except ValueError:
                    result.add_operation_2_stage(i, j, e - s, release_t=s)
                    if result.get_job_end_time(i, j) != e:
                        divergence += 1
            dispatched.add(target)
    return divergence
```

## 검증

1. `uv run ruff check src/ffc_ddw_sum_et/algorithm/pw_cp/`
2. `uv run pytest tests/algorithm/pw_cp/` (22개 회귀)
3. 직전 RTF refactor를 검증한 동일 3 시나리오 재실행 — divergence 0 유지 확인 (Phase A로 분리된
   NTF가 이미 0이었으므로 회귀 없으면 그대로 0).
4. 더 빡센 케이스 (n=200, batch=m) 한 번 보기 — fallback이 발동되더라도 schedule은 feasible해야
   함.

## 안 하는 것

- `AssertionError` 추가 (사용자 지시).
- `PwCpBuildResult`에 `right_bar_init_start` dict 신규 노출.
- 호출부 시그니처 변경.
- 단위 테스트 신규 작성.

## 참고

- 출발점 구현: `hybridflowshop/cpsat_model_2/pw_cp.py:create_pw_cp_schedule` L462-597
  (Phase 1 LTF, Phase 2 NTF, Phase 3 RTF matching).
- 직전 refactor 커밋: `2e88664 refactor(pw-cp): free RTF machine in merge replay`.

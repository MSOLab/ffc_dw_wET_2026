# NEH-CP `extra_batch_size_expr` 추가

## Context

지금 `NehCpConstructor.run()`의 `added_batch_size`는 상수 정수만 받는다. 인스턴스 크기에
따라 batch를 키우고 싶을 때마다 새 config 시나리오를 손으로 적어야 했다. 새 옵션으로
`12 + n/25`처럼 **상수 오프셋 + n 비례 항** 형태를 표현하고 싶다.

깔끔한 접근: `added_batch_size`는 그대로 두고(상수 오프셋), 새 인자
`extra_batch_size_expr: str | None`을 추가해 `n`에 비례하는 가산항을 받는다. 실제 step별
가산 batch는 `added_batch_size + int(resolve_value_expr(extra_batch_size_expr, n, c, m))`.

precedent: `cp_tl`/`total_timelimit`이 이미 `resolve_value_expr`을 통해 `"0.024nc"` 류
suffix grammar를 지원한다. 이 메커니즘에 `n` suffix만 더하고, NEH-CP 인자 하나를 더한다.

최종 결과: 새 config (`metadata/20260427/neh_cp_config_14.yaml`)에서

```yaml
added_batch_size: 12
extra_batch_size_expr: "0.04n"   # 실제 batch = 12 + n/25
```

와 같이 쓸 수 있다.

## Files to change

### 1. `src/ffc_ddw_sum_et/orchestration/value_resolver.py`

- 기존 `nc`/`c`/`m` 분기 옆에 `elif s.endswith("n"):` 추가 (단, `nc` 분기보다 **뒤에**
  두어야 `"0.024nc"`가 먼저 매칭됨; `endswith` 체크 순서가 grammar의 우선순위이므로
  `nc` → `n` → `c` → `m` 순서로 둔다).
- `factor * job_count` 반환.
- 마지막 `ValueError` 메시지에 `n` 패턴도 함께 명시.
- 함수 자체는 generic이므로 `cp_tl_string`이라는 변수명으로 메시지 만드는 부분은
  현재도 부정확함 — 그냥 `value_expr`로 바꾸는 김에 정리 (작은 곁가지).

### 2. `src/ffc_ddw_sum_et/orchestration/neh_cp.py`

- `NehCpConstructor.run()` (line 143-160) 시그니처에 `extra_batch_size_expr: str | None = None`
  추가 — `added_batch_size` 바로 뒤가 자연스러움.
- 본문에서 `n`/`stage_count`/`last_stage_mc_count`가 결정된 직후 (line 290 직후, line 294의
  `num_batches` 분기 **이전**) 다음 처리:

  ```python
  if extra_batch_size_expr is not None and num_batches is None:
      extra = resolve_value_expr(
          extra_batch_size_expr, n, stage_count, last_stage_mc_count
      )
      added_batch_size = added_batch_size + int(extra)
  ```

  - `num_batches`가 설정된 경우는 line 301에서 `added_batch_size`를 통째로 덮어쓰므로
    `extra_batch_size_expr`는 무시한다 (기존 `added_batch_size` 무시 동작과 동일 정책).
  - `int(extra)`: 정수 절단. `0.04 * 50 = 2.0` → 2, `0.04 * 100 = 4.0` → 4 같은 자연스러운
    결과. floor 동작 의도임.
- docstring에 `extra_batch_size_expr` 항목 추가 (한두 줄: 의미와 `num_batches`와의 상호작용).

### 3. `src/ffc_ddw_sum_et/orchestration/controller.py`

- `FFcDDWSubroutineController.neh_cp()` (line 959-) 시그니처에 동일하게
  `extra_batch_size_expr: str | None = None` 추가.
- `NehCpConstructor(self).run(...)` 호출에 `extra_batch_size_expr=extra_batch_size_expr`
  전달.

### 4. **New** `metadata/20260427/neh_cp_config_14.yaml`

- `metadata/20260426/20260426_config.yaml`을 베이스로 단일 시나리오:
  - `added_batch_size: 12`
  - `extra_batch_size_expr: "0.04n"`
  - `total_timelimit`은 기존 운영 중이던 `"0.01nc"`(혹은 사용자가 지정) 유지.
  - `output_dir: output/20260427`
  - `instance_worker_cnt: 48` (사용자 default per memory).
  - `output_subdir`/`name`: `neh_cp_bs12_plus_004n_dplus2_pf1` 류.
- 헤더 주석에 새 옵션 의미 한 줄 설명.

## Files NOT to change

- 기존 `cp_tl`/`total_timelimit` 동작은 변경 없음 — `n` suffix가 추가되니 이론상 이쪽도
  `"0.04n"`을 받을 수 있게 되지만, 별도 설명 없음(자연스럽게 활성화).
- 기존 config (`metadata/20260423/.../`, `metadata/20260425/.../`, `metadata/20260426/...`)
  는 손대지 않음.

## Verification

1. **Lint/format**: `uv run ruff check` / `uv run ruff format` 통과.
2. **Resolver 단위 동작 (수동)**: `uv run python -c` 한 줄로 확인:
   - `resolve_value_expr("0.04n", 50, 5, 3)` → `2.0`
   - `resolve_value_expr("0.04n", 100, 5, 3)` → `4.0`
   - `resolve_value_expr("0.024nc", 50, 5, 3)` → `6.0` (기존 grammar 유지 회귀 검사)
3. **End-to-end smoke**: `metadata/20260427/neh_cp_config_14.yaml`에 일시적으로
   `ins_index: [0, 1]` 작은 서브셋 지정 후 `uv run python main.py
   metadata/20260427/neh_cp_config_14.yaml` (혹은 프로젝트 entry point) 실행해서
   - run이 정상 종료되고
   - log에 batch별 첫 batch 크기 ≈ `max(12 + 0.04*n, max_m*2)`로 찍히는지 확인.
   - 테스트 후 `ins_index` 주석 처리하고 본 실험으로 사용.
4. **회귀**: 기존 NEH-CP config (예: `metadata/20260426/20260426_config.yaml`) 한 시나리오를
   `extra_batch_size_expr` 없이 짧게 돌려 동작 변화가 없는지 확인.

## 결정 사항 / 비결정 사항

- **결정**: `n` suffix만 추가 (아직 `12 + 0.5c` 같은 c 가산 요청은 없음 — YAGNI).
- **결정**: `int()` 절단(floor)으로 변환. round나 ceil 필요해지면 그때 옵션화.
- **결정**: `num_batches` 설정 시 `extra_batch_size_expr`는 무시 (기존 `added_batch_size`
  무시 정책과 일관).
- **비결정**(승인 후 확인): config 시나리오의 `total_timelimit`/sorting/`pf_method` 같은
  나머지 파라미터는 `metadata/20260426/20260426_config.yaml`의 첫 시나리오를 그대로
  복사 — 사용자가 다른 값 원하면 알려줘야.
- **참고**: 승인 후 본 plan 파일은 사용자 선호에 따라 `plans/experiment/20260427/neh-cp-extra-batch-size-expr.md`
  로 옮겨 commit 한다.

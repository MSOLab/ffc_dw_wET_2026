# job_contrib_cp step label 정규화 — jd 레벨당 1 label (전 차트 공통)

**작성일**: 2026-07-28 · **종류**: 코드 변경 계획
**상태**: C1·C2·C3 구현 완료 · 차트 육안 확인 완료 (2026-07-28)
**선행**: 없음 · **후속**: 없음 (code change, 실험 아님)

---

## 1. 문제

`incremental_job_contrib_cp` 구간의 차트 표현에 독립적인 문제가 둘 있다.
**문제 1(증상 A·B)** 은 step label 파편화, **문제 2(증상 C)** 는 composite step의
종료 지점 마커 누락이다.

### 문제 1 — step label이 (jd, rep) 쌍마다 생성된다

step label이 **jd 레벨당 하나가 아니라 (jd, rep) 쌍마다 하나씩** 생성된다.
증상은 두 가지이고, 두 번째가 더 중요하다.

### 증상 A (가독성) — 같은 jd가 여러 점으로 찍힌다

`multi_scenario_method_mean_rpdf_and_mean_norm_time_scatter.html` payload
(jd006에서 rep이 2회 돈 인스턴스):

```
[8]  label=incremental_job_contrib_cp.3-jd006_r001  x=0.5742
[13] label=incremental_job_contrib_cp.4-jd006_r002  x=0.8143
```

### 증상 B (정확성) — 인스턴스마다 step index가 어긋나 평균이 깨진다

같은 시나리오의 두 인스턴스 obj_log 실측
(`output/20260728_incremental_job_contrib_cp_resume/20260728T165441_559062/ijd01n_k5_resume/`):

```
Rep1: … 3-jd006_r001, 4-jd006_r002, 5-jd007_r001, 6-jd008_r001, …
Rep3: … 3-jd006_r001, 4-jd007_r001, 5-jd008_r001, …
```

`4-`가 Rep1에서는 jd006의 2회차, Rep3에서는 jd007이다. 차트들은
`subroutine_name` 문자열을 step key로 쓰므로 **같은 jd 레벨이 인스턴스마다 다른
key로 들어간다.** 결과적으로 `load_method_mean_metrics`의 각 평균 점은 서로 다른
인스턴스 부분집합 위에서 계산되고(나머지는 carry-forward로 메워짐),
`_prepare_scenario_endpoint_df`의 `subroutine_order`도 시나리오/인스턴스 간
정렬이 어긋난다. 이건 미관 문제가 아니라 집계 정확성 문제다.

한 인스턴스에서 rep이 한 번이라도 더 돌면 그 뒤의 **모든** jd 레벨 index가 1씩
밀리므로, 증상 B는 rep이 발생하는 순간 광범위하게 발생한다.

### 문제 2 — 증상 C: flow의 마지막이 top-level 마커로 끝나지 않는다

method-mean scatter는 marker shape으로 call level을 구분한다 (`open circle` =
top-level controller step, `open star-diamond` = 그 아래 등록된 sub-step).
`coarsen_solve_reconstruct`는 자기 자신의 endpoint를 등록하므로 obj_log에
bare label `1-coarsen_solve_reconstruct`가 남고, 차트에서 **그 outer step의 종료
지점이 동그라미로 찍힌다**.

반면 `incremental_job_contrib_cp`는 아무것도 등록하지 않는다
(`controller.py:3324` — "Registers nothing"). 따라서 bare label
`2-incremental_job_contrib_cp`가 obj_log에 존재하지 않고, flow의 **마지막 점이
sub-step(star-diamond)인 `…jdNNN`으로 끝난다.** 2026-07-28 재생성 차트
(`output/20260728_incremental_job_contrib_cp_resume/20260728T174300_971238/…_multi_scenario_method_mean_rpdf_and_mean_norm_time_scatter.html`)
에서 확인됨.

문제 1의 정규화와는 **무관한 별개 결함**이다 (label을 아무리 정리해도 없는 점은
생기지 않는다).

## 2. 진단 — label이 만들어지는 경로

routix `MethodContextManager.context_of_current_method`는 call stack을
`"{index}-{name}"` 형태로 만들어 `.`으로 join한다
(`.venv/.../routix/method_context_manager.py:114-118`). `index`는
`DepthIndexTracker`가 **깊이별로 push마다 증가**시키는 카운터다.

`controller.py:3497` 은 rep마다 context를 push한다:

```python
context_name = f"jd{jd:03d}_r{rep:03d}"
with self.temporarily_extended_context(context_name):
    self.job_contrib_cp(jd_target=jd, **base_kwargs)
```

따라서 obj_log의 note label은:

```
2-incremental_job_contrib_cp.3-jd006_r001   ← depth2 카운터 3번째 push
2-incremental_job_contrib_cp.4-jd006_r002   ← 4번째 push
```

`obj_log_loader._parse_step_label`이 바깥쪽 `2-`만 벗기므로
`subroutine_name = "incremental_job_contrib_cp.3-jd006_r001"`이 되고,
**step index prefix(`3-` vs `4-`)와 rep suffix(`_r001` vs `_r002`)가 둘 다 남는다.**
둘 중 하나만 제거해도 나머지가 달라 여전히 별개 key다.

### 용어 정정 — `_rNNN`은 "retry"가 아니다

`controller.py:3571-3589`의 rep 루프는 **개선이 있을 때만** 반복하고, 개선이
없으면 즉시 break한다. 즉 `_r002`는 실패한 재시도가 아니라 **jd006에서 한 번 더
개선에 성공한 iteration**이다.

따라서 이 변경은 "중복/노이즈 점 제거"가 아니라 **jd 레벨당 1점으로 접고 그 레벨의
중간 개선점을 버리는 것**이며, 이는 의도된 트레이드오프다. 차트가 그리는 값은
best-so-far incumbent이고 병합 시 `global_end_sec`가 가장 큰 마지막 rep이
선택되므로, 남는 점은 그 jd 레벨의 최종(=최선) 상태다. 레벨 내부의 개선 궤적은
`_incremental_job_contrib_cp_progress.json` / `_incremental_job_contrib_cp_log.yaml`
에 그대로 남으므로 정보 손실은 차트 한정이다.

## 3. 설계 결정 — 정규화를 어디서 하는가 (문제 1)

**채택: `obj_log_loader`의 `CallSegment` 생성 지점 1곳.**
`subroutine_name`을 정규화하고 `prefixed_subroutine_name`에는 원본 label을 그대로
남긴다. 모든 차트가 `subroutine_name`을 step key로 쓰므로 한 곳만 고치면 런 안의
모든 차트가 같은 label 체계를 갖는다 (single source of truth).

### 기각 1 — controller에서 context_name을 `jd{jd:03d}`로 (rep suffix 제거)

`call_context`는 obj_log note label일 뿐 아니라 **per-call 아티팩트 파일명의
prefix**다 (`controller_core.py:343`, `:384`, `:392` — step log YAML, gantt SVG,
CP progress JSON). rep마다 context가 같아지면 파일이 서로 덮어쓴다. 또한 index
prefix는 routix가 부여하므로 controller에서 없앨 수 없다 → 증상 B가 남는다.

### 기각 2 — `method_mean_scatter.py`에서만 정규화

증상 B는 `rpdf_scatter_chart`와 `multi_scenario_method_chart`(flow comparison)에도
똑같이 존재한다. 한 차트에서만 고치면 같은 런의 차트 간 label이 달라지고, 나머지
차트에 같은 처리가 필요해지는 순간 정규화 로직이 복제된다.

## 4. 변경 사항

### C1 — `obj_log_loader`에 label 정규화 도입

`src/ffc_ddw_sum_et/report/obj_log_loader.py`

```python
_JOB_CONTRIB_REP_RE = re.compile(r"^(.*incremental_job_contrib_cp)\.\d+-(jd\d+)_r\d+$")


def _normalize_subroutine_name(name: str) -> str:
    """Collapse ``incremental_job_contrib_cp``'s per-rep contexts to one name
    per jd level: ``….3-jd006_r001`` / ``….4-jd006_r002`` → ``….jd006``.
    """
    match = _JOB_CONTRIB_REP_RE.match(name)
    return f"{match.group(1)}.{match.group(2)}" if match else name
```

적용은 `_build_calls_for_series` 한 곳 (`obj_log_loader.py:101-121`):

```python
idx, raw_name = _parse_step_label(label)
sub_name = _normalize_subroutine_name(raw_name)
...
CallSegment(
    call_index=idx,
    subroutine_name=sub_name,              # 정규화됨
    prefixed_subroutine_name=label,        # 원본 유지 (변경 없음)
    ...
)
```

`_parse_step_label`은 파싱만 하도록 그대로 둔다 (파싱과 정규화 분리).

정규식 설계 근거:

| 요소 | 이유 |
|---|---|
| `^(.*incremental_job_contrib_cp)` | CSR 안에서 호출될 경우 label이 `coarsen_solve_reconstruct-N-incremental_job_contrib_cp.…`가 되므로 앞쪽 경로를 보존한 채 매칭 |
| `\.\d+-` | routix가 붙인 depth index prefix 제거 |
| `(jd\d+)` | 보존할 부분 |
| `_r\d+$` | `{3}` 고정폭이 아니라 `\d+` + 끝 anchor. rep이 1000회를 넘어도(`_r1000`) 안전하고, 이름 중간의 유사 패턴을 건드리지 않음 |
| 전체 anchor 매칭 | "`incremental_job_contrib_cp`인 entry만"이라는 게이팅과 prefix 제거를 조건 하나로 통합. `incremental_sw_cp.1-batch_002` 등 다른 step의 index prefix는 batch 구분에 의도적으로 쓰이므로 건드리지 않는다 |

**단순화 원칙:** `CallSegment` 생성 지점 1곳만 바꾼다. `build_endpoint_df` /
`build_raw_progression_df` / `step_order` / `groupby` / `_order_parents_after_children`
는 전부 `subroutine_name`에서 파생되므로 자동으로 따라온다. 차트 쪽 코드 변경은 없다.

### C2 — 문서/주석 갱신

- `obj_log_loader` 모듈 docstring: endpoint label 형식 설명에 "`subroutine_name`은
  정규화된 표시용 이름, `prefixed_subroutine_name`은 원본 label" 명시.
- `CallSegment.subroutine_name` / `.prefixed_subroutine_name` 필드 주석 추가.
- `method_mean_scatter.load_method_mean_metrics` docstring의 step-key 설명
  (`method_mean_scatter.py:159-166` 주석, `:136-139` Returns의 `label` 설명)에
  job_contrib은 jd 레벨당 1 point임을 추가.

### C3 — `incremental_job_contrib_cp`가 자기 endpoint를 등록 (증상 C)

`src/ffc_ddw_sum_et/orchestration/controller.py`

`coarsen_solve_reconstruct`의 선례(`controller.py:3104-3122`)를 그대로 따른다:
elapsed를 측정한 **직후** `_register`를 정확히 1회 호출한다.

```python
# for jd in level_list: ... 루프가 끝난 직후, try 블록 안 (finally의 dump보다 앞)
elapsed = time.monotonic() - step_start
self._register(
    SubroutineReport(
        elapsed_time=elapsed,
        obj_value=self.solution_manager.best_obj_value,
        obj_bound=None,
    ),
    self.solution_manager.get_incumbent(),
)
```

`jd_start_cnt >= n` 조기 반환 경로에도 같은 등록이 필요하다. 이때 실제 경과
시간을 보고하려면 `step_start = time.monotonic()`을 **메서드 진입 지점**으로
올려야 한다 (기존에는 검증·조기 반환 블록 뒤에 있었다). 이 이동은 per-iteration
CP progress의 `offset = iter_start - step_start`도 step 진입 기준으로 맞춘다.

설계 세부:

| 항목 | 결정 | 근거 |
|---|---|---|
| `solution` 인자 | **현재 incumbent** (`get_incumbent()`) | `None`은 안 된다 — `work_status`(`controller_core.py:550-556`)가 `history[-1]`을 읽어 `solution is None`이면 `None`을 반환하므로, 정상 종료한 런이 `<instance>_instance_result.yaml`·summary CSV에 **status 미상**으로 기록된다. incumbent 재등록은 안전하다: routix `register`는 **엄격히 더 나은** 목적값에서만 incumbent를 교체하고(`solution_manager.py:126-139`), 일관성 검사는 `solution.obj_value`와 report의 `obj_value`를 비교하는데 이 둘은 구성상 일치한다 |
| `obj_value` | `self.solution_manager.best_obj_value` (**필수, None 금지**) | `_fold_history_into_obj_log_dicts`는 `report.obj_value is not None`일 때만 endpoint note를 쓴다 (`ffcddw_single_instance_runner.py:138-141`). None이면 label이 아예 안 생겨 C3가 무효 |
| `obj_bound` | `None` | composite이 새로 증명한 bound가 없다. `register`는 None bound를 무시하므로 bound 계열에 영향 없음 |
| 등록 위치 | 루프 종료 직후, `finally`의 dump보다 **앞** | `_wrap_report`가 `start_time = timer.elapsed_sec - elapsed_time`으로 역산하므로 elapsed 측정과 `_register` 사이에 작업이 끼면 obj_log 타임스탬프가 틀어진다 (`controller_core.py:261-271`). dump는 register 이후 post-work로 두는 것이 contract가 허용하는 형태 |
| 반환형 | `-> None` 유지 | 현재 flow runner가 이 step의 반환값을 쓰지 않는다. 굳이 `SubroutineReport`로 바꾸지 않는다 (YAGNI) |

차트에서의 결과: bare label `<idx>-incremental_job_contrib_cp`가 obj_log에
생기고 → `_is_top_level_method`가 `.`이 없으므로 `True` → **open circle**.
`_order_parents_after_children`(`method_mean_scatter.py:43-71`)가 부모를 마지막
자식 뒤로 당기므로 flow의 끝에 위치한다.

**위험 1 — 타임스탬프 충돌.** `value_notes[end_key] = label`은 `setdefault`가
아닌 대입이다 (`ffcddw_single_instance_runner.py:141`). 부모의 endpoint 시각이
마지막 자식과 정확히 같으면 **마지막 jd 레벨의 label이 부모 label로 덮인다.**
실제로는 루프 탈출 처리만큼(마이크로초) 차이가 나므로 충돌하지 않지만,
검증에서 두 label이 모두 존재하는지 확인한다.

**위험 2 — step 계약 해석.** `orchestration/AGENTS.md`의 "step 호출당 `_register`
최대 1회" 불변식은 `solution_manager.history`의 귀속을 모호하지 않게 하려는 것이다.
CSR은 inner step이 **child controller**에 등록되므로 부모 컨트롤러 기준 1회다.
`incremental_job_contrib_cp`는 inner `job_contrib_cp`가 **같은 컨트롤러**에
등록하므로, C3는 이 리포에서 처음으로 "부모와 자식이 같은 컨트롤러에 등록"하는
사례가 된다. 구현 시 `AGENTS.md`에 이 케이스를 명시해 계약을 갱신할 것.

**부작용 (의도된 것).** 인스턴스당 history 엔트리가 1개 늘어 `*_report.xlsx`의
step 행이 1행 증가한다. `work_status`는 이제 composite 엔트리에서 읽히지만,
incumbent를 함께 등록하므로 값 자체는 종전과 같은 `feasible`을 유지한다
(위 `solution` 행 참조).

**범위 밖.** `incremental_sw_cp`도 같은 이유로 자기 endpoint가 없다. 동일 처리가
일관적이겠지만 이번 변경 범위에 넣지 않는다 (사용자가 지적한 것은 job_contrib
구간이고, sw_cp는 현재 CSR 내부에서 호출되어 CSR의 동그라미로 닫힌다).

### 기각 (증상 C) — 차트에서 부모 점을 합성

`method_mean_scatter`에서 "자식은 있는데 자기 endpoint가 없는 composite"을 만나면
마지막 자식의 (time, rpdf)로 top-level 점을 만들어 끼워 넣는 방법. 좌표가 마지막
자식과 **완전히 동일**해 마커가 겹쳐 그려지고, 이 파일에는 이미 "중복 점을 만들지
않는다"는 명시적 방침이 있다 (`method_mean_scatter.py:250-256`). 데이터에 없는 점을
표시 계층이 지어내는 것이라 SSOT 원칙에도 어긋난다.

### 기각 (증상 C) — 마지막 자식을 top-level 마커로 렌더

`is_top_level`을 마지막 자식에 True로 주는 표시 전용 우회. 런 산출물을 전혀 바꾸지
않아 위험은 가장 낮지만, 범례("open circle = top-level subroutine")와 어긋나고
다른 차트에는 적용되지 않는다. **C3의 부작용(리포트 행 증가)이 수용 불가로
판명될 경우의 대안**으로만 남겨 둔다.

### 기각 — hover label을 `jdNNN`으로 축약

`_HTML_TEMPLATE`의 hovertemplate은 `method=%{customdata[1]}`이고 customdata[1]이
곧 `label`이다 (`method_mean_scatter.py:384`, `:403`). `method`(base name) 필드는
HTML 어디에도 출력되지 않으므로, label을 `jd006`으로 줄이면 hover에서 어느 step인지
사라진다. 정규화 후 label은 `incremental_job_contrib_cp.jd006`으로 이미 충분히
짧다 → **축약하지 않는다.**

## 5. 영향 범위

### C1·C2 (label 정규화) — `subroutine_name`을 소비하는 산출물 전부

| 산출물 | 변화 |
|---|---|
| `<scenario>_method_mean_scatter.html`, run-level `multi_scenario_method_mean_…_scatter.html` | jd 레벨당 1점. 인스턴스 간 step key 정렬 복구 (증상 A+B 해소) |
| `<scenario>_subroutine_rpdf_scatter.html` | 마커는 row 단위라 **점 개수 불변**. hover text와 mean vertical guide가 jd 레벨당 1개로 병합 |
| `multi_scenario_subroutine_flow_comparison.html` | `subroutine_order`가 인스턴스/시나리오 간 일관되게 매겨지고, `groupby("subroutine_name")` 평균이 jd 레벨당 1점 |
| `csr_inner_flow_comparison.html` | 영향 없음 (CSR inner에 job_contrib 없음) |
| `scripts/build_cross_run_flow_chart.py` 산출물 | flow comparison과 동일하게 적용 |
| `_step_log.yaml` / gantt SVG / `_incremental_job_contrib_cp_progress.json` 파일명 | **불변** (call_context 기반, 이번 변경과 무관) |

`prefixed_subroutine_name`에 원본 label이 남으므로 DataFrame 단계에서의 정보
손실은 없다 (현재 이 컬럼을 읽는 차트는 없다).

### C3 (composite endpoint 등록) — 새 런에만 적용

C1·C2와 달리 **표시 계층이 아니라 런 산출물 자체가 바뀐다.**

| 산출물 | 변화 |
|---|---|
| `<instance>_obj_log.json` | bare label `<idx>-incremental_job_contrib_cp` endpoint 1개 추가 |
| method-mean scatter | flow의 마지막이 open circle로 닫힘 (증상 C 해소) |
| flow comparison / rpdf scatter | job_contrib의 composite endpoint가 step 하나로 추가 (마지막 jd 직후, 거의 같은 시각) |
| `*_report.xlsx` step 행 | 인스턴스당 1행 증가 |
| `work_status` | 값 불변 (`feasible`) — composite 엔트리에서 읽히지만 incumbent를 함께 등록하므로 |
| **과거 런** | **소급 적용 안 됨** — obj_log에 없는 점이므로 재생성해도 생기지 않는다 |

## 6. 알려진 한계 — n이 섞인 런에서는 jd 값이 인스턴스마다 다르다

`metadata/20260728/incremental_job_contrib_cp_resume.yaml`은 `jd_start: "0.2n"`,
`jd_end: "0.4n"`이고 `resolve_jd_count_target`이 n에 비례해 해석한다. 따라서
**n이 서로 다른 인스턴스가 섞인 런에서는** n=20 → `jd004…jd008`,
n=50 → `jd010…jd020` 처럼 정규화 후에도 label이 갈린다.

- 이번 파일럿(n=50 고정)에서는 무해하다.
- 대규모 그리드로 확장할 때 이 파편화가 문제가 되면, key를 jd **값** 대신 jd
  **레벨 서수**(`jd_step_01`, `jd_step_02`, …)로 바꾸는 후속 변경이 필요하다.
  이번 변경 범위에는 넣지 않는다 (YAGNI — 현재 런에서 관측되지 않음).

## 7. 검증

### 7.1 C1·C2 (구현 완료 — 2026-07-28)

테스트 픽스처는 **raw obj_log label**(`incremental_job_contrib_cp.3-jd006_r001`)을
넣어야 한다. 이미 정규화된 이름을 넣으면 정규화 코드를 타지 않아 변경 전에도
통과하는 무의미한 테스트가 된다 (`_seg` 헬퍼가 `_normalize_subroutine_name`을
거치도록 해 둘 것). 각 테스트는 정규화를 identity로 되돌린 상태에서 **red임을
한 번 확인**한다.

1. **신규 unit test — `tests/report/test_obj_log_loader.py`**
   `_normalize_subroutine_name` 케이스:
   - `"incremental_job_contrib_cp.3-jd006_r001"` → `"incremental_job_contrib_cp.jd006"`
   - `"incremental_job_contrib_cp.4-jd006_r002"` → 동일 (병합 확인)
   - `"incremental_job_contrib_cp.12-jd006_r1000"` → 동일 (rep ≥ 1000)
   - `"coarsen_solve_reconstruct-5-incremental_job_contrib_cp.2-jd010_r001"`
     → `"coarsen_solve_reconstruct-5-incremental_job_contrib_cp.jd010"` (CSR 중첩)
   - `"incremental_sw_cp.1-batch_002"` → **불변**
   - `"coarsen_solve_reconstruct-1-calc_mcf_lb_and_derive_full_sch"` → **불변**
   - `"neh_cp"` → **불변**
2. **신규 loader-level test** — note 두 개(`3-…_r001`, `4-…_r002`)를 가진 obj_log에서
   두 `CallSegment`의 `subroutine_name`이 같고 `prefixed_subroutine_name`은 서로
   다른 원본 label임을 확인.
3. **신규 집계 test — `tests/report/test_method_mean_scatter.py`**
   - 같은 jd에서 rep 2회 → point 1개, `mean_time_pct`는 **마지막 rep**의 시간.
   - 증상 B 회귀 가드: 인스턴스 A가 `3-jd006_r001, 4-jd006_r002, 5-jd007_r001`,
     인스턴스 B가 `3-jd006_r001, 4-jd007_r001`일 때 jd007이 **하나의 point로
     병합되고 instance_count가 2**임을 확인.
4. **기존 회귀** — 특히 label 문자열을 정확 비교하는
   `tests/report/test_method_mean_scatter.py:68,104-111,342`,
   `tests/report/test_obj_log_loader.py`(`subroutine_name == "step_a"` 등)가
   그대로 통과해야 한다 (모두 정규화 대상이 아님). `uv run pytest tests/`.
5. **차트 재생성**:
   ```sh
   uv run python scripts/build_subroutine_flow_charts.py \
     output/20260728_incremental_job_contrib_cp_resume/20260728T165441_559062
   ```
   확인 항목:
   - method-mean scatter: jd 레벨당 1점, hover가 `incremental_job_contrib_cp.jdNNN`
   - flow comparison: job_contrib 구간 점 개수 감소, 시나리오 간 step 정렬 일치
   - rpdf scatter: **마커 개수 불변**, guide label만 병합
   - non-job_contrib 구간(`coarsen_solve_reconstruct`, `incremental_sw_cp`)의 점
     개수·label이 변경 전과 동일
6. `uv run ruff check` 통과.

### 7.2 C3 (구현 완료 — 2026-07-28)

`tests/orchestration/test_incremental_job_contrib_cp.py`. step 메서드를 flow가
아니라 **직접 호출**하는 테스트 하네스에서는 method-context 스택이 비어 있어
step_label이 `ROOT` / `<k>-jdNNN_rMMM` 형태다. 따라서 label 매칭은 outer step
이름이 아니라 inner 호출의 `jd\d+_r\d+$` suffix로 판별한다 (`_is_inner_call_label`).

1. **composite self-registration** (`test_composite_registers_self_endpoint`) —
   마지막 history 엔트리가 composite의 endpoint이고, `solution`이 현재 incumbent,
   `obj_value == best_obj_value`, `obj_bound is None`. 동일 목적값 재등록이
   incumbent 객체를 교체하지 않음도 확인.
2. **work_status 회귀 가드** (`test_composite_endpoint_keeps_work_status`) —
   composite 종료 후에도 `controller.work_status`가 `None`이 아님.
   `solution=None`으로 되돌리면 red가 되는 것을 확인했다.
3. **타임스탬프 충돌 가드**
   (`test_composite_endpoint_does_not_overwrite_last_inner_note`) — 마지막 inner
   호출과 composite endpoint의 `repr(start_time + elapsed_time)`가 서로 다르고,
   `_fold_history_into_obj_log_dicts` 후 각자의 note label이 보존됨. 현재는
   충돌하지 않으므로 이 테스트는 red였던 적이 없다 — 미래 회귀(예: register를
   `finally`로 옮기는 변경)를 막는 불변식 가드다.
4. **조기 종료 경로** (`test_jd_start_ge_n_no_cp_calls`, `test_zero_obj_early_exit`)
   — CP 호출이 없어도 endpoint가 1개 등록되고 `work_status`가 유지됨.
5. **summary 행 불변식** (`test_summary_log_rows_match_iterations`) — composite
   엔트리를 label로 걸러낸 뒤 CP-solve 행 수와 비교 (고정 offset `-1` 금지).
6. **문서** — `src/ffc_ddw_sum_et/orchestration/AGENTS.md`에 "부모 composite과
   inner step이 같은 컨트롤러에 등록하는 경우" + `solution=None` 금지 사유 반영
   (위험 2).
7. **차트 육안 확인 — 완료 (2026-07-28).** 새 런의 method-mean scatter에서
   `incremental_job_contrib_cp` flow가 top-level 마커(open circle)로 닫히는 것을
   확인했다. C3는 소급 적용되지 않으므로 기존 런 재생성이 아니라 새 런으로만
   확인 가능하다.

## 8. 소급 적용 범위

- **C1·C2 — 과거 런 전체에 소급 적용된다.** `_obj_log.json`의 원본 label은
  그대로지만 정규화가 로드 시점에 동작하므로, 차트를 재생성하기만 하면 과거 런도
  새 label 체계를 따른다. 이미 디스크에 있는 HTML은 재생성 전까지 옛 label을 유지.
- **C3 — 소급 적용되지 않는다.** 등록 자체가 없었으므로 과거 런의 obj_log에는
  bare endpoint가 없고, 차트를 재생성해도 동그라미는 생기지 않는다. 새로 도는
  런부터 적용된다.

## 9. 대상 파일

| 파일 | 변경 |
|---|---|
| `src/ffc_ddw_sum_et/report/obj_log_loader.py` | C1 — `_JOB_CONTRIB_REP_RE` + `_normalize_subroutine_name`, `_build_calls_for_series`에서 `subroutine_name`에 적용 / C2 — 모듈 docstring·`CallSegment` 필드 주석 |
| `src/ffc_ddw_sum_et/report/method_mean_scatter.py` | C2 — docstring/주석만 (로직 변경 없음) |
| `tests/report/test_obj_log_loader.py` | 검증 7.1-1·2 |
| `tests/report/test_method_mean_scatter.py` | 검증 7.1-3 (`_seg`가 `_normalize_subroutine_name`을 거치도록 수정 포함) |
| `src/ffc_ddw_sum_et/orchestration/controller.py` | **C3** — `incremental_job_contrib_cp` 루프 종료 직후 `_register` 1회 |
| `src/ffc_ddw_sum_et/orchestration/AGENTS.md` | **C3** — step 계약에 부모/자식 동일 컨트롤러 등록 케이스 명시 |
| `tests/orchestration/test_incremental_job_contrib_cp.py` | **C3** — 검증 7.2 (`_is_inner_call_label` 헬퍼 포함) |

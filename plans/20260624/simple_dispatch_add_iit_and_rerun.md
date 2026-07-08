# simple dispatch에 IIT 추가 + dispatch sweep 재실행

- 작성일: 2026-06-24
- 후속 분석 plan: `plans/20260624/dispatch_init_paper_justification_analysis.md`
  (이 작업이 끝나야 수행 가능)

## 1. 배경 / 문제

`initialize_by_simple_dispatch` (controller.py:1704)는 forward job-centric
decode로 **left-pack만** 하고 `make_semi_active` / `insert_idle_time`을 적용하지
않는다. 반면 reverse 파이프라인 `_dispatch_by_reversed_sequence_with_iit`
(controller.py:1530-1535)은 둘 다 적용한다.

목적함수가 **weighted E+T**이므로 idle-time insertion은 early job을 due window
쪽으로 밀어 earliness penalty를 줄이는 핵심 timing 보정이다. 따라서 현행 sweep의
`sd_` vs `rd_` 비교는 *decode 방향*과 *IIT 유무*가 섞여 있고, 2017 baseline
(simple ∘ {edd,lsl,osl})이 부당하게 약하다.

**결정(확정)**: `initialize_by_simple_dispatch`를 **항상 IIT 포함**으로 교체.
raw left-pack 변형은 보존하지 않는다. (참고: 기존 run
`output/20260624/20260624T153836_407384`는 폐기.)

## 2. 구현

### 2.1 소스 변경 (controller.py)

`initialize_by_simple_dispatch` 본문에서 forward decode 직후, **register 전**에
reverse 파이프라인과 동일한 보정을 적용:

```python
job_sequence = dispatch_seq_job_sequence(self.instance, sequence)
dispatcher = MixedDispatcher(self.instance, logger=self.logger)
schedule = dispatcher.get_job_centric_schedule_by_sequence(job_sequence)
schedule.make_semi_active(self.instance.stage_2_job_2_p_map)
schedule.insert_idle_time(
    self.instance.job_2_due_window_map,
    self.instance.job_2_ewt_map,
    self.instance.job_2_twt_map,
)
sum_e, sum_t = compute_weighted_earliness_tardiness(schedule, self.instance)
obj_value = float(sum_e + sum_t)
```

- **Subroutine step contract 준수**(CLAUDE.md): 추가 작업은 모두
  `elapsed = time.monotonic() - start_elapsed` **이전**에 둔다(= step의 실제
  작업). `self._register(...)`는 그대로 **1회**.
- docstring 갱신: "stage 역전·np-sweep·idle-time 삽입이 없는…" 문장을 "forward
  job-centric decode 후 make_semi_active + insert_idle_time으로 E/T timing
  보정" 으로 수정. reverse와 동일한 보정을 공유함을 명시.

### 2.2 TDD

1. 기존 simple-dispatch 테스트 위치 확인
   (`tests/` 내 `initialize_by_simple_dispatch` / `get_job_centric_schedule_by_sequence`
   참조 테스트).
2. **red**: 새 회귀 테스트 추가 — 동일 sequence에 대해 "IIT 적용 후 weighted
   E+T ≤ raw left-pack의 weighted E+T" (개선/동률) 그리고 idle이 실제로 삽입되어
   start time이 right-shift됨을 검증. 변경 전이면 실패해야 함.
3. **green**: 2.1 적용 후 통과.
4. 기존 테스트가 raw 결과를 고정값(golden)으로 검증하고 있으면 새 동작에 맞게
   갱신(이유 주석).

### 2.3 정리

- `uv run ruff check`
- `uv run ruff format`
- `uv run pytest`(관련 테스트) 통과 확인.

## 3. sweep 재실행

- config: `metadata/20260624/dispatch_sequence_full_sweep_config.yaml`
  (22 scenario = 11 priority × `sd_`/`rd_`, large 전체, `instance_worker_cnt: 96`).
- 실행: 프로젝트 표준 진입점으로 FULL_RUN (예: `uv run python -m ... <config>` —
  기존 run을 만든 동일 경로). 새 timestamp run이 `output/20260624/` 아래 생성됨.
- 산출 검증(분석 전 필수):
  - 새 run의 `*_rpdf_comparison.csv`에서 **22 scenario × 1440 instance = 31,680
    행, `bestObj`/`RPDf_BKS_data` 결측 0** 확인.
  - `sd_` scenario 표본의 obj가 기존(폐기) run의 `sd_` 대비 **개선**되었는지
    sanity check(IIT 효과 확인).

## 4. 완료 기준 (Definition of Done)

- [ ] `initialize_by_simple_dispatch`가 make_semi_active + insert_idle_time 적용.
- [ ] step contract(register 1회, elapsed 측정 위치) 유지.
- [ ] 회귀 테스트 red→green, 전체 관련 테스트 통과.
- [ ] ruff check/format 통과.
- [ ] 새 sweep run 생성 + comparison CSV 무결성(31,680행, 결측 0) 검증.
- [ ] 새 run 경로를 후속 분석 plan에 기록.

## 5. 잔여 차이(비-IIT) 명시 — 분석/논문에서 다룰 것

IIT를 양쪽에 맞춰도 reverse 파이프라인은 np 후보(machine_then_job True/False)를
makespan 기준으로 골라 best를 취하지만 simple은 단일 job-centric pass다. 이는
"simple vs reverse pipeline"이라는 두 *decode 전략*의 본질적 차이이므로 그대로
두되, 논문/분석에서 한 줄로 명시한다. (후속 분석 plan Track A/C에서 인용.)

# NEH-CP `midpoint` tie-break 규칙 비교 (1440 그리드)

작성일: 2026-08-03 / 대상 브랜치: `20260731_neh_cp`

이 문서는 **코드 변경 + 실험 실행 계획**이다. 별도 대화에서 이 문서만 읽고 구현·실행
할 수 있도록 현재 상태·대수적 사전 분석·설계 결정·작업 순서·분석 계획을 모두 담는다.

**선행 문서 (읽는 순서대로)**

- `plans/experiment/20260731/neh_cp_incumbent_sequence.md` — 순서 유도 구현 계획
- `plans/analysis/20260801/neh_cp_seq_source_full.md` — 1440 그리드 모드 비교
  (**결과 0**: flow `bestObj`는 순서 효과를 지운다 → 측정면은 NEH 스텝 자체 산출물)
- `plans/analysis/20260801/neh_cp_seq_replicate.md` — replicate 쌍, run-to-run 노이즈
- `plans/analysis/20260802/neh_cp_budget_allocation.md` — **조치 4**: 모든 비교는 T별로 읽는다

---

## 1. 질문

선행 1440 그리드 분석의 모드 순위는 `completion` < `midpoint` < `first_stage`이고
(NEH 스텝 기준, 2 run 6/6 재현), 앞의 둘은 flow 기준으로 구분되지 않는다. 여기서
자연스럽게 나오는 질문이 **"1차 정렬 키가 동률일 때 무엇으로 가르는가가 결과를
움직이는가"**다.

이 run이 답하는 것은 정확히 두 가지다.

1. **`midpoint` 모드의 tie-break을 `first_stage`에서 `completion`으로 바꾸면
   NEH-CP 산출물이 움직이는가?** (동일 run 내 paired 대비)
2. **incumbent 유도 순서가 `job_priority` 순서보다 나은가 — 동일 seeding prefix,
   동일 예산에서?** 파일럿(`neh_cp_seq_source_pilot.md` "알려진 교란")이
   "이 런으로 답할 수 없다"고 남긴 항목이다. 선행 1440 분석 결과 3이 간접적으로
   답했지만, 그때 baseline은 **seeding prefix가 아예 없는** 시나리오였다. 이 run은
   prefix를 붙인 통제군을 직접 둔다.

---

## 2. 사전 확인 — 요청된 세 변형 중 둘은 대수적으로 축퇴한다 (먼저 읽을 것)

최초 요청은 세 변형(`completion2` / `midpoint2` / `first_stage2`)이었다. 코드를 읽고
정렬 키의 대수를 확인한 결과 **둘은 기존 모드와 순서가 완전히 동일**해서 arm에서
뺐다. 그 근거를 남긴다 — 나중에 같은 아이디어가 다시 제안될 때 재유도하지 않기 위해서다.

현재 `schedule_job_sequence` (`src/ffc_ddw_sum_et/solution/schedule_sequence.py:69`)의
정렬 키. `fs` = 첫 stage 시작, `ls` = 마지막 stage 종료, `m = (fs+ls)/2`,
`rank` = `job_priority` 순위:

| source | 1차 | 2차 | 3차 |
|---|---|---|---|
| `midpoint` | `m` | `fs` | rank |
| `first_stage` | `fs` | `ls` | rank |
| `completion` | `ls` | `fs` | rank |

즉 **세 모드 모두 이미 시간 기반 2차 키를 갖고 있다.** 요청은 그 2차 키를
"다른 모드의 1차 키"로 바꾸는 것이었는데:

- **`first_stage2` ≡ `first_stage`**: `fs`가 동률인 그룹 안에서 `m = (fs+ls)/2`는
  `ls`의 순증가 함수다 (`fs` 고정). 현재 2차 키가 이미 `ls`이므로 **순서가 동일**하다.
- **`completion2` ≡ `completion`**: `ls`가 동률인 그룹 안에서 `m`은 `fs`의 순증가
  함수다. 현재 2차 키가 이미 `fs`이므로 **순서가 동일**하다.
- **`midpoint2` ≠ `midpoint`**: `m`이 동률인 그룹 안에서는 `ls = 2m − fs`이므로
  `ls` 오름차순은 `fs` 오름차순의 **정확한 역순**이다. 따라서 `midpoint2`는
  `midpoint`의 **동률군만 뒤집은** 순서다. 세 요청 중 유일하게 새로운 순서다.

(부동소수 주의: `m`은 `(fs+ls)/2.0`이고 2로 나누는 것은 이진 부동소수에서 정확하므로
위 단조성은 실제 구현에서도 예외 없이 성립한다. §5 테스트로 못 박는다.)

**동률이 실제로 얼마나 생기는가** — run A(`20260801T012922_726471`)의
`dv4_mcf_fmm_neh_cp_completion_seq` 최종 스케줄 1440개 중 60개를 무작위 표본
(seed 0)해 job별 키 값의 동률을 셌다:

| 키 | 동률에 속한 job 비율 | 동률이 1개 이상인 인스턴스 |
|---|---|---|
| `fs` (first_stage) | 9.88 % (746/7550) | 60 / 60 |
| `m` (midpoint) | **3.55 % (268/7550)** | **45 / 60** |
| `ls` (completion) | 4.87 % (368/7550) | 52 / 60 |

→ `midpoint2`는 인스턴스의 약 3/4에서 `midpoint`와 다른 순서를 내지만, 움직이는
것은 job의 3.55 %뿐인 **작은 섭동**이다. 효과가 있다면 작을 것으로 예상하고 설계한다
(§4의 검정력 참고).

**같은 종류의 발견 전례**: `bottleneck` 모드가 `first_stage`의 구조적 별칭이었던 건
(`plans/analysis/20260731/neh_cp_seq_source_pilot.md` 결과 2). 그때는 파일럿 런을
태우고서야 알았고, 이번에는 실행 전에 걸렀다.

---

## 3. 설계

### 3.1 flow (세 arm 공통)

```
initialize_by_dispatch_v4 -> calc_mcf_lb_and_derive_full_sch
                          -> run_flip_makespan_cp_from_incumbent -> neh_cp*
```

즉 **full sequence에서 `incremental_sw_cp`와 `solve_base_model_cpsat`를 뺀 것**이다.
run A/B의 `dv4_mcf_fmm_*` 계열과 **완전히 같은 flow·같은 파라미터**이므로 그
계열이 그대로 교차 run 대조군이 된다 (§6.2).

꼬리를 뺀 것은 이 실험에 유리하다 — `plans/analysis/20260802/neh_cp_budget_allocation.md`
결과 3이 보였듯 `incremental_sw_cp`는 NEH 수준 격차의 **97.9 %를 흡수**한다.
꼬리가 없으면 NEH 수준 효과가 flow 수준까지 남는다.

> 다만 꼬리를 빼도 **flow `bestObj`는 여전히 신뢰할 수 없다.** flow 최종값은
> `min(seed, NEH)`이고, NEH가 seed를 못 이긴 인스턴스(선행 분석 기준 31.5–34.9 %)
> 에서는 모든 arm이 **같은 숫자**를 보고한다. 측정면은 §6.1대로 NEH 스텝 자체
> 산출물이다.

### 3.2 arm 구성 (3개)

| # | 시나리오 이름 | NEH 스텝 | 정렬 키 | 역할 |
|---|---|---|---|---|
| 1 | `dv4_mcf_fmm_neh_cp_midpoint2_seq` | `neh_cp_midpoint_seq`, `seq_tiebreak: completion` | `m` → `ls` → rank | **처치군** |
| 2 | `dv4_mcf_fmm_neh_cp_midpoint_seq` | `neh_cp_midpoint_seq` (기본) | `m` → `fs` → rank | **동일-run 통제군** (질문 1) |
| 3 | `dv4_mcf_fmm_neh_cp_job_priority` | `neh_cp`, `job_priority: due2-weight-pos` | 인스턴스 규칙 | **통제군** (질문 2) |

- **arm 1 − arm 2**가 tie-break 효과를 정확히 격리한다. 두 arm은 seeding prefix·
  예산·NEH 파라미터가 전부 같고 **동률군의 순서만** 다르다. 같은 run 안에 두는 것이
  핵심이다 — 교차 run으로 재면 run-to-run 노이즈(1440 평균 SE 0.230 pp,
  `neh_cp_seq_replicate.md` 결과 2)가 섭동 크기와 같은 자릿수라 답이 나오지 않는다.
- **arm 3**은 최초 요청의 "원래대로 due2-weight-pos" 시나리오다. arm 1·2와 prefix·
  예산이 같으므로, 선행 분석 결과 3이 갖고 있던 "baseline에는 prefix가 없다"는
  교란 없이 유도 순서 대 인스턴스 규칙을 잰다.
- arm 3의 NEH 스텝은 `neh_cp`(incumbent를 읽지 않는 기존 스텝)다. prefix는 그대로
  실행되므로 **seed와 예산은 arm 1·2와 동일**하고, 차이는 삽입 순서의 출처뿐이다.

### 3.3 예산

run A/B와 **동일하게 고정**한다 (교차 비교 가능성을 지키기 위해서다):

| 항목 | 값 |
|---|---|
| 시나리오 cap | `0.09nc` (선행 run에서 한 번도 바인딩되지 않음, 최대 사용률 24.8 %) |
| FMM `cp_tl` | `0.0036nc` |
| NEH `total_timelimit` | `0.0108nc`, `added_batch_size: 15`, `batch_tl_mode: linear` |
| `instance_worker_cnt` / `painter_thread_cnt` | 12 / 96 |

**예상 소요**: run A의 `dv4->mcf_lb->fmm` 계열 평균 15.21 s/인스턴스 기준
3 × 1440 × 15.21 / 12 ≈ **91분** + 리포트 생성. 96코어 단독 점유 전제
(메모리: machine core count).

---

## 4. 검정력 — 이 run이 무엇을 말할 수 있는가

`neh_cp_seq_replicate.md`가 실측한 값으로 미리 계산해 둔다. 결과가 나온 뒤
"유의하지 않다"를 해석할 때 필요하다.

| 지표 | 값 | 출처 |
|---|---|---|
| NEH 스텝 기준 per-instance 노이즈 sd | 12.36 pp | 결과 1 |
| paired 대비의 표준오차 (1440) | ≈ 12.36/√1440 ≈ **0.33 pp** | 파생 |
| 검출 가능한 최소 효과 (2 σ) | **≈ 0.65 pp** | 파생 |
| 참고: 모드 간 실효과 (`completion`−`midpoint`) | 2.21 pp (6.9 σ) | 결과 3 |

즉 **tie-break 효과가 0.65 pp 이상이면 검출되고, 그 아래면 "0.65 pp보다 작다"까지만
말할 수 있다.** job의 3.55 %만 움직이는 섭동임을 감안하면 후자가 될 가능성이 높고,
그 경우의 결론은 "tie-break 규칙은 모드 선택(2.21 pp)보다 최소 3배 이상 작은
레버"라는 **상한**이다. 이것은 유용한 음성 결과이며, `TODO.md`에 남길 값어치가 있다.

---

## 5. 코드 변경

### 5.1 `solution/schedule_sequence.py` — tie-break 키를 선택 가능하게

현재 각 `source`의 2차 키는 `if/elif` 안에 하드코딩돼 있다. 1차 키를 뽑는 함수를
표로 빼고, 2차 키를 인자로 받는다.

```python
_KEY_FN: dict[str, Callable[[float, float], float]] = {
    "midpoint": lambda fs, ls: (fs + ls) / 2.0,
    "first_stage": lambda fs, ls: fs,
    "completion": lambda fs, ls: ls,
}
_DEFAULT_TIEBREAK: dict[str, ScheduleSeqSource] = {
    "midpoint": "first_stage",
    "first_stage": "completion",
    "completion": "first_stage",
}

def schedule_job_sequence(
    schedule: FFcSchedule,
    source: ScheduleSeqSource,
    *,
    tiebreak_source: ScheduleSeqSource | None = None,
    tiebreak_rank: Mapping[str, int] | None = None,
) -> list[str]: ...
```

- `tiebreak_source=None` → `_DEFAULT_TIEBREAK[source]` → **기존 동작과 바이트 동일**.
- `bottleneck`은 기존 분기를 그대로 둔다 (2차 키 `bn_mid`가 다른 어휘다).
  `source == "bottleneck"`에 `tiebreak_source`가 주어지면 `ValueError`.
- `tiebreak_source == source`도 `ValueError` (2차 키가 무의미).

### 5.2 `orchestration/controller.py` — `neh_cp_midpoint_seq`에만 노출

`seq_tiebreak: ScheduleSeqSource | None = None` 파라미터를 **`neh_cp_midpoint_seq`
하나에만** 추가하고, `_run_neh_cp`에 keyword-only로 전달한다.

> **왜 세 스텝 전부가 아닌가.** §2의 대수 때문이다. `completion`의 tie-break
> 후보는 `first_stage`(= 현재 기본값)와 `midpoint`(≡ `first_stage`)뿐이라
> **구분되는 설정값이 존재하지 않는다.** `first_stage`도 같다(`completion` /
> `midpoint` ≡ `completion`). 즉 이 파라미터가 의미를 갖는 source는 `midpoint`
> 하나뿐이므로, 나머지에 노출하면 "설정할 수는 있으나 아무것도 바꾸지 않는"
> 죽은 손잡이가 된다. 4차원 이상의 키가 생기면 그때 다시 본다 (§9).

`_run_neh_cp`는 받은 값을 `schedule_job_sequence(..., tiebreak_source=seq_tiebreak)`
로 넘긴다. 추가로:

- 진단 로그 한 줄(`controller.py:2616`)에 `tiebreak=%s`를 붙인다.
- `_step_log.yaml` 매핑에 `job_sequence_tiebreak` 키를 추가한다
  (`job_sequence_source` 옆). fallback 시에는 `null`.

**스텝 계약 준수** (`src/ffc_ddw_sum_et/orchestration/AGENTS.md`): 순서 유도는 전부
`elapsed` 측정 이전에 일어나고 `_register`는 호출당 1회 — 변경 없음.

### 5.3 작업 순서 (TDD)

각 단계는 "실패하는 테스트 → 최소 구현 → green". 매 단계 후 `uv run ruff check`,
마지막에 `uv run ruff format`.

**단계 1 — `tiebreak_source` 파라미터** (`tests/solution/test_schedule_sequence.py`)

1. `tiebreak_source="completion"`을 준 `midpoint`가, `m`이 동률인 그룹을
   기존(`fs` 오름차순)의 **역순**으로 낸다 — 손으로 만든 동률 케이스.
2. **별칭 회귀 테스트 (§2를 코드로 못 박는다)**: 무작위 시각으로 만든 스케줄
   여러 개에서
   - `completion` + `tiebreak_source="midpoint"` == `completion` 기본,
   - `first_stage` + `tiebreak_source="midpoint"` == `first_stage` 기본.
   이 테스트가 깨지면 §2의 대수가 더 이상 성립하지 않는다는 뜻이고, 그때는 이
   문서와 분석 문서의 해석을 함께 고쳐야 한다.
3. `tiebreak_source=None`이 기존 출력과 동일 (회귀).
4. `source="bottleneck"` + `tiebreak_source` → `ValueError`.
   `tiebreak_source == source` → `ValueError`.

**단계 2 — 컨트롤러 배선** (`tests/orchestration/test_neh_cp_incumbent_sequence.py`)

1. `seq_tiebreak="completion"`을 준 `neh_cp_midpoint_seq`가 기대 순서를
   `NehCpOption.custom_job_sequence`에 실어 dispatcher를 호출한다
   (`NehCpDispatcher.run` monkeypatch, 기존 테스트 스타일).
2. `seq_tiebreak`를 담은 최소 `subroutine_flow`가 routix
   `SubroutineFlowValidator`를 통과한다.
3. `seq_tiebreak=None`이면 기존과 동일한 순서 (회귀).
4. incumbent 부재 시 `seq_tiebreak`가 있어도 `job_priority` fallback + warning.

**단계 3 — 문서**

- `docs/algorithms/neh_cp.md`: `seq_tiebreak` 파라미터와 "`midpoint`에만 노출하는
  이유"(§2 대수)를 적는다.
- `README.md` 스텝 표: 새 스텝이 아니므로 행 추가 없음.

**단계 4 — config** (§6)

---

## 6. 실험 config 및 실행

### 6.1 config

`metadata/20260803/neh_cp_midpoint_tiebreak.yaml` (신규):

```yaml
run_mode: FULL_RUN

benchmark_dir: benchmarks/PRA2017/large
ins_index_source: benchmarks/PRA2017/pra2017_hybrid_match.csv
bks_table_csv_path: benchmarks/PRA2017/pra2017_bks_table.csv

output_dir: output/20260803_neh_cp_midpoint_tiebreak

instance_worker_cnt: 12
draw_gantt: false
draw_progress_plot: true
painter_thread_cnt: 96

scenarios:
  # 헤더 주석: 3 arm, flow는 dv4 -> MCF-LB -> FMM -> NEH-CP (ISW-CP / base CP 없음),
  # 계획 문서 경로, §2의 별칭 축퇴 사유를 요약해 남긴다.
  - name: dv4_mcf_fmm_neh_cp_midpoint2_seq   # 처치군
      ... neh_cp_midpoint_seq + seq_tiebreak: "completion"
  - name: dv4_mcf_fmm_neh_cp_midpoint_seq    # 동일-run 통제군
      ... neh_cp_midpoint_seq (기본)
  - name: dv4_mcf_fmm_neh_cp_job_priority    # job_priority 통제군
      ... neh_cp (job_priority: "due2-weight-pos")
```

세 arm의 prefix 3스텝과 NEH 파라미터는 `metadata/20260731/neh_cp_seq_source_compare.yaml`
의 `dv4_mcf_fmm_neh_cp_midpoint_seq`(:198)에서 **그대로 복사**한다 — 한 글자라도
달라지면 run A/B와의 교차 비교(§7.2)가 무효가 된다.

### 6.2 실행

```bash
uv run python main.py --config metadata/20260803/neh_cp_midpoint_tiebreak.yaml
```

> **`--config`를 반드시 명시할 것.** `main.py:31`의 `CONFIG_PATH`는 하드코딩된
> 기본값(현재 `metadata/20260801/neh_cp_budget_allocation.yaml`)이다. run
> `20260801T102120_801587`은 이 기본값을 잊어서 의도와 다른 config가 돌아간 사고다
> (`neh_cp_seq_replicate.md` "소스 run" 참조).

실행 전 확인:

1. 96코어를 단독 점유하는가 — 다른 실험/고아 워커가 없는지 확인
   (run A의 폐기 이력이 경합 때문이었다).
2. 실행 후 `main.log`에서 **fallback warning 0건 / permutation 보정 warning 0건**
   확인. 1건이라도 있으면 그 인스턴스는 순서 비교에서 빠져야 한다.
3. 시나리오별 NEH 스텝 소요가 세 arm에서 균질한지 (노력 균질성 확인, 선행 분석
   결과 1과 같은 검사).

### 6.3 provenance 커밋

CLAUDE.md의 **run setting** 규약대로, 런 디렉터리가 생긴 뒤 config 스냅샷을 커밋한다:

```
20260803_neh_cp_midpoint_tiebreak/<timestamp> run setting
computer: calop4

- question: does the midpoint mode's tie-break key move the NEH-CP output, and is a derived order better than job_priority at equal prefix and budget
- 3 scenarios x 1440 PRA2017 large instances; flow is dispatch_v4 -> mcf_lb -> fmm -> neh_cp with no ISW-CP / base CP tail
- ...
```

---

## 7. 분석 계획

런이 끝난 뒤 `plans/analysis/20260803/neh_cp_midpoint_tiebreak.md`를 SSOT로 쓰고,
`analysis/20260803_neh_cp_midpoint_tiebreak/`에 CSV를 낸다 (gitignore 대상).

### 7.1 측정면 — NEH 스텝 자체 산출물

**flow `bestObj`로 결론을 내지 않는다** (`neh_cp_seq_source_full.md` 결과 0).
스크립트 `scripts/20260803/analyze_neh_midpoint_tiebreak.py`(신규)는
`ffc_ddw_sum_et.report.obj_log_loader.build_step_registrations`로 인스턴스별
`*_obj_log.json`을 스텝 경계로 쪼갠다. 파싱 전에 **`docs/artifacts/obj_log.md`를
읽을 것** — 스키마·리더 선택·세 가지 함정(로더가 note 없는 LB 점을 떨어뜨리는 건
포함)을 그 문서가 소유한다.

지표 3종 (`analyze_neh_step_quality.py`와 동일 정의):

- `seed_obj` — NEH 진입 시 incumbent (FMM 출력)
- `neh_obj` — NEH 스텝 **자체 출력** (`StepRegistration.own_obj`)
- `neh_best` — NEH 블록을 나갈 때의 incumbent (`.incumbent`) = 꼬리가 물려받는 값
- `flow_best` — 참고용

RPDf는 `ffc_ddw_sum_et._calc.rpd_f`를 **import 해서** 쓴다 (손으로 쓴
`2(obj−ref)/(obj+ref)`는 무비용 인스턴스에서 0/0이 되어 시나리오당 ~57개를 조용히
떨어뜨린다).

### 7.2 대비

| 대비 | 격리하는 것 | 기대 |
|---|---|---|
| arm1 − arm2 (paired, 1440) | **tie-break 키** | §4대로 \|효과\| < 0.65 pp면 상한 결론 |
| arm1 − arm3, arm2 − arm3 (paired) | **유도 순서 대 `job_priority`** (동일 prefix·예산) | 선행 결과 3은 −9.25 pp (prefix 없는 baseline 대비) |
| arm2 vs run A/B의 `dv4_mcf_fmm_neh_cp_midpoint_seq` | **run-to-run 재현성** | 1440 평균이 ±0.451 pp 밴드 안에 들어와야 함 |

세 번째 줄이 이 run의 무료 위생 검사다. arm 2는 run A/B의 같은 이름 시나리오와
**설정이 동일한 세 번째 replicate**이므로, 밴드를 벗어나면 이 run의 환경(경합, 코어
점유)을 먼저 의심해야 한다. run A/B의 스텝 경계 CSV는
`analysis/20260801_neh_cp_seq_full/step_objectives.csv`에 있고(run B 것은
`analysis/20260801_neh_cp_seq_full_runB/`에 없으므로
`scripts/20260801/analyze_neh_step_quality.py --out-dir`로 재생성한다).

### 7.3 T별 분해 (필수)

`plans/analysis/20260802/neh_cp_budget_allocation.md` **조치 4**: 통합 평균이
T=0.2와 T=0.6의 반대 부호 효과를 상쇄해 실효과를 완전히 지운 전례가 있다.
`--t {0.2,0.4,0.6}` 플래그를 스크립트에 넣고 **네 벌 모두 돌린다**. 결론은
T=0.6 슬라이스를 우선해서 읽는다 (그 슬라이스만 척도가 포화되지 않고 cap이
480개 전부에서 구속된다).

(T,R)·(n,c) 셀 분해도 함께 낸다 — 인스턴스 파라미터 해석은
`pra2017-instance-params` 스킬을 먼저 읽는다.

### 7.4 보조 지표

- `dist_to_job_priority`, `dist_to_midpoint` 등 컨트롤러 진단 라인 파싱 —
  arm 1과 arm 2의 유도 순서가 실제로 얼마나 떨어져 있는지
  (`normalized_mean_rank_distance`). §2의 예측(job의 3.55 %만 이동)을 실측으로 확인.
- `_step_log.yaml`의 `job_sequence` / `job_sequence_tiebreak` — arm 1이 정말
  `completion` tie-break을 썼는지 배선 확인.

---

## 8. 커밋 계획 (Conventional Commits, 제목 ≤49자)

계획서 자신이 첫 커밋이다.

1. `docs(plan): add neh_cp midpoint tiebreak plan`
2. `feat(solution): make tie-break key selectable`
3. `feat(controller): expose seq_tiebreak on neh_cp`
4. `docs(neh-cp): document the seq_tiebreak parameter`
5. `feat(neh-cp): add midpoint tiebreak config`
6. (런 후) `20260803_neh_cp_midpoint_tiebreak/<ts> run setting` — provenance
7. (분석 후) `analysis/20260803_neh_cp_midpoint_tiebreak merged analysis` — provenance

각 커밋 시점에 테스트가 green이므로 bisect가 가능하다.

---

## 9. 범위 밖 / 후속

- **`completion2` / `first_stage2`**: §2대로 기존 모드의 별칭이므로 만들지 않는다.
  단계 1의 별칭 회귀 테스트가 이 판단을 코드로 고정한다.
- **`seq_tiebreak`를 다른 source에 노출**: 지금은 죽은 손잡이다(§5.2). 시간 기반
  키가 4개 이상이 되면(예: flow time `ls − fs`, slack `d − ls`) `completion` /
  `first_stage`에도 구분되는 설정값이 생기므로 그때 다시 본다.
- **α-족으로의 일반화**: 세 모드는 사실 `key = α·fs + (1−α)·ls` 한 족이다
  (`first_stage` α=1, `midpoint` α=0.5, `completion` α=0). 선행 분석의
  `completion` < `midpoint` < `first_stage` 순위는 **α가 낮을수록 좋다**는 뜻이고,
  α<0 외삽(`ls + β·(ls − fs)`, 즉 완료시각 기준에 flow time을 가산)은 아직 아무도
  재보지 않았다. 이번 run은 tie-break(2차 키) 질문에만 답하므로 **범위 밖**이다.
  결과가 "tie-break은 레버가 아니다"로 나오면 다음 실험 1순위 후보다.
- 결과가 나오면 `TODO.md`에 "tie-break 규칙은 상한 X pp의 레버"를 사유와 함께
  남겨, 같은 아이디어가 다시 제안될 때 재실험하지 않도록 한다.

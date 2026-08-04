# NEH-CP 삽입 순서: `(last-1)` stage 종료시각 기반 키 2종 (`midpoint3` / `completion3`)

작성일: 2026-08-04 / 대상 브랜치: `20260731_neh_cp`

이 문서는 **코드 변경 + 실험 실행 계획**이다. 별도 대화에서 이 문서만 읽고 구현·실행
할 수 있도록 현재 상태·사전 확인·설계 결정·작업 순서·분석 계획을 모두 담는다.

**선행 문서 (읽는 순서대로)**

- `plans/experiment/20260731/neh_cp_incumbent_sequence.md` — 순서 유도 구현 계획
- `plans/experiment/20260803/neh_cp_midpoint_tiebreak.md` — tie-break 키 선택 가능화,
  **§2의 축퇴 대수** (같은 종류의 사전 확인)
- `plans/analysis/20260804/neh_cp_seq_tiebreak_merge.md` — 6종 통합 리포트(잠정)
- `plans/analysis/20260801/neh_cp_seq_replicate.md` — run-to-run 노이즈 밴드

**형제 문서**: `plans/experiment/20260804/job_batch_cp.md` — 같은 순서 어휘를 쓰는
새 알고리즘. 여기서 추가하는 `seq_end_stage`를 그대로 재사용한다.

---

## 1. 질문

지금까지의 순서 유도 키는 모두 **마지막 stage 종료시각 `ls`** 를 축으로 삼는다
(`midpoint = (fs+ls)/2`, `completion = ls`, `first_stage`의 2차 키 = `ls`).

그런데 이 저장소의 후처리는 **마지막 stage만 다르게 취급한다**:

- `FFcSchedule.make_semi_active` (`solution/ffc_schedule.py:1032`) — 전 stage를
  좌측 정렬(semi-active)한다.
- `FFcSchedule.insert_idle_time` (`:1656`) — **마지막 stage에만** 유휴시간을
  삽입해 각 job의 완료시각을 due window 쪽으로 우측 이동시킨다
  (`last_stage_id = self.stages[-1]`, `:1681`).

즉 첫~(last-1) stage는 **자원 경합이 결정한 위치**에 있고, 마지막 stage의 종료시각
`ls`는 거기에 **due window가 끌어당긴 이동분**이 얹힌 값이다. `ls`로 정렬하는 것은
스케줄 구조와 due date를 섞어서 읽는 셈이고, due date 성분은 이미 `job_priority`
(tie-break rank)와 목적함수가 따로 보고 있다.

**질문**: 축을 마지막 stage에서 **(last-1) stage 종료시각 `ls'`** 로 옮기면
NEH-CP 산출물이 좋아지는가?

요청된 두 변형:

| 이름 | 1차 키 | 2차 키 | 3차 키 |
|---|---|---|---|
| `midpoint3` | `(fs + ls') / 2` | `ls'` | `job_priority` rank |
| `completion3` | `ls'` | `fs` | `job_priority` rank |

(`midpoint3`의 2차 키를 `ls'`로 두는 것은 사용자 확정 사항이다 — 2026-08-04.
`plans/experiment/20260803`의 `midpoint2`(2차 키 = `ls`)와 같은 계열이고, 그 arm이
통합 리포트에서 잠정 최상위였다.)

---

## 2. 사전 확인 — 두 키는 기존 모드의 별칭이 **아니다** (먼저 읽을 것)

이 저장소는 같은 함정을 두 번 밟았다: `bottleneck`은 `first_stage`의 구조적 별칭
이었고(파일럿 런을 태우고서야 발견, `plans/analysis/20260731/neh_cp_seq_source_pilot.md`
결과 2), `completion2` / `first_stage2`는 대수적으로 기존 모드와 동일했다
(`plans/experiment/20260803/neh_cp_midpoint_tiebreak.md` §2, 실행 전에 걸러냄).
따라서 **arm을 만들기 전에 별칭 검사를 한다.**

### 2.1 대수로는 축퇴하지 않는다

`ls'`는 `ls`의 단조 함수가 아니다. `ls = ls' + (마지막 stage 대기 + 처리시간 +
`insert_idle_time`이 삽입한 유휴)` 인데, 삽입 유휴량은 job마다 due window와
가중치에 따라 다르므로 `ls`와 `ls'`의 순서는 서로 뒤집힐 수 있다. `midpoint2`가
`midpoint`의 **동률군만** 뒤집는 미세 섭동이었던 것과 달리, `ls'`로의 교체는
**동률이 아닌 쌍의 순서도 바꾼다.**

`midpoint3`의 2차 키 선택은 `midpoint`/`midpoint2`와 같은 대수를 따른다: `m'`이
동률인 그룹 안에서 `ls' = 2m' − fs`이므로 `ls'` 오름차순은 `fs` 오름차순의 정확한
역순이다. 즉 2차 키 `ls'`(요청안)와 `fs`(기본값)는 **서로 구분되는 설정**이다.

### 2.2 실측 — run A 최종 스케줄 60개 표본

```bash
uv run python scripts/20260804/preflight_last1_seq_keys.py \
  output/20260731_neh_cp_seq_source_compare/20260801T012922_726471/dv4_mcf_fmm_neh_cp_completion_seq
```

(표본 60, seed 0. `<instance>_solution.json` = flow 최종 스케줄을 읽는다. NEH-CP가
실제로 읽는 incumbent(FMM 출력)는 저장되지 않으므로 대용이다. 여기서 묻는 것은
"이 파이프라인이 만드는 스케줄에서 키들이 얼마나 떨어져 있는가"라는 구조적 질문
이므로 대용으로 충분하다.)

**키별 동률 job 비율** (평균):

| 키 | 동률 비율 |
|---|---:|
| `first_stage` (`fs`) | 10.18 % |
| `midpoint` (`m`) | 3.24 % |
| `completion` (`ls`) | 4.39 % |
| **`midpoint3` (`m'`)** | **3.37 %** |
| **`completion3` (`ls'`)** | **4.56 %** |

**순서 간 거리** (`normalized_mean_rank_distance`, 완전 역순 = 1.0):

| a | b | 평균 거리 | 순서 완전 일치 | 키 Spearman ρ |
|---|---|---:|---:|---:|
| `completion3` | `completion` | 0.0774 | 0 / 60 | 0.968 |
| `completion3` | `first_stage` | 0.1186 | 0 / 60 | 0.951 |
| `completion3` | `midpoint` | 0.0767 | 0 / 60 | 0.978 |
| `midpoint3` | `midpoint` | 0.0447 | 0 / 60 | 0.989 |
| `midpoint3` | `first_stage` | 0.0683 | 0 / 60 | 0.983 |
| `midpoint3` | `completion` | 0.0991 | 0 / 60 | 0.957 |
| `midpoint3` | `completion3` | 0.0557 | 0 / 60 | 0.989 |
| *(대조)* `midpoint` | `first_stage` | 0.0778 | 0 / 60 | 0.977 |
| *(대조)* `midpoint` | `completion` | 0.0733 | 0 / 60 | 0.980 |

읽는 법 — 마지막 두 줄이 **기존 모드 사이의 거리**이고, 이 실험이 만드는 효과의
척도다 (`completion` − `midpoint`는 NEH 스텝 기준 2.21 %p, 6.9 σ의 실효과를 냈다,
`neh_cp_seq_replicate.md` 결과 3).

1. **`completion3`는 새 순서다.** `completion`과의 거리 0.0774는 `midpoint`↔
   `completion` 거리(0.0733)보다 **크다.** 즉 "마지막 stage를 축에서 빼는 것"은
   "모드를 바꾸는 것"과 같은 크기의 섭동이다. `first_stage`와의 거리는 0.1186으로
   가장 멀어, `bottleneck`이 그랬던 식의 `first_stage` 별칭이 **아니다.**
2. **`midpoint3`는 더 작은 섭동이다.** `midpoint`와의 거리 0.0447 ≈ 모드 간 거리의
   60 %. `midpoint2`(동률군 3.55 %만 이동)보다는 훨씬 크지만 모드 교체보다는 작다.
   검정력 계산(§4)에서 이 점을 감안한다.
3. 60/60 인스턴스에서 **어떤 쌍도 순서가 일치하지 않았다.** 축퇴 없음.

`c`별로 나눠도 그림이 같다 (c=5: `completion3`↔`completion` 0.0882, c=10: 0.0679 —
stage가 적을수록 한 stage를 빼는 영향이 크다는 예상과 일치).

---

## 3. 설계

### 3.1 `solution/schedule_sequence.py` — 종료 stage를 선택 가능하게

현재 `schedule_job_sequence`(`:30`)는 `last_stage = schedule.stages[-1]`을 하드코딩
한다(`:89`). 이를 파라미터화한다.

```python
def schedule_job_sequence(
    schedule: FFcSchedule,
    source: ScheduleSeqSource,
    *,
    tiebreak_source: ScheduleSeqSource | None = None,
    tiebreak_rank: Mapping[str, int] | None = None,
    end_stage_index: int = -1,
) -> list[str]: ...
```

- `end_stage_index`는 `schedule.stages`에 대한 **음수 인덱스**다. `-1`(기본) = 마지막
  stage → **기존 동작과 바이트 동일**. `-2` = (last-1) stage.
- 검증: `-len(stages) <= end_stage_index <= -1`이 아니면 `ValueError`. 양수 인덱스는
  받지 않는다 — "뒤에서 몇 번째"라는 의도를 인덱스 부호로 강제해, stage 수가 다른
  인스턴스에 같은 config를 걸어도 뜻이 유지된다.
- `source == "bottleneck"`에 `-1` 아닌 값이 오면 `ValueError` (`tiebreak_source`와
  같은 규칙 — bottleneck의 키 어휘에는 종료 stage가 없다).
- `_KEY_FN` / `_DEFAULT_TIEBREAK` 테이블은 **그대로 둔다.** 두 테이블은 `(fs, ls)`
  두 스칼라만 받으므로, 넘기는 `ls`를 `ls'`로 바꾸면 자동으로 `midpoint3` /
  `completion3`가 된다. 즉 **새 `ScheduleSeqSource` 리터럴을 만들지 않는다** —
  `midpoint3` = `("midpoint", end_stage_index=-2, tiebreak_source="completion")`.

  > **왜 새 리터럴이 아닌가.** 모드는 이미 `key = α·fs + (1−α)·X` 한 족이고
  > (`neh_cp_midpoint_tiebreak.md` §9), 여기서 바꾸는 것은 `α`가 아니라 `X`다.
  > 리터럴로 만들면 `midpoint3` / `completion3` / (나중에) `midpoint4`가 서로
  > 곱집합으로 늘어난다. 직교 파라미터로 두면 `end_stage_index=-3`도 코드 변경
  > 없이 얻는다.

- 누락 방어는 기존 규칙 그대로: 해당 stage에 op이 없는 job은 **건너뛴다**(결과가
  `schedule.jobs`보다 짧을 수 있고, 컨트롤러가 permutation으로 보정한다).
  `end_stage_index=-2`는 이 경로를 더 자주 밟을 수 있다 — 부분 스케줄에서 마지막
  stage는 있고 (last-1) stage는 없는 경우가 가능하다.

docstring에 §2.1의 대수(“`ls'`는 `ls`의 단조함수가 아니므로 `midpoint`/`completion`의
축퇴 논거가 `end_stage_index`에는 적용되지 않는다”)를 남긴다.

### 3.2 `orchestration/controller.py` — `seq_end_stage` 노출

`seq_end_stage: int = -1`을 **`neh_cp_midpoint_seq`(`:2258`)와
`neh_cp_completion_seq`(`:2420`) 두 스텝에만** 추가하고 `_run_neh_cp`(`:2471`)에
keyword-only로 전달한다.

| 스텝 | `seq_tiebreak` | `seq_end_stage` |
|---|---|---|
| `neh_cp` | — | — |
| `neh_cp_midpoint_seq` | 있음(기존) | **추가** |
| `neh_cp_completion_seq` | 없음 | **추가** |
| `neh_cp_first_stage_seq` | 없음 | 추가하지 않음 (§8) |
| `neh_cp_bottleneck_seq` | 없음 | 없음 (§8) |

`_run_neh_cp`는 받은 값을 그대로 `schedule_job_sequence(..., end_stage_index=...)`에
넘기고, 추가로:

- **stage 수 방어**: `abs(seq_end_stage) > instance.stage_count`이면 `-c`로 클램프
  하고 `warning`을 남긴다(런이 죽지 않게). PRA2017 그리드는 `c ∈ {5, 10}`이라
  발화하지 않지만, 1-stage 인스턴스를 쓰는 테스트/CSR 하위 인스턴스에서 도달 가능
  하다.
- 진단 로그 한 줄(`:2625`)에 `end_stage=%d`를 **`tiebreak=%s` 바로 뒤**에 넣는다.
  → **§3.4의 정규식 제약을 반드시 지킬 것.**
- `seq_end_stage != -1`일 때만, 선택된 모드를 `end_stage_index=-1`로도 계산해
  `dist_to_same_source_last_stage=%.4f`를 같은 줄 **끝**에 덧붙인다. 이것이
  §2.2의 표본 60개 예측(0.045 / 0.077)을 1440개 전부에서 실측으로 확인하는
  무료 계측이다.
- `_step_log.yaml` 매핑(`:2724`)에 `job_sequence_end_stage` 키를 추가한다
  (fallback 시 `null`).

**스텝 계약 준수** (`src/ffc_ddw_sum_et/orchestration/AGENTS.md`): 순서 유도는 전부
`elapsed` 측정 이전에 일어나고 `_register`는 호출당 1회 — 변경 없음.

### 3.3 진단 로그 4-모드 블록은 건드리지 않는다

`_run_neh_cp`는 매 호출마다 4개 모드 순서를 전부 계산해 거리 필드를 찍는다
(`:2600`–`:2640`). 여기에 `-2` 변형 2종을 더하면 필드가 6개가 되고 §3.4의 정규식이
깨질 위험만 커진다. §3.2의 `dist_to_same_source_last_stage` 한 필드로 충분하다.

### 3.4 **깨뜨리면 안 되는 것: 커밋된 로그 파서**

`scripts/20260801/analyze_neh_pass_chain.py:94`의 `DIAG_RE`가 이 진단 라인을 파싱
한다:

```python
DIAG_RE = re.compile(
    r"(?P<step>neh_cp_\w+?_seq): seq source=(?P<mode>\w+) .*?"
    r"dist_to_job_priority=(?P<job_priority>[\d.]+) "
    r"dist_to_prev_neh=(?P<prev_neh>[\d.]+|N/A)"
)
```

따라서:

1. `dist_to_job_priority=`와 `dist_to_prev_neh=` **사이에 아무것도 끼워 넣지 않는다.**
   새 필드는 `tiebreak=` 근처(앞쪽) 또는 줄 끝에 붙인다.
2. 필드 이름 `dist_to_job_priority` / `dist_to_prev_neh`를 **바꾸지 않는다.**
   (TODO의 `_ns_` 리네이밍 항목은 지역 변수에 대한 것이고 로그 필드가 아니다.)
3. 스텝 이름 패턴 `neh_cp_\w+?_seq`를 유지한다.

이 제약은 형제 문서(`job_batch_cp.md`)에도 그대로 적용된다 — 거기서 새 스텝이
같은 형식의 줄을 찍되 스텝 이름이 `job_batch_cp_*`라 이 정규식에 걸리지 않는다는
것이 오히려 안전 장치다.

### 3.5 함께 처리하는 `TODO.md` 항목

`TODO.md`의 "`neh_cp_*_seq` — deferred polish from the 2026-07-31 review"는 항목
1–3의 착수 조건을 **"`schedule_sequence.py` 또는 `_run_neh_cp`을 다음에 편집할 때"**
로 적어 두었다. 이 계획이 정확히 그 편집이므로 함께 처리한다 (각각 수 분):

1. **알 수 없는 `source`가 조용히 `[]`를 반환** → `ValueError`
   (`parameters/sorter.py::param_sort_job_sequence`와 동일 계약).
2. **`normalized_mean_rank_distance` 문서화** — 정규화 근거(`n²/2`, 완전 역순 = 1.0),
   candidate에만 있는 job은 무시하고 `n`은 reference 길이를 쓴다는 점, 죽은 코드
   `if n > 0` 제거.
3. **`_ns_used_seq` / `_ns_fallback` → `used_sequence` / `sequence_fallback`** 리네임.

항목 4–6과 7(= `bottleneck` 삭제 결정)은 착수 조건이 다르다 — §8을 볼 것.

---

## 4. 검정력 — 이 run이 무엇을 말할 수 있는가

`plans/analysis/20260801/neh_cp_seq_replicate.md` 실측값 기준.

| 지표 | 값 |
|---|---|
| NEH 스텝 기준 per-instance 노이즈 sd | 12.36 %p |
| paired 대비의 SE (1440) | ≈ 0.33 %p |
| 검출 가능한 최소 효과 (2 σ) | ≈ **0.65 %p** |
| 참고: 모드 간 실효과 (`completion`−`midpoint`, NEH 스텝) | 2.21 %p (6.9 σ) |
| 참고: tie-break 효과 (`midpoint2`−`midpoint`, flow) | −0.16 %p (0.5 σ, 미확정) |

§2.2의 거리로 효과 크기를 선형 외삽하면(모드 간 거리 0.073 ↔ 실효과 2.21 %p):

- `completion3` − `completion` (거리 0.077) → **2 %p 안팎이면 검출된다.** 이 run의
  주 arm.
- `midpoint3` − `midpoint2` (거리 ≈ 0.045 + tie-break 차이) → **1.3 %p 안팎**,
  검출 가능선 위. 다만 외삽은 순서 거리와 효과가 비례한다는 가정에 기댄 것이고
  그 가정 자체는 검증된 적이 없다 — **이 run이 그 가정의 첫 검증이기도 하다**
  (거리 대 효과 산점도, §6.3).

---

## 5. 작업 순서 (TDD)

각 단계는 "실패하는 테스트 → 최소 구현 → green". 매 단계 후 `uv run ruff check`,
마지막에 `uv run ruff format`.

### 단계 1 — `end_stage_index` (`tests/solution/test_schedule_sequence.py`)

1. `end_stage_index=-2`를 준 `completion`이 (last-1) stage 종료시각 순서를 낸다 —
   3 stage 이상 손수 만든 스케줄에서, **마지막 stage와 (last-1) stage의 job 순서가
   서로 반대**가 되도록 시각을 설계한다. (같은 순서면 단언이 무의미하다 —
   `bottleneck` 테스트가 정확히 그 실수를 했다.)
2. `end_stage_index=-2` + `source="midpoint"` + `tiebreak_source="completion"`이
   §2.1의 대수대로 동률군을 뒤집는다.
3. `end_stage_index=-1`(기본)이 기존 출력과 동일 (회귀).
4. 범위 밖 인덱스(`0`, `1`, `-(c+1)`) → `ValueError`.
5. `source="bottleneck"` + `end_stage_index=-2` → `ValueError`.
6. (last-1) stage에 op이 없는 부분 스케줄에서 그 job을 건너뛴다.
7. **비별칭 회귀 테스트**: 무작위 시각 스케줄 여러 개에서
   `completion(end=-2) != completion(end=-1)`인 케이스가 존재한다. §2의 실측을
   코드로 못 박아, 나중에 후처리가 바뀌어 `ls ≡ ls' + const`가 되면 이 테스트가
   깨져 알려주게 한다.

### 단계 2 — TODO 항목 1–3 (같은 파일)

- 알 수 없는 `source` → `ValueError` 테스트.
- `normalized_mean_rank_distance` docstring + 죽은 코드 제거 (동작 불변, 기존
  테스트가 회귀 검사).
- 지역 변수 리네임 (테스트 불필요).

### 단계 3 — 컨트롤러 배선 (`tests/orchestration/test_neh_cp_incumbent_sequence.py`)

1. `seq_end_stage=-2`를 준 `neh_cp_completion_seq` / `neh_cp_midpoint_seq`가 기대
   순서를 `NehCpOption.custom_job_sequence`에 실어 dispatcher를 호출한다
   (`NehCpDispatcher.run` monkeypatch, 기존 테스트 스타일).
2. `midpoint3` 조합(`seq_end_stage=-2` + `seq_tiebreak="completion"`)이 4개 기존
   모드 어느 것과도 다른 순서를 낸다 (fixture는 3 stage 이상).
3. `seq_end_stage=-1`(기본)이면 기존과 동일 (회귀).
4. `abs(seq_end_stage) > c`이면 클램프 + `warning` (`caplog`), 런이 죽지 않는다.
5. 두 스텝을 담은 최소 `subroutine_flow`가 routix `SubroutineFlowValidator`를 통과.
6. incumbent 부재 시 `seq_end_stage`가 있어도 `job_priority` fallback + warning.
7. **로그 형식 회귀**: 진단 라인이 `scripts/20260801/analyze_neh_pass_chain.py`의
   `DIAG_RE`에 여전히 매치된다 (§3.4). 스크립트에서 정규식을 import 하지 말고
   테스트에 같은 패턴을 복제하고, 출처를 주석으로 남긴다.

### 단계 4 — 문서

- `docs/algorithms/neh_cp.md`: `seq_end_stage` 절 추가 — 정렬 키 표에 `ls'` 행,
  §1의 후처리 근거, `midpoint3`/`completion3` 조합 표, `first_stage`에 노출하지
  않는 이유(§8).
- `README.md` 스텝 표: 새 스텝이 없으므로 행 추가 없음.

### 단계 5 — config (§6)

---

## 6. 실험 config 및 실행

### 6.1 arm 구성 (5개)

`metadata/20260804/neh_cp_last1_stage_seq.yaml` (신규). flow·예산은
`metadata/20260803/neh_cp_midpoint_tiebreak.yaml`에서 **한 글자도 바꾸지 않고**
복사한다 (교차 run 비교 가능성 유지).

| # | 시나리오 이름 | NEH 스텝 설정 | 역할 |
|---|---|---|---|
| 1 | `dv4_mcf_fmm_neh_cp_completion3_seq` | `neh_cp_completion_seq`, `seq_end_stage: -2` | **처치군 A** |
| 2 | `dv4_mcf_fmm_neh_cp_midpoint3_seq` | `neh_cp_midpoint_seq`, `seq_end_stage: -2`, `seq_tiebreak: completion` | **처치군 B** |
| 3 | `dv4_mcf_fmm_neh_cp_completion_seq` | `neh_cp_completion_seq` (기본) | arm 1의 동일-run 통제군 |
| 4 | `dv4_mcf_fmm_neh_cp_midpoint2_seq` | `neh_cp_midpoint_seq`, `seq_tiebreak: completion` | arm 2의 동일-run 통제군 (2차 키가 같고 축만 다름) |
| 5 | `dv4_mcf_fmm_neh_cp_midpoint_seq` | `neh_cp_midpoint_seq` (기본) | 앵커 / run-to-run 재현성 검사 |

**통제군을 같은 run 안에 두는 것이 핵심이다** — 교차 run 노이즈(1440 평균 ±0.45 %p)가
기대 효과와 같은 자릿수다(`neh_cp_seq_replicate.md`). arm 5는 run A/C와 설정이 같은
네 번째 replicate이므로 무료 위생 검사가 된다.

arm 2의 통제군을 `midpoint2`(2차 키 `ls`)로 둔 것은 의도적이다: `midpoint3`의 2차
키가 `ls'`이므로, `midpoint`(2차 키 `fs`)와 비교하면 **축 변경과 tie-break 변경이
섞인다.** arm 4가 tie-break을 맞춰 축 효과만 남긴다.

### 6.2 예산·규모

run A/C와 동일: 시나리오 cap `0.09nc`, FMM `cp_tl: 0.0036nc`, NEH
`total_timelimit: 0.0108nc` / `added_batch_size: 15` / `batch_tl_mode: linear`,
`instance_worker_cnt: 12`, `painter_thread_cnt: 96`, `draw_gantt: false`.

**예상 소요**: 5 × 1440 × 15.2 s / 12 ≈ **152분** + 리포트. 96코어 단독 점유 전제.

### 6.3 실행

```bash
uv run python main.py --config metadata/20260804/neh_cp_last1_stage_seq.yaml
```

> **`--config`를 반드시 명시할 것.** `main.py:31`의 `CONFIG_PATH`는 하드코딩된
> 기본값이고, 이를 잊어 다른 config가 돌아간 사고 전례가 있다
> (`neh_cp_seq_replicate.md` "소스 run").

실행 전/후 확인:

1. 96코어 단독 점유 (다른 실험·고아 워커 없음).
2. `main.log`에서 **fallback warning 0건 / permutation 보정 warning 0건 /
   stage 클램프 warning 0건**. 1건이라도 있으면 그 인스턴스는 비교에서 뺀다.
3. arm별 NEH 스텝 소요가 균질한지 (노력 균질성).
4. arm 1·2의 진단 라인에 `end_stage=-2`와 `dist_to_same_source_last_stage`가
   찍혔는지 (배선 확인).

### 6.4 provenance 커밋

```
20260804_neh_cp_last1_stage_seq/<timestamp> run setting
computer: calop4

- question: does moving the sort axis from the last stage's end to the (last-1) stage's end improve NEH-CP's own output
- 5 scenarios x 1440 PRA2017 large instances; flow is dispatch_v4 -> mcf_lb -> fmm -> neh_cp with no ISW-CP / base CP tail
- treatments completion3 / midpoint3 (seq_end_stage: -2) with their tie-break-matched in-run controls
- plan: plans/experiment/20260804/neh_cp_last1_stage_seq.md
```

---

## 7. 분석 계획

`plans/analysis/20260804/neh_cp_last1_stage_seq.md`가 SSOT,
`analysis/20260804_neh_cp_last1_stage_seq/`에 CSV (gitignored).

### 7.1 측정면 — NEH 스텝 자체 산출물 (flow `bestObj` 아님)

`plans/analysis/20260801/neh_cp_seq_source_full.md` **결과 0**: flow 최종값은
`min(seed, NEH)`라 NEH가 seed를 못 이긴 인스턴스(약 1/3)에서 모든 arm이 같은 숫자를
보고한다. `plans/analysis/20260804/neh_cp_seq_tiebreak_merge.md`가 flow 수준에서
멈춰 결론 2를 확정하지 못한 것도 같은 이유다. **같은 실수를 반복하지 않는다.**

`scripts/20260804/analyze_neh_last1_seq.py` (신규):

- `ffc_ddw_sum_et.report.obj_log_loader.build_step_registrations`로 인스턴스별
  `*_obj_log.json`을 스텝 경계로 쪼갠다. 파싱 전 **`docs/artifacts/obj_log.md`를
  읽을 것** (스키마·리더 선택·세 가지 함정).
- 지표: `seed_obj`(FMM 출력) / `neh_obj`(NEH 스텝 자체 출력, `StepRegistration.own_obj`)
  / `neh_best`(블록 이탈 시 incumbent) / `flow_best`(참고).
- RPDf는 `ffc_ddw_sum_et._calc.rpd_f`를 **import 해서** 쓴다 (손으로 쓴
  `2(obj−ref)/(obj+ref)`는 무비용 인스턴스에서 0/0이라 시나리오당 ~57개를 조용히
  떨어뜨린다).

### 7.2 대비

| 대비 | 격리하는 것 |
|---|---|
| arm1 − arm3 (paired, 1440) | **축 변경** (`ls` → `ls'`), completion 계열 |
| arm2 − arm4 (paired) | **축 변경**, midpoint 계열 (2차 키 통제) |
| arm2 − arm5 | 축 + tie-break 합산 효과 (실무적 "권장 설정" 비교) |
| arm1 − arm2 | `completion3` vs `midpoint3` (α 족 안에서의 순위) |
| arm5 vs run A/C의 동명 시나리오 | run-to-run 재현성 (±0.45 %p 밴드) |

### 7.3 T별 분해 (필수)

`plans/analysis/20260802/neh_cp_budget_allocation.md` **조치 4**: 통합 평균이 T=0.2와
T=0.6의 반대 부호 효과를 상쇄해 실효과를 지운 전례가 있다. `--t {0.2,0.4,0.6}`을
스크립트에 넣고 **네 벌 모두** 낸다. 결론은 T=0.6 슬라이스를 우선한다.
(n, c) 셀 분해도 함께 — 특히 **c=5 대 c=10**은 §2.2에서 축 변경의 크기가 달랐으므로
사전 예측이 있는 슬라이스다. 인스턴스 파라미터 해석은 `pra2017-instance-params`
스킬을 먼저 읽는다.

### 7.4 순서 거리 대 효과 (§4의 가정 검증)

컨트롤러 진단 라인에서 `dist_to_same_source_last_stage`를 인스턴스별로 파싱해,
`x = 순서 거리` / `y = arm 간 RPDf 차이` 산점도와 상관을 낸다. "순서를 많이 흔들수록
효과가 크다"는 §4의 외삽 가정이 참인지 처음으로 재는 것이고, 참이라면 앞으로의
순서 실험은 **런을 태우기 전에** 효과 크기를 예측할 수 있게 된다.

---

## 8. 범위 밖 / 열린 결정

- **`first_stage`에 `seq_end_stage` 노출**: 대수적으로는 구분되는 설정이다
  (`first_stage`의 2차 키가 `ls` → `ls'`로 바뀌고, `fs` 동률이 job의 10.18 %이므로
  실제로 순서가 움직인다). 그러나 `first_stage`는 두 번의 1440 그리드에서 일관되게
  **가장 나쁜 모드**였다(+1.53 %p). 지금 노출하면 쓰이지 않을 손잡이가 하나 더 는다.
  `first_stage`가 어떤 슬라이스에서 되살아나면 그때 노출한다.
- **`neh_cp_bottleneck_seq` 삭제 — 이 계획이 착수 조건을 만족시킨다.**
  `TODO.md` 항목 7은 "`bottleneck`은 `first_stage`의 별칭임이 증명됨. 순서 모드
  가족을 다음에 손댈 때 삭제하거나 재정의할 것. 재정의 제안이 없으면 삭제가
  기본값"이라고 적혀 있고, 이 계획이 바로 그 "다음에 손대는" 시점이다.
  CLAUDE.md가 **TODO 항목의 자율 실행을 금지**하므로 여기서는 실행하지 않고
  결정만 요청한다: `neh_cp_bottleneck_seq` / `ScheduleSeqSource`의 `"bottleneck"` /
  `_find_bottleneck_stage` / 관련 테스트를 삭제할 것인가?
  (형제 문서 `job_batch_cp.md`는 이 판단을 선반영해 `bottleneck` 변형을 **만들지
  않는다** — 죽은 모드의 별칭을 새로 늘리지 않기 위해서다.)
- **`end_stage_index=-3` 이하**: 코드는 이미 지원하지만 arm으로 만들지 않는다.
  c=5에서 `-3`은 중간 stage이고 "후처리가 마지막 stage만 건드린다"는 §1의 근거가
  더는 적용되지 않는다. `-2`가 이기면 그때 α 족과 함께 다시 본다.
- **α 족으로의 일반화** (`key = α·fs + (1−α)·X`): `neh_cp_midpoint_tiebreak.md` §9의
  후속 후보 그대로. 이 run이 `X`를 바꾸는 실험이므로, `α`를 연속적으로 훑는 실험은
  그 다음이다.
- 결과가 나오면 `TODO.md`에 결론 한 줄(효과 크기와 부호)을 남겨, 같은 아이디어가
  다시 제안될 때 재실험하지 않도록 한다.

---

## 9. 커밋 계획 (Conventional Commits, 제목 ≤49자)

계획서와 사전 확인 스크립트가 첫 커밋이다 — §2의 숫자가 계획의 근거이므로 함께
들어가야 재현된다.

1. `docs(plan): add neh_cp last-1 stage seq plan`
2. `feat(solution): select the sort axis end stage`
3. `refactor(schedule-seq): clear 2026-07-31 review debt`  (TODO 1–3)
4. `feat(controller): expose seq_end_stage on neh_cp`
5. `docs(neh-cp): document the seq_end_stage parameter`
6. `feat(neh-cp): add last-1 stage seq config`
7. (런 후) `20260804_neh_cp_last1_stage_seq/<ts> run setting`
8. (분석 후) `analysis/20260804_neh_cp_last1_stage_seq merged analysis`

각 커밋 시점에 테스트가 green이므로 bisect가 가능하다.

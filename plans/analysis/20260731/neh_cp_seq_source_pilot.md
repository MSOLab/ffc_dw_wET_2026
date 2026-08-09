# NEH-CP sequence source — 3-instance 파일럿 분석

**작성일**: 2026-08-01 · **종류**: 단일-run 사후 analysis (tracked SSOT)
**선행**: `plans/experiment/20260731/neh_cp_incumbent_sequence.md` (구현 계획),
커밋 `7a27a4c` (컨트롤러 스텝 4종), `bf51f4d` (실험 config).

---

## 질문

1. incumbent 유도 순서 배선이 실제로 동작하는가 (fallback 없이, `job_priority`와
   유의미하게 다른 순서로)?
2. 4개 모드(`midpoint` / `first_stage` / `bottleneck` / `completion`) 중 무엇이
   나은가?
3. `bottleneck` 모드의 "idle 합 최소 = 병목" 정의가 이 문제(E/T 목적, 의도적
   idle 삽입)에서 타당한가? — `TODO.md`의 "`neh_cp_*_seq` — deferred polish" 7번
   항목이 이 런에 답을 위임했다.

---

## 소스 run (full path)

- `output/20260731_neh_cp_seq_source_compare/20260801T005425_870711`
  — 13 시나리오 × 3 인스턴스 = 39 런, 에러 0.
  config 스냅샷: 같은 디렉터리의 `neh_cp_seq_source_compare.yaml`.

**규모 한계 (결론 해석에 필수)**: `ins_index: [60, 61, 63]`으로 좁힌 파일럿이다.
세 인스턴스는 전부 **같은 파라미터 셀** (n=50, c=5, 3 mc/stage, T=0.6, R=0.2,
W=10, Rep0/1/3). 1440 그리드가 아니다.

**노력 균질성 (교란 배제)**: neh_cp 스텝 소요는 39런 전부 6.24–7.15 s
(평균 6.60 s), `total_timelimit: "0.027nc"` = 6.75 s에 바인딩됐다. 시나리오
`timelimit` 22.5 s는 한 번도 바인딩되지 않았고 seeding 스텝은 0.15–0.53 s다.
→ **모든 시나리오가 동일한 CP 예산**을 NEH에 썼다.

## 재현

```bash
uv run python scripts/20260731/analyze_neh_cp_seq_pilot.py \
    output/20260731_neh_cp_seq_source_compare/20260801T005425_870711
```

Block 1은 각 시나리오의 최종 `*_solution.json`을 `load_schedule_json`으로
복원해 `_find_bottleneck_stage`를 다시 돌린다. Block 2는 컨트롤러가 남긴
`seq source=... dist_to_*` 진단 라인을 파싱한다. Block 3–4는
`*_rpdf_comparison.csv`를 쓴다.

---

## 결과 1 — 배선은 정상

- fallback warning 0건, permutation 보정 warning 0건 → 세 seeding prefix 모두에서
  incumbent가 항상 존재했고 완전했다.
- `dist_to_job_priority`가 36개 유도 순서 전부에서 **0.5968–0.8272** →
  유도 순서는 `job_priority` 순서와 근본적으로 다르다. `custom_job_sequence`가
  실제로 NEH 삽입 순서를 지배하고 있다.
- `_step_log.yaml`이 새 매핑 형식(`job_sequence_source` / `job_sequence_fallback`
  / `job_sequence` / `steps`)으로 나오고, `neh_cp_baseline`은 기존 리스트 형식을
  유지한다 — 계획 §2.4대로.

## 결과 2 — `bottleneck`은 `first_stage`의 별칭이다 (모드 드롭)

Block 1: 검사한 **9개 최종 스케줄 전부**에서 선택된 병목 stage가 첫 stage(`i0`)다.
첫 stage의 idle 합은 **정확히 0**이고, 나머지 stage는 45–503이다.

```
scenario                           instance  selected  idle[i0] idle[i1] idle[i2] idle[i3] idle[i4]
dv4_mcf_fmm_neh_cp_bottleneck_seq  Rep0      i0               0       78      198      411      471
dv4_neh_cp_bottleneck_seq          Rep1      i0               0      148      393      278      503
neh_cp_bottleneck_seq              Rep3      i0               0      403      490      321      333
```

**메커니즘**: `get_stage_2_mc_2_idle_time_map(include_idle_before_first_op=False)`
는 machine의 첫 op 이전 idle을 버린다. 첫 stage는 선행 stage 제약이 없어
left-shift(semi-active, 이 config는 `make_semi_active_after_cp: true`)된 스케줄에서
op 사이에 idle이 남을 수 없다. 반면 E/T 목적의 `insert_idle_time`이 만드는 의도적
idle은 전부 하류 stage에 쌓인다. 따라서 최소-idle 규칙은 **무조건 stage 0을
고른다.**

Block 2가 결과를 확인해 준다 — 36개 유도 순서 전부에서
`dist(bottleneck, first_stage)` ∈ [0.0000, 0.0032]이고, 다른 모든 모드 쌍은
0.0208–0.1344다. 두 모드는 1차 정렬 키가 같고 2차 키(`bn_mid` vs `ls_end`)만
달라, 50개 중 인접 1–2쌍만 뒤바뀐다.

**결론**: `TODO.md` 7번이 위임한 질문의 답은 "실험적으로 열등"이 아니라
**"구조적으로 축퇴"**다. 실험으로 판별할 대상이 아니었다. 모드를
`metadata/20260731/neh_cp_seq_source_compare.yaml`에서 제거했다 (13 → 10 시나리오).
`neh_cp_bottleneck_seq` 스텝 메서드와 `ScheduleSeqSource`의 `"bottleneck"`
리터럴은 남겨 뒀다 — 삭제/재정의 결정은 `TODO.md` 7번에 유예 조건과 함께 기록.

재정의를 원한다면 두 방향이 있다: machine 점유율 최대 stage, 또는
`include_idle_before_first_op=True`로 계산해 stage 0에도 head-of-schedule
slack을 부과하기.

## 결과 3 — 모드 간 차이는 노이즈 안에 있다

결과 2의 축퇴가 뜻밖의 도구를 준다: `bottleneck`과 `first_stage`는 **거의 동일한
입력 순서를 받는 자연 near-replicate 쌍**이므로, 두 시나리오의 RPDf 차이가 순수
CP-SAT 노이즈(8-thread wall-clock, 비결정적)의 하한 추정치가 된다.

| prefix | 4-mode 평균 RPDf 스프레드 | 노이즈 프록시: bottleneck − first_stage (인스턴스별, pp) |
|---|---|---|
| `mcf_lb->fmm` | 2.95 pp | −2.31, **+4.64**, +3.08 |
| `dispatch_v4` | 2.60 pp | −2.89, +2.87, **+7.80** |
| `dv4->mcf_lb->fmm` | 5.13 pp | +0.62, +1.52, −0.30 |

**입력이 사실상 같은데 인스턴스당 최대 7.80 pp가 흔들린다.** 모드 효과(2.60–5.13
pp)는 그 안에 완전히 잠긴다. per-instance 순위도 prefix를 바꾸면 뒤집힌다:

```
[dispatch_v4]  0060 0061 0063     [dv4->mcf_lb->fmm]  0060 0061 0063
bottleneck        1    4    4     completion             1    1    1
completion        2    1    3     first_stage            2    2    3
first_stage       3    3    1     bottleneck             3    3    2
midpoint          4    2    2     midpoint               4    4    4
```

`dv4->mcf_lb->fmm`에서 `completion`이 3/3 1위인 것이 유일한 신호지만 다른 두
prefix에서 재현되지 않는다. **이 파일럿으로 모드를 순위 매길 수 없다.** 기록된
CSR batch CP 노이즈 플로어(1440 그리드 평균 obj ±350)와 정합적이다.

## 결과 4 — 실제로 움직인 것은 seeding prefix

| prefix | 평균 RPDf (%) | 인스턴스별 |
|---|---|---|
| none (baseline, `job_priority`) | 22.86 | 20.31, 21.97, 26.30 |
| `mcf_lb->fmm` | 20.39 | 18.49, 21.59, 21.09 |
| `dispatch_v4` | **15.18** | 11.69, 15.53, 18.30 |
| `dv4->mcf_lb->fmm` | **14.97** | 9.20, 17.16, 18.54 |

3/3 인스턴스에서 순서가 일관되므로 n=3에서도 방향은 믿을 만하다. 두 가지:

- **`dispatch_v4`가 seeding을 거의 다 한다.** 그 위에 `mcf_lb->fmm`을 얹어 얻는
  것은 0.21 pp — 노이즈다.
- **`mcf_lb->fmm` 단독은 `dispatch_v4` 단독보다 5.21 pp 나쁘다.** 기존 flow
  (`metadata/20260709/mcf_lb_fmm_neh_cp.yaml`)가 쓰는 조합인데 여기서는 열등하다.
  본 실험의 질문은 아니지만 후속으로 볼 값어치가 있다.

---

## 알려진 교란 (본 런에서 해소되지 않음)

**"incumbent 유도 순서가 `job_priority`보다 나은가"는 이 런으로 답할 수 없다.**
`job_priority`를 쓰는 시나리오는 `neh_cp_baseline` 하나뿐이고 그것은 seeding
prefix가 아예 없다 (plain `neh_cp`는 incumbent를 읽지 않으므로 seeding을 붙여도
순서에 영향이 없어 그렇게 설계됐다). 따라서 baseline 대비 7.7 pp 격차는 순서
효과와 seeding 효과가 교란돼 있다.

분리하려면 `initialize_by_dispatch_v4 -> neh_cp` 및
`dv4 -> mcf_lb -> fmm -> neh_cp` 통제군이 필요하다. **의도적으로 추가하지
않았다** — 사용자 판단으로, 현 config를 그대로 full 그리드에 돌린다. 그 결과는
"어느 유도 모드가 나은가"와 "어느 seed가 나은가"에는 답하지만, "유도 순서 대
`job_priority`"에는 답하지 않는다는 점을 인용 시 유의할 것.

## 조치

1. `bottleneck` 시나리오 3개를 `metadata/20260731/neh_cp_seq_source_compare.yaml`
   에서 제거 (13 → 10 시나리오). 헤더 주석에 사유와 이 문서 경로 기록.
2. 같은 파일의 `ins_index` 를 다시 주석 처리 → full 그리드 복원.
3. `TODO.md` 7번 항목을 "실험 대기"에서 "해결됨 + 코드 결정 유예"로 갱신.

# sw_cp `rj_right_justify_scope` 옵션 + 3단계 partition gantt — staged change 리뷰/검증

- **날짜**: 2026-07-05
- **대상**: branch `20260705_rj_scope_option`의 **staged(uncommitted) change**
  (계획서 `plans/20260705/sw_cp_rj_scope_option.md` 실행 결과)
- **판정**: ✅ **코드 이상 없음.** 계획서 Part A~E가 충실히 구현되어 의도대로 동작하며,
  결과 산출물과 발표자료 PDF(`analysis/20260705_p3_sw_cp.pdf`)가 정확히 일치.
  `ruff check` 통과, 재실행 시 결정론적으로 동일 obj 재현. **git add/commit은 하지 않음.**

---

## 1. 변경 범위 (계획서 Part A~E)

| Part | 파일 | 내용 | 상태 |
| --- | --- | --- | --- |
| A | `algorithm/sw_cp/option.py` | `rj_right_justify_scope: Literal["rtf_only","all_ops"]="rtf_only"` 필드 + `__post_init__` 검증 | ✅ |
| A | `algorithm/sw_cp/dispatcher.py` | `rj_schedule` 빌드 분기(all_ops = `delay_job_latest_leq_obj_contrib_all_stages`, rtf_only = `delay_operations_latest_leq_obj_contrib`) | ✅ |
| A | `orchestration/controller.py` | `sw_cp` / `incremental_sw_cp` 두 step 메서드에 파라미터 노출·전달 | ✅ |
| B | `algorithm/sw_cp/dispatcher.py` | partition gantt를 **3단계**로: `1_before_cp` / `2_after_cp`(raw CP) / `3_after_sm_iti`. `cand_raw = cand.deepcopy()`를 make_semi_active/insert_idle_time **이전**에 스냅샷(debug_gantt일 때만) | ✅ |
| C | `algorithm/sw_cp/visual.py` | **누락-바 버그 수정** — op 바 배치를 "partition의 incumbent-`k` 조회"에서 "schedule 실제 op → region 색상"으로 전환. CP가 machine 재배정한 op도 실제 lane에 렌더 | ✅ |
| D | `algorithm/sw_cp/visual.py` + `dispatcher.py` | 경계선(dummy boundary)을 `ref_schedule=rj_schedule` 기준으로 세 phase 고정 | ✅ |
| E | `metadata/20260705/rj_scope_compare.yaml` | 단일 run·2 scenario(all_ops / rtf_only) 비교 config | ✅ |

---

## 2. Run 정보

- **원본 run (PDF의 출처)**: `output/20260705/20260705T191956_457619`
- **검증 재실행 (본 리뷰)**: `output/20260705/20260705T205628_078755`
  — 두 run 모두 최종 obj **3685.0** 동일(결정론 확인). 원본 run은 그대로 보존.
- **Config**: `metadata/20260705/rj_scope_compare.yaml`
  (benchmark `PRA2017/small`, instance `Instance_20_2_2_0,2_0,2_10_Rep0`,
  `sw_cp` `batch_size=m`, `step_size=2`, `unfixed_batch_count=2`, `cp_tl=60`,
  `debug_partition_gantt=true`, `debug_partition_gantt_max_steps=3`)
- **산출**: scenario 2개 × step 3개 × phase 3개 = **18 SVG**
  (`{scenario}/…/progress/2-sw_cpstep_00N_partition_{1_before_cp,2_after_cp,3_after_sm_iti}.svg`)
- **환경**: python 3.12.3, ortools 9.15.6755, `uv.lock` 존재(재현 가능).

---

## 3. 검증 결과

### 3.1 회귀·정합성

- `uv run ruff check` — **All checks passed**.
- 실행 로그 error/traceback 0건. 두 scenario 모두 완주.
- 모든 phase에서 op 라벨 **40개**(20 job × 2 stage) 정상 — 즉 렌더 누락 없음.
- 두 scenario의 `1_before_cp` 기하가 **서로 다름**(scope 분기 실효 확인), 같은 scenario의
  phase 간 기하가 다름(CP·후처리 효과 관측됨).

### 3.2 PDF와 산출물 일치 (핵심 비교)

step=2 `1_before_cp`를 PNG로 렌더해 PDF와 대조:

- **`all_ops`** (PDF 2·3쪽): LTF 포함 **전 op 우측정렬** → 좌측에 **큰 left dummy**
  (`i1-i1_0`·`i1-i1_1` LTF가 t≈165까지 우측 이동). PDF "Left dummy operation 길이 과대"와 일치.
- **`rtf_only`** (PDF 4·5쪽): LTF는 incumbent 좌측 유지, **RTF만 우측정렬** →
  left·right dummy 모두 최소. PDF "Left & right dummy operation 길이 최소화"와 일치.

### 3.3 버그 수정(Part C) 검증

- 수정 전 증상: `2_after_cp` step_000 첫 stage에 op 20개 중 2개만 렌더(약 절반 누락).
- 수정 후: step_000 `2_after_cp`(raw CP)에서 **40개 바 전부 정상** 렌더.
  원인이던 "incumbent-`k` lookup miss"(CP가 machine 재배정한 op 누락)가 해소됨.

### 3.4 3단계 진단(Part B) — 가설 입증

- `2_after_cp`(raw CP)의 RTF op은 CP 해 위치에 있고,
  `3_after_sm_iti`에서 RTF가 **좌측으로 압축 이동**(경계선은 rj 기준 고정 유지).
- ⇒ RTF 밀림의 원인은 **CP 해 자체가 아니라 `make_semi_active`+`insert_idle_time` 후처리**임이
  시각적으로 확정됨(계획서 미해결 항목 #2 해소).

### 3.5 경계선 고정(Part D)

- `1/2/3` phase 세 그림의 좌/우 dummy 경계선(파선)이 **동일 위치**로 고정 —
  RTF의 상대 변위를 한눈에 비교 가능.

---

## 4. 참고 (버그 아님)

- 이 인스턴스에서 두 scope 모두 최종 obj **3685.0**로 수렴. `rj_right_justify_scope`는 CP
  서브문제의 **참조 프레이밍(dummy 크기·진단 시각화)** 을 바꾸는 것이라, 이 소규모 인스턴스에서
  반드시 다른 해로 갈라지지는 않음 — 정상.
- `metadata/20260705/rj_scope_compare.yaml`에 파일 끝 개행 없음(`\ No newline at end of file`).
  기능 무해, 위생 차원의 사소한 항목.

---

## 5. 재현

```bash
uv run python main.py --config metadata/20260705/rj_scope_compare.yaml
# 산출: output/20260705/<timestamp>/{rj_all_ops,rj_rtf_only}/…/progress/
#       2-sw_cpstep_00N_partition_{1_before_cp,2_after_cp,3_after_sm_iti}.svg
```

SVG→PNG 육안 확인이 필요하면(시스템 cairo 없을 때) matplotlib 백엔드로 직접 덤프하는
래퍼 방식을 사용: `render_partition_gantt_svg`의 `plt.close(fig)`를 임시 가로채 `fig.savefig(..., format="png")`.

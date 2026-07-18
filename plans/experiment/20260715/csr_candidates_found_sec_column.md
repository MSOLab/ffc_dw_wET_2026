# CSR candidates CSV에 `sec_elapsed_step` 컬럼 추가 — coarse-obj-over-time을 단일 파일로

> 목적: `<instance>_csr_candidates.csv` **한 파일만으로** coarsening된 문제의
> "objective value over time" 곡선을 그릴 수 있게 한다. 현재는 값(`coarse_obj`)은
> CSV에, 시각(timestamp)은 `*_SingleInstanceRunner.log`의 incumbent 로그에
> 흩어져 있어 **두 파일을 조인**해야 한다.
>
> 이 문서는 설계 + TDD(빨간불 먼저) 계획까지만. 구현은 별도.

## Context — 현재 관측

예시: `output/20260714_csr_full_grid_k248/20260714T184236_642971/csr_full_d2wp_k2/Instance_100_10_3_0,2_0,2_10_Rep0/`

- `progress/*_csr_candidates.csv` — 후보별 `coarse_obj` / `restored_obj`는 있으나
  `elapsed_sec` 열은 **복원(reconstruct) 소요 시간**(`recon_elapsed`, ~0.01s)이라
  "언제 발견됐는지"의 시각축이 아니다.
- `*_SingleInstanceRunner.log` — SolutionManager incumbent 로그의 값
  (82222 → 82200 → 63910 → 62801)이 곧 `coarse_obj`이고 여기엔 벽시계 시각이
  붙지만, CSV와 별도 파일이다.
- `*_obj_log.json` — composite 스텝(`coarsen_solve_reconstruct`)이 서브루틴 계약상
  register를 **1회만** 하므로 최종 restored 1점(22.57s → 61538)뿐. coarse 궤적 없음.

→ 단일 파일 해결책: CSV에 **"각 coarse 후보가 발견된 CSR-only 경과초"** 컬럼 추가.

## 원칙 — 기록 초는 CSR-only elapsed (global 아님)

후보는 `coarsen_solve_reconstruct`가 만든 **자식(child) 컨트롤러**의 history에서
수확된다 (`controller.py:2899-2914`). 자식 컨트롤러는 자기 타이머(`child.timer`)를
`child.run()` 시점 = **CSR 스텝 진입 직후**에 0부터 시작한다. 각 등록 리포트에서:

```
rec.report.start_time + rec.report.elapsed_time  ==  child.timer.elapsed_sec (등록 시점)
```

(`controller_core.py:274-281`, `_wrap_report`: `start_time = self.timer.elapsed_sec
- report.elapsed_time`.)

이 값이 곧 **CSR 스텝 시작 이후 경과한 실측 초**이다. 자식 프레임이라 글로벌
컨트롤러 클럭이 섞이지 않아 **자동으로 CSR-only**이고, `time_factor`는 스케줄
시간축(`time_factor * C^c`)만 스케일링할 뿐 이 타이머(실초)에는 영향이 없다
(`controller_core.py:92-95`). 즉 원칙이 이 소스에서 공짜로 보장된다 — 별도
재계산·보정 불필요.

## 이름 충돌 결정

기존 `elapsed_sec`(= `recon_elapsed`, `controller.py:2944,2952`)와 새 시각 컬럼이
둘 다 "sec"라 혼동된다. ``sec_elapsed_*`` 접두어로 통일한다.
후속으로 ``sec_elapsed_step``, ``sec_elapsed_recon``, ``sec_elapsed_global`` 등
일관된 어휘를 써서 ``elapsed_sec``(글로벌)과 구분할 예정. **결정:**

| 컬럼 | 의미 | 상태 |
|---|---|---|
| `sec_elapsed_step` | coarse 후보가 발견된 CSR-only 경과초 → **over-time 곡선의 x축** | 신규 |
| `sec_elapsed_recon` | 복원+검증 소요 시간 (기존 `elapsed_sec` 개명) | 개명 |

- `elapsed_sec` → `sec_elapsed_recon` 개명 이유: "sec" 컬럼 2개의 모호성 제거.
  개명은 이 CSV를 **읽는 다운스트림 소비자가 없어** 안전하다
  (`scripts/dump_csr_coarse_obj.py`는 `run_coarsen_solve_reconstruct`를 재실행하는
  독립 경로이고 이 CSV를 파싱하지 않음).
- 개명이 부담되면 fallback: `elapsed_sec` 유지 + `sec_elapsed_step`만 추가. 단
  본 계획은 개명안을 기본으로 한다.

## 구현 대상 (4곳)

1. **`CsrCandidate` 필드 추가** — `algorithm/coarsen_solve_reconstruct.py:75-88`
    ```python
    sec_elapsed_step: float | None = None  # child-frame(=CSR-only) elapsed at registration
    ```
   - `frozen=True, slots=True, kw_only=True` 유지. **default `= None`**으로 두어
     `dedup_candidates` 단위 테스트의 기존 `CsrCandidate(...)` 3개 생성
     (`tests/orchestration/test_csr_solve_flow.py:140-155`)이 타이밍 없이도 계속
     통과하게 한다. 프로덕션 수확 경로는 항상 값을 채운다.
   - docstring에 "child-frame elapsed = CSR-only" 의미 1줄 추가.

2. **수확 루프에서 채우기** — `controller.py:2905` 근처
    ```python
    report = rec.report
    sec_elapsed_step = getattr(report, "start_time", 0.0) + getattr(report, "elapsed_time", 0.0)
    raw_candidates.append(CsrCandidate(..., sec_elapsed_step=sec_elapsed_step))
    ```
    - dedup은 "먼저 온 후보 우선"(`coarsen_solve_reconstruct.py:126-135`)이므로
      중복 축약 시 **더 이른 `sec_elapsed_step`**가 남아 시각 단조성이 유지된다 (일관됨).

3. **candidate_rows dict에 추가** — `controller.py:2945-2954`
    ```python
    "sec_elapsed_step": cand.sec_elapsed_step,
    ```
    (기존 `"elapsed_sec": recon_elapsed` → `"sec_elapsed_recon": recon_elapsed`로 개명)

4. **CSV fieldnames** — `ffcddw_single_instance_runner.py:771-778`
    ```python
    fieldnames = ["source", "coarse_obj", "coarse_bound", "restored_obj",
                  "valid", "sec_elapsed_step", "sec_elapsed_recon"]
    ```

## TDD — 빨간불 먼저

`tests/orchestration/test_csr_solve_flow.py`:

- **T1 (dataclass)**: `CsrCandidate(..., sec_elapsed_step=1.5).sec_elapsed_step == 1.5`;
  default 생략 시 `None`. dedup가 이른 `sec_elapsed_step`를 보존하는지.
- **T2 (harvest, controller)**: `test_solve_flow_*`류에서 `csr_candidate_rows`의
  각 row에 `sec_elapsed_step` 키가 있고, 값이 `report.start_time + elapsed_time`와
  일치(단조 증가)하는지. child 리포트를 스텁하는 기존 픽스처 재사용.
- **T3 (runner CSV)**: `test_runner_emits_candidates_csv`
  (`test_csv_solve_flow.py:361-412`) rows에 `sec_elapsed_step`/`sec_elapsed_recon` 키 추가 →
  파싱된 CSV 헤더에 `sec_elapsed_step` 존재, `sec_elapsed_recon` 존재, 값 라운드트립 검증.
  기존 `elapsed_sec` 단언은 `sec_elapsed_recon`로 교체.

각 테스트를 **먼저 실패**시키고(필드/키/헤더 부재), 그 다음 4곳 구현으로 초록불.

## 검증

- `uv run pytest tests/orchestration/test_csr_solve_flow.py -q`
- `uv run ruff check` / `uv run ruff format`
- 스모크: 소형 인스턴스 1개 CSR 실행 → `*_csr_candidates.csv`에서
   `(sec_elapsed_step, coarse_obj)`가 `*_SingleInstanceRunner.log`의 incumbent
  (시각, 값) 시퀀스와 일치하는지 대조.

## 산출물 (구현 후 기대)

`csr_candidates.csv` 단일 파일:

```
source, coarse_obj, coarse_bound, restored_obj, valid, sec_elapsed_step, sec_elapsed_recon
1-calc_mcf_lb_and_derive_full_sch, 82222, , 80899, True, 1.17, 0.0106
2-run_flip_makespan_cp_from_incumbent, 82200, , 80923, True, 3.48, 0.0100
3-neh_cp, 97585, , 94339, True, ~, 0.0097
4-incremental_sw_cp.1-batch_002, 63910, , 62601, True, 19.10, 0.0098
4-incremental_sw_cp.2-batch_003, 62801, , 61538, True, 22.52, 0.0101
```

→ `(sec_elapsed_step, coarse_obj)`만으로 coarse-obj-over-time 곡선. (`sec_elapsed_step` 예시값은
위 인스턴스 로그 시각 − 스텝 시작 시각으로 추정한 근사; 실측은 구현 후 확인.)

## 미해결 / 범위 밖

- 스텝 **내부**(단일 CP 풀이 중) 개선 궤적은 여전히 저장 안 됨
  (`log_search_progress: False`). 본 변경의 해상도는 "스텝 단위" 그대로. 더 촘촘한
  intra-solve 궤적이 필요하면 별도 건 (`csr_cp_trajectory_json` 경로 확장 검토).

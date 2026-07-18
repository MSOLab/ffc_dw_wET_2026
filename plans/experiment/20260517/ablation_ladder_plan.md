# Ablation ladder experiment plan — FFcDDW (wET) thesis chapter

작성일: 2026-05-17
목적: 박사학위논문 P3 챕터(`Juntaek-PhD-Thesis/contents/ffc_ddw_wET.tex`)의
Computational experiments 절에 들어갈 **구성 요소별 기여도(ablation)** 실험을
정의한다. 현재까지 ablation ladder 실험은 제대로 수행된 적이 없으며, 본
계획서는 그 실험을 재현 가능하게 명세한다.

관련:
- 최종 채택 run: `output/20260512/20260512T105405_551753/`
  (config `mcf_lb_neh_cp_ipwcp_base_cpsat_3_config.yaml`).
- 논문 측 계획서: `Juntaek-PhD-Thesis/plans/experiment/20260517_ffc_ddw_wET_draft.md`.

---

## 1. 배경

제안 알고리즘(IGC의 FFcDDW 인스턴스화)은 5개 단계로 구성된다:

1. `calc_mcf_lb_and_derive_full_sch` — MCF 하한 + 초기 full schedule 유도
   (iterative tightening 포함).
2. `run_flip_makespan_cp_from_incumbent` — flip-makespan CP.
3. `neh_cp` — CP 기반 다중삽입 재구성.
4. `incremental_sw_cp` — 점증 슬라이딩 윈도우 CP (ISW-CP).
5. `solve_base_model_cpsat` — 최종 base-CP refinement.

P2(FFm‖Cmax) 챕터처럼 각 구성 요소가 최종 해 품질에 주는 한계 기여를
정량화하기 위해, 단계를 누적적으로 켜는 **ablation ladder**를 실행한다.

## 2. Ablation 구성 (C1–C5)

| Config | MCF-LB+full sch | Flip-mksp CP | NEH-CP | ISW-CP | Base-CP |
|--------|:--:|:--:|:--:|:--:|:--:|
| C1 | -- | -- | -- | -- | ✓ |
| C2 | ✓ | -- | -- | -- | ✓ |
| C3 | ✓ | ✓ | -- | -- | ✓ |
| C4 | ✓ | ✓ | ✓ | -- | ✓ |
| C5 (IGC) | ✓ | ✓ | ✓ | ✓ | ✓ |

- **C1** = 순수 CP-SAT baseline. 전체 시간예산을 base CP 한 번 호출에 투입.
- **C2–C4** = 단계를 하나씩 추가.
- **C5** = 제안 알고리즘 전체. `mcf_lb_neh_cp_ipwcp_base_cpsat_3_config.yaml`의
  단일 시나리오와 동일하며, `20260512T105405_551753` run을 재현해야 한다.

## 3. 실험 설계 원칙

### 3.1 시간 예산
- 시나리오별 wall-clock 시간제한은 **모든 config 공통** `"0.09nc"`
  ($0.09 \times n \times c$ 초).
- 각 단계의 내부 시간제한(`cp_tl`, `total_timelimit`)은 C5와 **동일하게
  고정**(아래 YAML 참조). 단계를 제거하면 그 단계가 쓰던 시간이 자연스럽게
  마지막 `solve_base_model_cpsat`로 흘러가, base CP가 더 오래 탐색한다.
  즉 모든 config가 동일한 총 예산을 쓰되 분배만 다르다 — P2 ablation과
  동일한 fairness 원칙.

### 3.2 결정성·재현성
- seed, solver thread 수(8), `instance_worker_cnt`(12)를 최종 run과 동일하게
  유지해 C5가 `20260512T105405_551753`을 재현하도록 한다.
- 5개 config를 **하나의 config 파일·하나의 run**으로 묶어, 동일 코드 버전·
  동일 머신·동일 시점에 측정한다 (cross-config 비교 공정성).

### 3.3 벤치마크
- `benchmarks/PRA2017/large` 전체 1,440 인스턴스.
- 스모크 테스트 시에만 `ins_index`로 부분집합 사용.

## 4. 산출물

한 번의 run으로 `output/20260517/<timestamp>/` 아래 5개 시나리오 서브디렉터리가
생성된다. 각 시나리오의 `..._summary.csv`가 per-instance 결과
(`instanceName, bestObj, bestBound, elapsedTime, workStatus, ...`)를 담는다.

논문 측에서는 이 5개 `summary.csv`에서 `(config, instanceName, bestObj)`만
추출해 `Juntaek-PhD-Thesis/data/ffc_ddw_wET/`에 경량 CSV로 commit한다
(P2의 `data/fm_prmu_sumTj/` 방식과 동일, self-contained).

## 5. 실행 절차

```sh
cd ~/code/ffc_ddw_sum_et
uv run python main.py --config metadata/20260517/ablation_ladder_config.yaml
```

1. 먼저 `ins_index`를 소수 인스턴스로 제한한 스모크 run으로 5개 시나리오가
   모두 정상 종료하는지 확인.
2. 스모크 통과 후 `ins_index` 줄을 주석 처리하고 전체 1,440 인스턴스 run.
3. 완료 후 5개 `summary.csv`를 논문 저장소로 추출.

예상 소요: 인스턴스당 최대 `0.09nc`초, 5개 config → 한 인스턴스가 5번 풀린다.
`instance_worker_cnt=12` 기준 대략적 추정은 run 전 스모크에서 보정.

## 6. config 파일

`metadata/20260517/ablation_ladder_config.yaml` (본 계획과 함께 생성). 5개
시나리오 C1–C5. C2–C5의 단계 파라미터는 `mcf_lb_neh_cp_ipwcp_base_cpsat_3_config.yaml`과
1:1로 동일하다. config 파일 자체를 단일 출처(SSOT)로 보고, 본 문서는 의도만
기술한다.

## 7. 논문 ablation 표/그림 매핑

- 표 `ablation.tex` — C1→C2→C3→C4→C5 평균 RPDf. 각 화살표가 한 구성 요소의
  한계 기여.
- 그림 — C1–C5 평균 RPDf 막대그래프 (P2 `c1c5_rpdf_summary.pdf` 대응).
- RPDf 정의·Best pool은 논문 측 계획서(`20260517_ffc_ddw_wET_draft.md` §5 D3)에서
  확정.

## 8. 확인/주의 사항

- `solve_base_model_cpsat`가 시나리오 시간제한까지 계속 탐색하는지(=남은 예산
  흡수) 코드로 재확인 — ablation fairness의 핵심 가정.
- C1에서 `calc_mcf_lb_and_derive_full_sch`가 없으면 incumbent·`mcfLb`·하한이
  비어 있을 수 있다. base CP가 자체적으로 초기해를 만들므로 동작에는 문제
  없으나, `summary.csv`의 `mcfLb`/`bestBound` 컬럼이 config별로 다름을 추출
  단계에서 감안.
- C5 결과가 `20260512T105405_551753`과 통계적으로 일치하는지 검증
  (불일치 시 코드 버전·환경 차이 추적).

# coarsen_mode(rounding) robustness — "K=1 best" 판정이 rounding에 견고한가 (사전 작성)

**작성일**: 2026-07-24 · **종류**: 실험 실행 계획(사전 작성)
**선행 맥락**:
- `plans/analysis/20260724/csr_reconstruct_mode_active_vs_semi.md` — active는 semi 대비 +29.19 pp 악화.
- lastsemi 3-way full-grid merge (`output/20260724_merge_lastsemi_3way/20260724T203441_310017`):
  lastsemi가 semi를 −3.17 pp 앞서고 active 손실을 110.9 % 회복. **그러나 mode 내부에서
  coarsening(k>1)은 not-coarsening(k=1)을 이기지 못함** — equal-budget paired(같은 f):

  | lastsemi, k>1 vs k=1 | mean dRPDf | coarsen 승/무/패 |
  |---|---|---|
  | k=2 | +27.33 pp | 683 / 276 / 3361 |
  | k=4 | +31.86 pp | 711 / 253 / 3356 |
  | k=8 | +34.78 pp | 797 / 179 / 3344 |

  budget parity 확인됨(elapsedTime이 k에 무관하게 동일). 즉 **"coarsening은 손해, K=1이 최선"**.

이 판정은 지금까지 **`coarsen_mode: cumulative` 하나로만** 측정됐다. 본 실험은 나머지
rounding 규칙(`ceil`, `floor`, `round`)에서도 같은 판정이 나오는지 검증한다.

---

## 1. 질문 / 가설

> coarsening의 손해가 `cumulative` rounding 특유의 왜곡 때문일 가능성이 있는가?
> 다른 rounding(`ceil`/`floor`/`round`)을 쓰면 coarsening이 K=1과 경쟁 가능한가?

**가설(귀무)**: 아니다. rounding 규칙은 coarse 인스턴스 생성의 세부일 뿐이고, coarsening의
손해는 **스케줄 해상도(정보) 손실** 자체에서 온다. 따라서 네 rounding 모두에서 **K=1이
여전히 최선**이고 판정은 견고하다.

**반증 시그널**: 어떤 mode에서 k>1 vs k=1 paired dRPDf가 0 근처 또는 음수로 바뀌면,
판정은 rounding-의존적 → coarse 인스턴스 생성 규칙을 재검토해야 한다.

---

## 2. 핵심 사실 — 무엇을 재사용하고 무엇을 새로 도는가

`FFcDDWParameters.coarsen_processing_times`
(`src/ffc_ddw_sum_et/parameters/ffc_ddw_params.py:284`)에서 `factor=1`이면 네 mode가 모두
항등(원본):

- `ceil(p/1)=p`, `round(p/1)=p`, `floor(p//1)=p`,
- `cumulative`: `cum=round(cumsum(p)/1)=cumsum(p)` → 스테이지별 차분이 원본 `p` 복원(≥1).

⇒ **K=1은 rounding-무관.** 따라서:

| 셀 | 상태 |
|---|---|
| k=1 (모든 mode 동일) | **재사용** — `output/20260724_lastsemi_fullgrid/20260724T155337_875856` |
| cumulative × k{2,4,8} | **재사용** — 같은 run |
| **{ceil, floor, round} × k{2,4,8}** | **신규 실행 (본 실험)** |

새로 도는 것은 `{ceil,floor,round} × k{2,4,8} × f{5,10,15}` = **27 시나리오**뿐.

---

## 3. 실험 설계

- **config**: `metadata/20260724/lastsemi_rounding_robust.yaml` (생성 완료).
  - 27 시나리오 `csr_k{K}_tl{f}_lastsemi_{mode}`, K∈{2,4,8}, f∈{05,10,15},
    mode∈{ceil,floor,round}.
  - 각 시나리오의 inner solve_flow·timelimit은 `lastsemi_fullgrid.yaml`의 대응
    k{2,4,8} 블록에서 **그대로 복제**, `coarsen_mode`와 name만 변경 →
    cumulative 대비 순수 rounding 효과.
  - `reconstruct_mode: active_but_last_semi` 고정, `dump_csr_coarse: false`,
    `ins_index` 없음(full 1440), `instance_worker_cnt: 12`.
- **reconstruct_mode = lastsemi 단일 고정 근거**: lastsemi는 coarsening penalty가 가장
  **작은** 모드(고-k에서 semi보다 8.5 pp 덜 나쁨). 즉 coarsening에 **가장 유리한** 조건.
  여기서도 K=1이 이기면 semi에서는 자명하게 이긴다(penalty가 더 큼) → 단일 모드로 충분.
- **비용**: 27 scn × 1440 ≈ **~7.4 h** (calop4, 12-worker; lastsemi_fullgrid 12 scn =
  3h18m 기준).
- **실행**: `main.py`의 `CONFIG_PATH`를 본 config로 지정 완료 → `uv run python main.py`.

---

## 4. 병합 + 분석 (실행 후)

### 4.1 병합
신규 run(27 scn) + 기존 `lastsemi_fullgrid`(12 scn: k1 + cumulative k{2,4,8})를
symlink merge → 하나의 POST_PROCESS_ONLY run에서 **rounding×k×f 전체 격자**의
`*_rpdf_comparison.csv` 생성.

- `scripts/build_merged_run_dir.py` 기반, `build_lastsemi_merge.py`를 참고해
  **신규 merge 스크립트**(`scripts/20260724/build_rounding_merge.py`)를 실행 시점에 작성.
  - **주의(기존에 물린 함정)**: `build_merged_run_dir`는 첫 소스의 artifact_layout을
    이식한다. 반드시 **신버전 run(신규 rounding run 또는 lastsemi_fullgrid)**의 layout으로
    restamp해야 `csr_analysis` KeyError를 피한다 (`build_lastsemi_merge.py`의 restamp 로직
    동일 적용).
- 시나리오 이름 규칙: `_lastsemi_{mode}` = 해당 rounding, `_lastsemi`(접미사 없음) =
  cumulative. k=1(`csr_k1_tl{f}_lastsemi`)은 cumulative-이름이지만 **모든 mode의 k=1을
  대표**.

### 4.2 분석
`scripts/20260724/analyze_recon_lastsemi.py`의 헬퍼(`_pair`/`_wtl`/`_cell_table`)를 재사용해
**신규 분석 스크립트**(`scripts/20260724/analyze_rounding_robust.py`)를 작성:

- mode를 이름에서 파싱(`cumulative`/`ceil`/`floor`/`round`).
- **핵심 블록**: 각 mode m에 대해 `k∈{2,4,8}` vs `k=1`를 **같은 f로 per-instance paired**
  (dRPDf = k>1 − k=1; 양수 = coarsening 손해), mean dRPDf + win/tie/loss.
- **budget parity 블록**: mode×k×f별 mean elapsedTime — k에 무관하게 동일한지 재확인
  (equal-budget 비교 정당성).
- 보조: mode×k×f별 mean RPDf 표.

### 4.3 판정 기준
- **견고(가설 확정)**: 네 mode 모두에서 k>1 vs k=1 mean dRPDf가 **양수**(coarsening 손해),
  cumulative와 부호·규모가 유사 → "K=1 최선"은 rounding-불변.
- **반증**: 어떤 mode에서 dRPDf ≤ 0 또는 승패가 뒤집힘 → rounding-의존, coarse 생성 규칙
  재검토.
- 노이즈 유의: mean dObj가 CSR CP 노이즈 플로어(~±350, 1440 grid)를 넘는지로 실신호 판단.

---

## 5. 재현 커맨드 (실행 후 채움)

```bash
# 1) 실험
uv run python main.py            # CONFIG_PATH = metadata/20260724/lastsemi_rounding_robust.yaml

# 2) 병합 (신규 스크립트, run 완료 후 작성)
uv run python scripts/20260724/build_rounding_merge.py \
    --rounding-run output/20260724_lastsemi_rounding_robust/<ts> \
    --lastsemi-run output/20260724_lastsemi_fullgrid/20260724T155337_875856 \
    --dest         output/20260724_merge_rounding \
    --config-out   metadata/20260724/merge_rounding.yaml
uv run python main.py --config metadata/20260724/merge_rounding.yaml

# 3) 분석 (신규 스크립트)
uv run python scripts/20260724/analyze_rounding_robust.py <merged_run_dir>
```

---

## 6. 작업 순서

1. **(완료)** config 생성 + main.py 포인터 지정.
2. **(다음 대화) 실험 실행** → run setting 커밋
   (`output/20260724_lastsemi_rounding_robust/<ts> run setting`, `computer: calop4`).
3. **병합 스크립트 + 분석 스크립트 작성** → 병합 → 분석.
4. 결과를 `plans/analysis/20260724/`에 merged analysis로 정리
   (판정: rounding 견고성 확정/반증).

---

## 7. 확인 필요 (Open Questions)

- **f=20 / k=16 축 확장**: 논의됐으나 이번 실험에서는 **취소**(제외). 필요 시 후속 실험에서
  별도 처리(그 셀들은 cumulative·k=1 backfill이 없어 순수 재사용 불가).
- **semi 모드 대칭 확인**: 본 실험이 견고성을 확정하면 YAGNI. 반증 시에만 semi로 확장 검토.

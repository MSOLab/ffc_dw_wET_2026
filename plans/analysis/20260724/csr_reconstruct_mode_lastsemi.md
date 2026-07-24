# reconstruct_mode 3-way (semi / active / active_but_last_semi) — full-1440 merged analysis

**작성일**: 2026-07-24 · **종류**: 교차-run merged analysis (사후 작성, tracked SSOT)
**선행**: `plans/analysis/20260724/csr_reconstruct_mode_active_vs_semi.md`
(active가 semi 대비 +29.19 pp 악화) · `plans/experiment/20260724/active_but_last_semi_reconstruction.md`
(가설 + `active_but_last_semi` 모드 설계).

---

## 질문

1. `active_but_last_semi`(마지막 stage만 coarse 배정 보존, 앞 stage는 active 재구성)가
   `active`의 earliness 폭증을 회복하는가? semi를 이기는가?
2. lastsemi로 earliness 결함을 고치면 **coarsening(k>1)이 not-coarsening(k=1)을 이기게** 되는가?

---

## 소스 run (full path)

- **신규 lastsemi (12 scn, full 1440)**:
  `output/20260724_lastsemi_fullgrid/20260724T155337_875856`
  — `csr_k{1,2,4,8}_tl{05,10,15}_lastsemi`, `reconstruct_mode=active_but_last_semi`,
  `coarsen_mode=cumulative`. wall 3:18:24, 에러 0.
- **재사용 semi/active (24 scn, full 1440)**:
  `output/20260724_csr_k_f_cumulative_recon_ab/20260724T005703_124252`
  — 7729b4b의 reconstruct AB. 재실행 없이 symlink 재사용.
- **병합 (36 scn, POST_PROCESS_ONLY)**:
  `output/20260724_merge_lastsemi_3way/20260724T203441_310017`

## 재현

```bash
# 병합 조립 (첫 소스의 구버전 layout 대신 lastsemi run layout으로 restamp → csr_analysis KeyError 회피)
uv run python scripts/20260724/build_lastsemi_merge.py \
    --base-run     output/20260724_csr_k_f_cumulative_recon_ab/20260724T005703_124252 \
    --lastsemi-run output/20260724_lastsemi_fullgrid/20260724T155337_875856 \
    --dest         output/20260724_merge_lastsemi_3way \
    --config-out   metadata/20260724/merge_lastsemi_3way.yaml
uv run python main.py --config metadata/20260724/merge_lastsemi_3way.yaml
# 3-way 분석 (Block 1~6)
uv run python scripts/20260724/analyze_recon_lastsemi.py \
    output/20260724_merge_lastsemi_3way/20260724T203441_310017
```

within-mode coarsening 표(아래 결과 2)는 같은 `*_rpdf_comparison.csv`에서 mode별로
`k∈{2,4,8}`를 `k=1`과 **같은 f로 per-instance paired** (dRPDf = k>1 − k=1)하여 산출.

건전성: 12 셀 × 3 모드 전부 1440 완전. Block 5의 `active − semi = +29.19 pp,
패배 10550/17280`이 7729b4b 결론과 소수점까지 일치 → 재사용 데이터 병합 정확성 확증.

---

## 결과 1 — lastsemi는 semi를 이기고 active 손실을 회복

전체(17,280 pair) paired 비교 (dRPDf = pp, 음수 = 앞의 모드가 우수):

| 비교 | mean dRPDf | mean dObj | win/tie/loss |
|---|---|---|---|
| **lastsemi − semi** | **−3.17** | −2,380 | 10143 / 1519 / 5618 |
| lastsemi − active | −32.36 | −6,856 | 11969 / 172 / 5139 |
| active − semi (참고) | +29.19 | +4,476 | 6549 / 181 / 10550 |

→ lastsemi가 active 손실의 **110.9 % 회복** (회복을 넘어 semi를 3.17 pp 앞섬).
가설(효과 a = 마지막 stage 재배정이 earliness 폭증의 원인) **확정**.

이득은 **coarsening이 심할수록** 커짐 (lastsemi − semi, κ=factor별):

| κ | 1 | 2 | 4 | 8 |
|---|---|---|---|---|
| lastsemi − semi (pp) | −0.64 | −0.25 | −2.61 | **−9.19** |

budget(f)별로도 단조: f5 −2.35 → f10 −3.46 → f15 −3.71 pp. 큰 이득 셀(k8)의 dObj는
CSR CP 노이즈 플로어(~±350)를 크게 넘어 실신호.

mean RPDf (pp, 낮을수록 좋음):

| k | f | semi | active | lastsemi |
|---|---|---|---|---|
| 1 | 5 | 27.863 | 61.124 | 26.641 |
| 1 | 10 | 6.448 | 53.776 | 5.926 |
| 1 | 15 | 0.236 | 50.379 | 0.055 |
| 2 | 5 | 55.691 | 78.155 | 56.508 |
| 2 | 10 | 34.432 | 67.589 | 33.293 |
| 2 | 15 | 25.256 | 61.057 | 24.816 |
| 4 | 5 | 64.123 | 83.301 | 61.942 |
| 4 | 10 | 40.864 | 69.922 | 38.231 |
| 4 | 15 | 31.019 | 62.908 | 28.016 |
| 8 | 5 | 71.812 | 83.164 | 64.994 |
| 8 | 10 | 50.169 | 67.652 | 40.627 |
| 8 | 15 | 42.543 | 61.656 | 31.340 |

---

## 결과 2 — coarsening은 lastsemi에서도 손해 (K=1이 최선)

mode=lastsemi 내부, equal-budget(같은 f) paired `k>1 vs k=1` (양수 = coarsening이 **더 나쁨**):

| lastsemi | mean dRPDf | coarsen 승/무/패 | (참고) semi mean dRPDf |
|---|---|---|---|
| k=2 vs k=1 | +27.33 pp | 683 / 276 / 3361 | +26.94 |
| k=4 vs k=1 | +31.86 pp | 711 / 253 / 3356 | +33.82 |
| k=8 vs k=1 | +34.78 pp | 797 / 179 / 3344 | +43.33 |

- coarsening이 이기는 인스턴스는 전체의 ~16–18 %뿐. k가 클수록 단조로 더 나빠짐.
- **budget parity 확인**: elapsedTime이 k에 무관하게 동일 (lastsemi f5: k1=4.26 s vs
  k8=4.21 s / f15: k1=12.02 s vs k8=11.95 s) → K=1이 시간을 더 쓴 게 아닌 공정한 비교.
- lastsemi는 coarsening penalty를 **줄이긴** 함 (k8: semi +43.33 → lastsemi +34.78, −8.5 pp)
  — 그러나 K=1(예: f15 RPDf 0.055)과의 격차(k8 f15 31.34) 근처도 못 감.

---

## 결론

1. **lastsemi ≻ semi ≻ active**: lastsemi는 active의 earliness 폭증을 고쳐 semi를 −3.17 pp
   앞선다. 이득은 고-coarsening(k=8 −9.19 pp)에 집중. active는 폐기.
2. **coarsening 판정 불변**: lastsemi로도 equal-budget에서 **K=1이 최선**(k>1은 +27~35 pp
   손해). lastsemi는 재구성 결함은 고쳤으나 coarsening이 버리는 스케줄 해상도(정보 손실)는
   되돌리지 못한다.
3. **후속**: (2)가 `coarsen_mode=cumulative` 하나로만 측정됐으므로, ceil/floor/round에서도
   K=1이 최선인지 검증 예정 —
   `plans/experiment/20260724/lastsemi_rounding_robustness.md`.

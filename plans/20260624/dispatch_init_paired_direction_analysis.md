# Dispatch initialization 근거 분석 v2: priority-set × 양방향(paired) 정당화

> 산출물: `analysis/20260624_dispatch_init_justification_2.md`
> 선행: `analysis/20260624_dispatch_init_justification_1.md`
>       (`plans/20260624/dispatch_init_paper_justification_analysis.md`)
> 작성일: 2026-06-24

## 0. 한 줄 요약 (v1 대비 무엇이 바뀌나)

v1은 22개 scenario(11 priority × {sd, rd})에서 **자유 부분집합**을 골랐다.
그래서 채택안(k=5)이 `rd_edd + rd_wxd2 + sd_due2_weight_pos + sd_w1 + sd_wxd2`
처럼 **방향이 비대칭**이었다 — `edd`는 reverse만, `w1`은 simple만. 이는
"어느 priority를 어느 방향으로 돌릴지"를 미리 안다는 전제라 ship 정책으로
정당화하기 어색하다.

v2의 deliverable은 **job priority 집합 `P`**(크기 m ∈ {2,3,4})이고, 런타임은
각 `p ∈ P`에 대해 **두 방향을 모두**(`sd_p`, `rd_p`) 디코드해 **2m개 스케줄**을
만든 뒤 per-instance oracle-best를 취한다 (direction-symmetric / **paired**).
즉 **결정 단위 = priority, 방향은 항상 둘 다**.

- **비교 1 (main)**: `(sd, rd) × (1,2,3,4 priorities)` — m별 best priority-set의
  obj/rpdf 비교 → 채택 m·P\* 확정.
- **비교 2 (ablation)**: `(sd) × P\*` vs `(sd, rd) × P\*` — **고정된 P\* 위에서**
  reverse 방향의 순수 기여 분리.

설명 방식·트랙 구조·metric·oracle 정의·baseline은 v1과 동일.

## 1. 입력 데이터 (v1과 동일 run, 재실행 불필요)

- run: `output/20260624/20260624T165544_348097` (simple+IIT 적용 재실행본).
- 22 scenario = 11 priority × {`sd_`, `rd_`}, 1440 instance, 결측 0
  (스크립트가 pivot 시 구멍 있으면 raise — v1에서 검증됨).
- **v2는 같은 데이터의 재-slice일 뿐, 새 실험 run 없음.** 모든 paired 스케줄
  (`sd_p`, `rd_p`)이 이미 run 안에 있으므로 oracle 계산에 누락 없음.
- metric (둘 다 minimization):
  - `--metric rpdf` (`RPDf_BKS_data`): scale-free, 공정 선택 척도.
  - `--metric obj` (`bestObj`): 절대 weighted E+T, size-dominated → **n별 분해 병행**.
- oracle = per-instance best (해당 스케줄 집합을 모두 돌려 instance마다 best).
- baseline = **2017** = `(sd) × {edd, lsl, osl}` (simple-only 3개, Pan et al.
  2017이 forward FAM만 쓰므로 paired 아님). v1과 동일.

## 2. analyzer 보강 (최소 변경, TDD)

현재 `analyze_dispatch_sweep.py`의 combo 랭킹(`best_combos`/`metric_matrix`)은
**개별 scenario 열** 단위로 k-조합을 enumerate한다. v2 비교 1은 **priority를
단위로** 묶어 각 단위를 그 방향 열들(`sd_p`, `rd_p`)로 확장한 oracle이 필요하다.
→ 이 enumerate 기능만 추가한다. (gain 채점 `--baseline`/`--chosen`은 명시적
열 목록을 받으므로 paired 목록을 그대로 넘기면 **무변경으로 동작**.)

### 2.1 추가 함수 (scripts/analyze_dispatch_sweep.py)

```python
def priority_key(scenario: str) -> str:
    """Strip a leading decode-direction prefix (sd_/rd_) -> priority key."""
    for pre in ("sd_", "rd_"):
        if scenario.startswith(pre):
            return scenario[len(pre):]
    return scenario  # no recognized prefix -> its own unit

def best_unit_combos(mat, k, unit, top=5):
    """unit='scenario' -> 기존 best_combos. unit='priority' -> priority를 단위로
    k-조합을 뽑고, 각 단위를 그 priority의 (mat에 존재하는) 방향 열 전체로 확장해
    oracle min(over 모든 확장 열).mean. 라벨은 priority key 집합."""
```

- `report()`에 `unit` 인자 추가 → `best_unit_combos(mat, k, unit, top)` 호출,
  k 상한 = (scenario 모드) 열 수 / (priority 모드) priority 단위 수.
- `--methods sd_` + `--unit priority`면 각 priority 단위가 `sd_` 열 1개뿐 →
  자연히 **(sd) 단독 ablation**과 일치 (라벨만 bare priority).

### 2.2 CLI

- `--unit {scenario,priority}` (default `scenario`, **하위호환** → 기존 테스트 불변).
  `priority`일 때 `--combo-size`는 **priority 개수**를 의미.

### 2.3 테스트 (tests/scripts/test_analyze_dispatch_sweep.py, 기존 스타일)

- `test_priority_key_strips_direction`: `sd_wxd2→wxd2`, `rd_edd→edd`, `foo→foo`.
- `test_best_unit_combos_pairs_directions`: 2 instance × {sd_A,rd_A,sd_B,rd_B}
  합성 df. k=1 best priority oracle = 그 priority 두 열의 per-instance min mean,
  k=2 = 네 열 union의 min mean — 손계산값 pin.
- 기존 `--unit` 미지정 경로 회귀(default scenario) 1건 확인.

> 변경은 `analyze_dispatch_sweep.py` + 그 테스트에 한정. `io/`·`algorithm/`
> 미접근 → 경계 영향 없음. 변경 후 `uv run ruff check` / `uv run ruff format`,
> `uv run pytest tests/scripts/test_analyze_dispatch_sweep.py`.

## 3. 분석 트랙 → 산출물 (v1과 동일 골격)

### Track A — Decode 방향 코드 근거 (재사용, 무변경)
v1 Track A(§ "simple dispatch = 2017 FAM decode" + IIT 정합성)를 그대로
요약·인용. 코드 사실 불변이므로 재실행/재검증 없음.

### Track B — 비교 1: `(sd, rd) × m`, m ∈ {1,2,3,4}
- 각 m에 대해 **best priority-set**(paired oracle, `--unit priority`)을 산출.
- 표: m | best priority-set | obj | rpdf | Δ(이전 m). knee로 채택 m 결정
  (사용자 창 = 2/3/4).
- (보조) 동수 비교: 2017 priority를 paired로 돌린 `(sd,rd)×{edd,lsl,osl}`도
  한 줄 — priority 확장 vs 2017 차이를 paired 기준으로도 확인.

### Track C — 비교 2 (ablation): 고정 P\* 위 방향 기여
세 arm 모두 **동일 P\* 고정**, baseline(2017) 대비 gain을 함께 보고:
- **C-full**: `(sd, rd) × P\*` (2m 스케줄) — `--chosen sd_p1,rd_p1,...`.
- **C-sd**: `(sd) × P\*` (m 스케줄, reverse 제거) — `--chosen sd_p1,sd_p2,...`
  (= `--unit priority --methods sd_`).
- **C-rd**: `(rd) × P\*` (m 스케줄, simple 제거) — `--chosen rd_p1,rd_p2,...`
  (= `--unit priority --methods rd_`).
- 산출: P\*를 **고정**한 채 ① 단일 방향(sd-only / rd-only) 각각의 성능,
  ② full 대비 한 방향을 뺐을 때 손실(= 그 방향의 한계 기여)을 obj/rpdf·n별로.
  표 3행(C-full / C-sd / C-rd) + "full−sd"·"full−rd" Δ. v1 Track C보다 깔끔
  — priority 집합이 양방향 대칭으로 고정됨.

### Track D — 종합 + 비용, 채택안 확정
- **headline**: 2017 baseline `(sd)×{edd,lsl,osl}` vs 채택안 `(sd,rd)×P\*`
  → 절대 obj 이득(전체 + n별 −%) + rpdf. v1 headline 표와 동형.
- **비용**: 채택안은 instance당 **2m번** dispatch+IIT 디코드. v1 측정
  (디코드 평균 0.24s, n=200 0.44s)으로 2m·시간 환산 — init 오버헤드 무시 가능
  범위 명시.
- 채택안(P\*, m) 확정.

## 4. 실행 커맨드 (보강 후)

```bash
RUN=output/20260624/20260624T165544_348097

# Track B — 비교 1: (sd,rd) × m, m=1..4 (paired)
uv run python scripts/analyze_dispatch_sweep.py $RUN --metric rpdf \
    --unit priority --combo-size 1 2 3 4
uv run python scripts/analyze_dispatch_sweep.py $RUN --metric obj \
    --unit priority --combo-size 1 2 3 4

# Track C — 비교 2: 고정 P* ablation, 3 arm (P* 확정 후 채움)
#   C-full: (sd,rd)×P*
uv run python scripts/analyze_dispatch_sweep.py $RUN --metric obj \
    --baseline sd_edd,sd_lsl,sd_osl \
    --chosen sd_<p1>,rd_<p1>,sd_<p2>,rd_<p2>,...
#   C-sd: (sd)×P*  (reverse 제거)
uv run python scripts/analyze_dispatch_sweep.py $RUN --metric obj \
    --baseline sd_edd,sd_lsl,sd_osl \
    --chosen sd_<p1>,sd_<p2>,...
#   C-rd: (rd)×P*  (simple 제거)
uv run python scripts/analyze_dispatch_sweep.py $RUN --metric obj \
    --baseline sd_edd,sd_lsl,sd_osl \
    --chosen rd_<p1>,rd_<p2>,...
#   (rpdf 버전도 각각 1회씩)

# Track D — headline gain (전체 + n별)
uv run python scripts/analyze_dispatch_sweep.py $RUN --metric obj \
    --baseline sd_edd,sd_lsl,sd_osl \
    --chosen sd_<p1>,rd_<p1>,...        # = (sd,rd)×P*
uv run python scripts/analyze_dispatch_sweep.py $RUN --metric rpdf \
    --baseline sd_edd,sd_lsl,sd_osl --chosen <same>
```

## 5. 산출물 (`analysis/20260624_dispatch_init_justification_2.md`)

v1과 동일 섹션 구성:
1. 헤더(run/데이터/metric/oracle/링크) + **"v1 대비 변경" 박스**(§0 요지).
2. 헤드라인 결과 표(2017 → 채택안 `(sd,rd)×P\*`, obj/rpdf, n별).
3. Track A(재사용 요약) / B(비교 1 표) / C(ablation 표) / D(종합+비용).
4. 논문용 핵심 문장 4개(decode 정합성 / priority-set / **paired 양방향 정책** /
   종합 이득).
5. 재현 커맨드.

## 6. 리스크 / 주의

- **oracle 해석**: `(sd,rd)×P\*` oracle = "2m 스케줄을 모두 돌려 best" 전제 →
  ship 시 실제 2m회 init과 일치. 비용을 Track D에 명시.
- **obj size-dominated** → 절대 이득은 항상 n별 분해와 함께.
- **paired 제약은 v1보다 약한 상한**: 자유 부분집합(v1)이 paired(v2)보다 oracle이
  같거나 더 좋다. v2 채택안 obj가 v1 채택안보다 다소 나쁠 수 있으나, **정책
  정당화 가능성**(priority 단위 결정 + 항상 양방향)이 목적 → 그 trade-off를
  Track D에 한 줄 명시.
- Track A는 sweep 데이터로 직접 답 불가(FAM scenario 미포함) → 코드 독해 1차 근거,
  v1 결론 재사용.

## 7. 실행 순서

1. analyzer 보강(2.1–2.2) + 테스트(2.3) — **TDD: 테스트 먼저 red 확인**.
2. `ruff check`/`format`, `pytest` green.
3. Track B 커맨드 → m·P\* 확정.
4. Track C/D 커맨드(P\* 채워) 실행.
5. `analysis/20260624_dispatch_init_justification_2.md` 작성.

# Plan: priority inspector warning 수정 (W1·2·3·5·6·7)

> 작성일: 2026-06-25
> 대상: `scripts/render_priority_inspector.py`
> 출처: `/git-workflow` 리뷰에서 나온 warning 7건 중 **1·2·3·5·6·7** 대응
>       (W4 = `from __future__` 중복은 본 plan 범위 밖; §8 freebie 참고)
> 선행: `plans/experiment/20260625/priority_rule_simulator_viz.md` (도구 설계)
> 구현은 **다음 대화**에서. 본 문서는 계획까지.

전제: 도구 출력 수치(obj 91,881·47 tardy·group 21/29)는 이미 독립 검증으로
정확. 아래 수정은 **수치를 바꾸지 않는 정리/정직성/cosmetic** 위주이며,
W1만 동작(또는 표면)이 바뀐다. 회귀 기준 = instance 60 재실행 시 **stat 수치 불변**.

---

## W1 — `--compare` 미구현 (silent no-op)

- **위치**: arg 정의 `:713-718`, `compose_html` 전달 `:822`, 미사용 param
  `compose_html(compare_key=...)` `:513`.
- **문제**: CLI가 `--compare`를 받아 `compose_html`까지 넘기지만 거기서 무시.
  광고된 비교 기능이 조용히 아무 일도 안 함.
- **결정 (택1)**:
  - **(A·추천) 정직하게 제거** — `--compare` arg + `compare_key` param +
    전달부 삭제. 비교는 설계 plan §7의 "확장"으로 남김. 이번 패스는 warning
    제거가 목적이므로 최소·정직.
  - (B) 실제 구현 — 같은 레이아웃을 2-rule 좌우 2단으로. 작업량 큼(Panel A를
    두 번 그려 나란히 + stat 2세트). feature work라 별도 plan이 맞음.
- **추천**: **A (제거)**. B는 별도 feature plan으로 분리.

## W2 — Panel A 헤더 정렬 어긋남 (cosmetic)

- **위치**: 헤더 라벨 `w_center` `:261-266`; 실제 막대 `w_bar_x` `:320`,
  `+35` 오프셋 `:327`.
- **문제**: w- 라벨 x=130 / w+ 라벨 x=145 인데, 막대는 w- @x=95(폭≤30 → 95–125),
  w+ @x=130(→130–160). 즉 **w- 헤더가 w+ 막대 위**에 뜸.
- **근본 원인**: 막대 geometry(`w_bar_x=95`, `w_bar_max=30`, gap `35`)가 row
  loop **안에** 하드코딩돼 헤더와 SSOT 불일치.
- **수정**:
  1. 막대 geometry를 **모듈/함수 상위 상수**로 추출:
     `W_BAR_X0 = rank_gutter_w + job_gutter_w + 5`, `W_BAR_MAX = 30`,
     `W_BAR_GAP = 35`.
  2. 헤더 라벨 x를 그 상수에서 계산:
     `w_minus_center = W_BAR_X0 + W_BAR_MAX/2` (≈110),
     `w_plus_center  = W_BAR_X0 + W_BAR_GAP + W_BAR_MAX/2` (≈145).
  3. row loop는 같은 상수를 참조(중복 제거 → W6도 동시 해소 가능).
- **검증**: 렌더 후 w-/w+ 라벨이 각 막대 중심 위에 오는지 육안.

## W3 — 반환 타입 주석 불일치

- **위치**: 시그니처 `:186` `-> tuple[str, int, int, int]` (4),
  docstring `:189` 는 5개 나열, 실제 `return (svg_str, min_time, max_time,
  dw_left, dw_right)` `:407` = 5.
- **수정**: 주석을 `-> tuple[str, int, int, int, int]` 로. docstring은 이미
  맞음. 호출부 `panel_a_svg, *_ = ...` `:800` 는 그대로 동작.
- (선택) 실제로 호출부가 svg만 쓰므로, 반환을 `svg_str` 단일로 줄이고 미사용
  4값을 제거하는 것도 가능 — 단 향후 축 정렬용으로 남겨둔 값일 수 있으니
  **주석만 고치는 최소 수정 추천**.

## W5 — 오해 소지 상수명 + silent fallback

- **위치**: 상수 `DEFAULT_SMALL_DIR = .../large` `:43`; 폴백 로직 `:755-758`.
- **문제 1**: 이름은 "small"인데 `large/`를 가리킴(instance 60=n50이 large/에
  존재). **이름 ↔ 값 모순**.
- **문제 2**: `benchmark_dir = args.benchmark_dir or DEFAULT; if not exists:
  benchmark_dir = DEFAULT` — 사용자가 **잘못된 `--benchmark-dir`를 줘도 에러
  없이** default로 조용히 폴백.
- **수정**:
  1. `DEFAULT_SMALL_DIR` → `DEFAULT_BENCHMARK_DIR` 로 rename (값 유지).
  2. 폴백을 분리: `args.benchmark_dir` 가 **None일 때만** default 사용.
     명시 인자가 부재 경로면 `FileNotFoundError` raise (조용한 폴백 제거).
- **검증**: `--benchmark-dir /nonexistent` → 명확한 에러. 무인자 → 정상.

## W6 — `max_w` 행마다 재계산 (O(n²))

- **위치**: row loop 내부 `:317`
  `max_w = max(max(ewt.values()), max(twt.values()))`.
- **수정**: loop **진입 전 1회** 계산해 재사용. (W2의 geometry 상수 추출과
  같은 블록에서 처리하면 자연스러움.)
- n=50엔 무의미하나 대형 instance 확장(설계 plan의 job-subset) 대비 정리.

## W7 — non-wxd2 "Key" 컬럼이 d̄ 대신 due-range 중점 사용

- **위치**: 일반 fallback `:389-392`
  `ea = ew + (dl - (dw_left+dw_right)/2)`,
  `ta = tw + ((dw_left+dw_right)/2 - du)` → `_fmt_key(ta-ea)`.
- **문제**: `(dw_left+dw_right)/2` 는 **표시용 due-range 중점**이지
  wxd2의 `d̄`(= midpoint 평균)가 아님. non-wxd2 rule에서 "Key" 가 잘못된
  center 기반 aversion-delta 를 보여 **오해 소지**. 게다가 어떤 rule이든
  wxd2식 delta를 찍는 것 자체가 그 rule의 실제 정렬 기준과 무관.
- **수정 (택1)**:
  - **(A·추천) center를 진짜 d̄로** 교정 + 헤더 라벨 명시: 진짜
    `d_bar = mean((dl+du)/2)` 를 **한 번 계산**(wxd2 경로와 동일 식, SSOT
    헬퍼로 추출)해서 fallback delta에 사용. 컬럼 헤더를 rule≠wxd2일 때
    `T−E@d̄`(generic aversion proxy)로 라벨해 "이 rule의 정렬키가 아님"을 명시.
  - (B) non-wxd2면 Key 컬럼을 **비움/생략** — 가장 정직하나 정보 손실.
- **추천**: **A**. `compute_wxd2_partition` 의 d̄ 계산을 작은 헬퍼
  `mean_midpoint(instance)` 로 빼서 fallback·wxd2 양쪽이 공유(SSOT).
- (확장, 본 plan 밖) 각 rule의 실제 sort key를 노출하려면 rule별 key extractor
  가 필요 — 설계 plan §2의 "rule별 오버레이 plugin"과 함께 별도 작업.

---

## 작업 순서 (다음 대화)

1. **구조 정리 먼저**: 막대 geometry 상수 추출(W2) + `max_w` 호이스트(W6) +
   `mean_midpoint` 헬퍼 추출(W7) — 같은 블록이라 한 번에.
2. 헤더 라벨 x 재계산(W2) / fallback delta에 진짜 d̄ + 헤더 라벨(W7).
3. 타입 주석 수정(W3).
4. 상수 rename + 폴백 raise(W5).
5. `--compare` 제거(W1-A) — arg·param·전달부 3곳.
6. `uv run ruff check` / `ruff format`.
7. **회귀 검증**: `uv run python scripts/render_priority_inspector.py
   --instance-index 60 --rule-key wxd2` 재실행 → stat 수치(obj 91,881 등)
   **불변** 확인 + Panel A 헤더 정렬 육안 확인.

## 열린 결정

- **W1**: 제거(A) vs 구현(B) — 추천 A.
- **W3**: 주석만(최소) vs 반환 축소 — 추천 주석만.
- **W7**: d̄ 교정+라벨(A) vs 컬럼 생략(B) — 추천 A.

## §8 — 범위 밖 freebie (참고)

- **W4** (`from __future__ import annotations` 중복 `:22-23`): 한 줄 삭제면
  끝. 위 1번 구조 정리 커밋에 묻어가도 무방하나, 사용자가 명시 목록에서
  제외했으므로 **별도 판단**. 건드릴 거면 같이, 아니면 그대로.

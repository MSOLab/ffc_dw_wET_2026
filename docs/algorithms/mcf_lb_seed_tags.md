# MCF-LB Seed Tag Priority Maps

`phase1_mcf.py`에서 구성하는 `priority_map_by_tag`는 MCF 선점 스케줄 해를 initial incumbent seed로
변환할 때 사용하는 잡별 정렬 기준(우선순위 값)이다.
값이 **작을수록 먼저 배분**되며, 동점은 instance 정의 순서로 tie-break한다.

---

## 전체 태그 요약

| 태그 | 출처 | 수식 |
|------|------|------|
| `start_time` | MCF 해 | $\min(t\in \cal{T} \mid x_{jt}=1)$ |
| `completion_time` | MCF 해 | $\max(t\in \cal{T} \mid x_{jt}=1)$ |
| `completion_time_minus_p` | MCF 해 | $\max(t\in \cal{T} \mid x_{jt}=1) - p_{cj}$ |
| `avg_time` | MCF 해 | $average(t\in \cal{T} \mid x_{jt}=1)$ |
| `avg_time_minus_half_p` | MCF 해 | $average(t\in \cal{T} \mid x_{jt}=1) - p_{cj}/2$ |
| `due_date_lb` | 인스턴스 | $d^{-}_j$ |
| `due_date_lb_minus_p` | 인스턴스 | $d^{-}_j - \sum_i p_{ij}$ |
| `due_date_ub` | 인스턴스 | $d^{+}_j$ |
| `due_date_ub_minus_p` | 인스턴스 | $d^{+}_j - \sum_i p_{ij}$ |
| `due_date_star` | 인스턴스 | $d^{*}_j$ |
| `due_date_star_minus_p` | 인스턴스 | $d^{*}_j - \sum_i p_{ij}$ |
| `due_date_star_minus_half_p` | 인스턴스 | $d^{*}_j - \frac{1}{2}\sum_i p_{ij}$ |
| `due_date_star_plus_half_p` | 인스턴스 | $d^{*}_j + \frac{1}{2}\sum_i p_{ij}$ |
| `due_date_star_plus_p` | 인스턴스 | $d^{*}_j + \sum_i p_{ij}$ |

---

## 표기 정의

| 기호 | 의미 |
|------|------|
| $\mathcal{T}$ | 전체 시간대 집합 |
| $x_{jt}$ | MCF 해에서 잡 $j$가 시간대 $t$에 배분되면 1, 아니면 0 |
| $p_{cj}$ | 마지막 스테이지 $c$에서의 잡 $j$ 처리 시간 |
| $p_{ij}$ | 스테이지 $i$에서의 잡 $j$ 처리 시간 |
| $d^{-}_j$ | due-date 윈도우 하한 (earliness deadline) |
| $d^{+}_j$ | due-date 윈도우 상한 (tardiness deadline) |
| $w^{-}_j$ | earliness 페널티 가중치 |
| $w^{+}_j$ | tardiness 페널티 가중치 |
| $d^{*}_j$ | 가중 평균 due-date (아래 정의) |

### $d^{*}_j$ 정의

$$d^{*}_j = \frac{w^{-}_j \cdot d^{-}_j + w^{+}_j \cdot d^{+}_j}{w^{-}_j + w^{+}_j}$$

$w^{-}_j = w^{+}_j$이면 단순 중점 $(d^{-}_j + d^{+}_j) / 2$와 동일하다.

---

## MCF 해 기반 태그

MCF 선점 스케줄 풀이 결과(`x_val`)에서 유도한다.

| 태그 | 수식 | 의미 |
|------|------|------|
| `start_time` | $\min(t\in \mathcal{T} \mid x_{jt}=1)$ | MCF 해에서 잡 $j$가 최초 배분된 시각 |
| `completion_time` | $\max(t\in \mathcal{T} \mid x_{jt}=1)$ | MCF 해에서 잡 $j$의 마지막 배분 시각 |
| `completion_time_minus_p` | $\max(t\in \mathcal{T} \mid x_{jt}=1) - p_{cj}$ | completion_time에서 마지막 스테이지 처리 시간을 뺀 값; 잡의 "유효 시작" 추정 |
| `avg_time` | $\text{avg}(t\in \mathcal{T} \mid x_{jt}=1)$ | MCF 해에서 배분된 시간대들의 평균; 잡이 몰려 있는 시간대 중심 |
| `avg_time_minus_half_p` | $\text{avg}(t\in \mathcal{T} \mid x_{jt}=1) - p_{cj}/2$ | avg_time에서 반-처리시간을 빼 잡 중심을 보정한 값 |

> `completion_time_minus_p`와 `avg_time_minus_half_p`는 "잡이 시작해야 하는 시점"의
> 추정값으로, 마지막 스테이지 dispatching 우선순위를 MCF 해와 가깝게 맞추기 위한 조정이다.

---

## Instance 파라미터 기반 태그

문제 파라미터(due-date 창, 가중치, 처리시간)만으로 계산하며 MCF 해와 독립적이다.

### due-date 하한 계열

| 태그 | 수식 | 의미 |
|------|------|------|
| `due_date_lb` | $d^{-}_j$ | earliness 마감; 이보다 일찍 완료되면 earliness 페널티 발생 |
| `due_date_lb_minus_p` | $d^{-}_j - \sum_i p_{ij}$ | $d^{-}_j$에서 전체 처리시간을 빼 "가장 이른 허용 시작 시점" 근사 |

### due-date 상한 계열

| 태그 | 수식 | 의미 |
|------|------|------|
| `due_date_ub` | $d^{+}_j$ | tardiness 마감; 이보다 늦게 완료되면 tardiness 페널티 발생 |
| `due_date_ub_minus_p` | $d^{+}_j - \sum_i p_{ij}$ | $d^{+}_j$에서 전체 처리시간을 빼 "늦어도 허용 가능한 시작 시점" 근사 |

### $d^{*}$ 계열 (가중 평균 due-date 중심)

$d^{*}_j$를 기준으로 처리시간의 $\pm\frac{1}{2}$, $\pm 1$배를 더하거나 빼서 탐색 범위를 조정한다.

| 태그 | 수식 | 해석 |
|------|------|------|
| `due_date_star` | $d^{*}_j$ | 가중 평균 due-date; 페널티 균형점 |
| `due_date_star_minus_p` | $d^{*}_j - \sum_i p_{ij}$ | 전체 처리시간 이른 쪽 이동 |
| `due_date_star_minus_half_p` | $d^{*}_j - \frac{1}{2}\sum_i p_{ij}$ | 절반 처리시간 이른 쪽 이동 |
| `due_date_star_plus_half_p` | $d^{*}_j + \frac{1}{2}\sum_i p_{ij}$ | 절반 처리시간 늦은 쪽 이동 |
| `due_date_star_plus_p` | $d^{*}_j + \sum_i p_{ij}$ | 전체 처리시간 늦은 쪽 이동 |

$\pm\sum_i p_{ij}$ 조정은 잡이 $d^{*}_j$에 완료($+$)되거나 시작($-$)하도록 목표 시점을 이동시키는
dispatch heuristic이다.

---

## 태그 간 관계 요약

MCF 해 기반 (일반적으로 성립, 선점 분산도에 따라 달라질 수 있음):

$$\text{start\_time} \;\leq\; \text{avg\_time\_minus\_half\_p} \;\leq\; \text{avg\_time} \;\leq\; \text{completion\_time\_minus\_p} \;\leq\; \text{completion\_time}$$

Instance 기반:

$$d^{-}_j - \textstyle\sum_i p_{ij} \;\leq\; d^{*}_j - \textstyle\sum_i p_{ij} \;\leq\; d^{*}_j - \tfrac{1}{2}\textstyle\sum_i p_{ij} \;\leq\; d^{*}_j \;\leq\; d^{*}_j + \tfrac{1}{2}\textstyle\sum_i p_{ij} \;\leq\; d^{*}_j + \textstyle\sum_i p_{ij}$$

두 계열을 함께 실험함으로써 MCF 해의 정보를 활용하는 seed와
인스턴스 파라미터만 활용하는 seed의 성능을 비교할 수 있다.

---

## 관련 파일

| 역할 | 파일 |
|------|------|
| 태그 매핑 구성 | [phase1_mcf.py](../../src/ffc_ddw_sum_et/algorithm/mcf_lb/phase1_mcf.py) |
| MCF 기반 메서드 | [parallel_mc_pmtn.py](../../src/ffc_ddw_sum_et/algorithm/parallel_mc_pmtn.py) |
| Instance 기반 메서드 | [ffc_ddw_params.py](../../src/ffc_ddw_sum_et/parameters/ffc_ddw_params.py) |
| SeedTag 타입 정의 | `algorithm/mcf_lb/` 내 타입 모듈 |

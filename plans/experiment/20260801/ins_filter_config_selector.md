# `ins_filter` — instance 파라미터로 실험 대상 선택 (사전 작성, 코드 변경 계획)

**작성일**: 2026-08-01 · **종류**: 코드 변경 계획 (TDD) · **상태**: 계획
**선행**: `5b35cdf` (2026-08-01, "analysis/20260801_neh_cp_seq_replicate merged analysis")
· **후속**: 없음 (실험 아님, config 선택자 변경)

---

## 1. 문제

실험 config가 대상 instance를 `ins_index` 정수 리스트로만 지정할 수 있다.
자주 쓰는 슬라이스가 160개짜리라 YAML이 160줄 늘어나고, 그 리스트를 봐서는
어떤 슬라이스인지 알 수 없다.

더 심각한 건 **같은 지식이 12벌 복제**되어 있다는 점이다. 아래 config들의
`ins_index`는 바이트 단위로 동일한 160개 집합이며, 실측 결과 정확히
`T=0.6 AND R=0.2` 셀(= 16개 `(n,c,mps)` 셀 × W 2 × Rep 5)이다:

```plaintext
metadata/20260624/wspt_region_probe_config.yaml
metadata/20260711/csr_in_mix_f4f5.yaml
metadata/20260714/csr_higher_k_validation.yaml
metadata/20260719/merge_csr_k1_32.yaml
metadata/20260720/csr_coarsen_mode_T06.yaml
metadata/20260720/csr_k1_32_recon_fix.yaml
metadata/20260720/merge_csr_init_k4.yaml
metadata/20260721/csr_coarsen_mode_T06_2.yaml
metadata/20260724/csr_coarsen_mode_active.yaml
metadata/20260725/coarsening_crossover.yaml
metadata/20260727/csr_usability_sweep.yaml
metadata/20260801/neh_cp_seq_full_compare.yaml   ← 현재 staged
```

이미 우회 시도도 있다 — `scripts/20260727/build_exp3_config.py:49`는
`SRC_INS_INDEX_CONFIG = metadata/20260725/coarsening_crossover.yaml`을 열어
`ins_index`를 **다른 config에서 읽어온다**. 리스트를 또 복제하지 않으려는
동기는 이미 존재하는데, 이를 지탱할 1차 표현이 없어서 config 간 참조라는
잘못된 방향으로 샌 것이다.

`(T,R)=(0.6,0.2)` 셀의 단일 출처는 `benchmarks/PRA2017/pra2017_bks_table.csv`
이며, 이 파일 경로는 **이미 config의 `bks_table_csv_path` 필드에 들어 있다**.
필요한 건 그 컬럼으로 선택하는 표현식뿐이다.

## 2. 설계 결정 (확정)

| 항목 | 값 | 근거 |
|---|---|---|
| 새 config 키 | `ins_filter` | `ins_index`(명시 열거)와 병존, 대체 아님 |
| 필터 소스 | `bks_table_csv_path` | 이미 config에 있음. `n, c, totalMcCount, T, R, W` 보유 |
| 키 간 결합 | **AND** | `T: 0.6` + `R: 0.2` → 교집합 |
| 값이 리스트 | **OR** | `n: [150, 200]` |
| 값 비교 | **float 정규화 후 비교** | YAML `1.0`/`1` vs CSV `"1.0"` 불일치 방지 |
| `ins_index`와 동시 지정 | **ValueError** | 교집합/합집합 어느 쪽도 자명하지 않음 (YAGNI) |
| 기존 12개 config | **변경하지 않음** | 과거 run setting 커밋이 참조하는 실행 이력. §7 참조 |

### 2.1 왜 `ins_index_source` 서브셋 CSV가 아닌가

첫 후보는 실험별 `ins_index_source` CSV를 만드는 것이었다. 채택하지 않는다:

1. `ins_index_source`(`pra2017_hybrid_match.csv`)는 `insIndex → 파일명` 매핑의
   **유일 출처**다. 서브셋 CSV를 만들면 이 매핑이 실험 수만큼 복제되어,
   해결하려던 중복이 리스트에서 CSV로 옮겨갈 뿐이다.
2. 애초에 **동작하지 않는다.** `BenchmarkLoader.load_all`은 `ins_index`가
   `None`이면 CSV를 무시하고 디렉터리를 glob한다
   (`benchmark_loader.py:77-80`). 서브셋 CSV만으로는 필터가 걸리지 않으며,
   걸리게 하려면 `ins_index_source`의 의미 자체를 "매핑"에서 "선택"으로
   바꿔야 한다 — 리포팅 쪽(`reporting.py:886`, `:978`)이 같은 파일을 BKS
   메타데이터 조인에 쓰고 있어 의미 변경은 그쪽까지 번진다.

`ins_filter`는 매핑 파일을 건드리지 않고 **선택만** 표현한다.

### 2.2 왜 `BenchmarkLoader`가 아니라 별도 함수인가

`BenchmarkLoader`는 `benchmark_dir`와 `ins_index_source`만 안다. 필터 해석에는
`bks_table_csv_path`가 필요한데, 이를 로더에 주입하면 로더가 BKS 테이블까지
알게 되어 책임이 둘로 늘어난다. 대신 **config → `ins_index` 리스트**로
해석하는 함수를 앞단에 두고, 로더는 지금 계약(`ins_index` 리스트를 받는다)
그대로 둔다. `main.py`와 `scripts/validate_resume_config.py`가 같은 함수를
호출하므로 두 경로의 해석이 갈라질 수 없다.

## 3. 계약 (contract-first)

`src/ffc_ddw_sum_et/orchestration/ins_filter.py` 신규:

```python
FILTERABLE_COLUMNS = ("n", "c", "totalMcCount", "T", "R", "W")

def resolve_ins_index(config: dict) -> int | list[int] | None:
    """config의 ins_index / ins_filter를 실행 대상 insIndex로 해석한다."""
```

**입력**: 실험 config dict. 읽는 키는 `ins_index`, `ins_filter`,
`bks_table_csv_path` 셋뿐이다.

**반환**:

| 상황 | 반환 |
|---|---|
| 둘 다 없음 | `None` (= 전체 glob, 기존 동작 불변) |
| `ins_index`만 | 그 값을 그대로 (기존 동작 불변) |
| `ins_filter`만 | 매칭된 `insIndex`의 **오름차순 `list[int]`** |

**불변식**: `ins_filter` 경로의 반환값은 항상 비어있지 않다 (0개면 예외).

**오류** — 전부 `ValueError`, 실행 전에 즉시 실패한다:

| 조건 | 메시지에 반드시 포함 |
|---|---|
| `ins_index`와 `ins_filter` 동시 지정 | 두 키 이름, "둘 중 하나만" |
| `ins_filter`가 있는데 `bks_table_csv_path` 없음 | 필요한 키 이름 |
| `bks_table_csv_path` 파일 없음 | 경로 |
| 알 수 없는 컬럼 (오타) | 그 키 + `FILTERABLE_COLUMNS` 전체 |
| 매칭 0개 | 각 필터 컬럼의 **CSV에 실제 존재하는 값 목록** |

**값 비교 규칙**: 필터 값과 CSV 셀을 모두 `float()`로 정규화해 비교한다.
6개 컬럼이 모두 수치이므로 예외가 없고, `T: 0.6`(float) ↔ `"0.6"`,
`R: 1`(int) ↔ `"1.0"`, `n: 50` ↔ `"50"`이 모두 의도대로 매칭된다.
`float()`가 실패하는 필터 값은 위의 "매칭 0개"가 아니라 즉시 `ValueError`로
그 값을 지목한다.

**사용례** — §1의 160개 리스트를 대체하는 형태:

```yaml
benchmark_dir: benchmarks/PRA2017/large
ins_index_source: benchmarks/PRA2017/pra2017_hybrid_match.csv
bks_table_csv_path: benchmarks/PRA2017/pra2017_bks_table.csv
ins_filter:
  T: 0.6
  R: 0.2
```

```yaml
ins_filter:            # 큰 instance만, T는 두 수준
  n: [150, 200]
  T: [0.4, 0.6]
```

## 4. 변경 사항

### C1 — `orchestration/ins_filter.py` (신규)

§3의 계약을 구현. 모듈 docstring에 위 표(반환·오류·비교 규칙)를 그대로 싣는다
(config 스키마 문서가 따로 없으므로 이 docstring이 계약 문서다).
`orchestration/__init__.py`의 `__all__`에 `resolve_ins_index` 추가.

### C2 — `main.py` 호출부

`main.py:113-114`

```python
ins_index_filter = config.get("ins_index")
instances = loader.load_all(ins_index=ins_index_filter)
```

→

```python
ins_index_filter = resolve_ins_index(config)
instances = loader.load_all(ins_index=ins_index_filter)
```

`ins_filter`가 쓰인 경우 해석 결과를 로그로 남긴다 (개수 + 필터 내용):

```python
logger.info("ins_filter %s -> %d instances", config["ins_filter"], len(...))
```

config 파일 자체는 `main.py:71`에서 run 디렉터리로 복사되므로, 필터 표현식은
run 산출물에 그대로 보존된다.

### C3 — `scripts/validate_resume_config.py` 호출부

`:95`의 `config.get("ins_index")`를 `resolve_ins_index(config)`로 교체.
이 스크립트는 `main.main()`의 검사를 재현하는 것이 존재 이유이므로
(`scripts/CLAUDE.md` §5), 같은 함수를 쓰지 않으면 RESUME 검증이 실제 실행과
다른 instance 집합을 보게 된다.

### C4 — `metadata/20260801/neh_cp_seq_full_compare.yaml`

현재 staged 되어 있는 160줄 `ins_index`를 §3 사용례의 `ins_filter` 2줄로
교체한다. `main.py`의 `CONFIG_PATH` 변경(이미 staged)은 그대로 둔다.

## 5. 대상 파일

| 파일 | 변경 |
|---|---|
| `src/ffc_ddw_sum_et/orchestration/ins_filter.py` | **신규** — C1 계약 구현 |
| `src/ffc_ddw_sum_et/orchestration/__init__.py` | `resolve_ins_index` export |
| `main.py` | C2 호출부 1줄 + 로그 |
| `scripts/validate_resume_config.py` | C3 호출부 1줄 |
| `metadata/20260801/neh_cp_seq_full_compare.yaml` | C4 160줄 → 2줄 |
| `tests/orchestration/test_ins_filter.py` | **신규** — §6 |
| `src/ffc_ddw_sum_et/orchestration/benchmark_loader.py` | **변경 없음** (§2.2) |

## 6. 검증 (TDD — 각 테스트가 red를 거쳐야 함)

**단위** (`tests/orchestration/test_ins_filter.py`, tmp_path에 축소 CSV 작성 —
`test_benchmark_loader.py`의 `_write_index_csv` 스타일을 따른다)

1. 스칼라 단일 조건 (`{"T": 0.6}`)이 해당 행들의 `insIndex`를 오름차순
   `list[int]`로 반환한다.
2. 두 키가 **AND**로 결합된다.
3. 리스트 값이 **OR**로 결합된다 (`{"n": [150, 200]}`).
4. 값 정규화: `T: 0.6`(float) ↔ `"0.6"`, `R: 1`(int) ↔ `"1.0"`,
   `n: 50`(int) ↔ `"50"`이 모두 매칭된다.
5. `ins_index`도 `ins_filter`도 없으면 `None`.
6. `ins_index`만 있으면 그 값이 그대로 반환된다 (기존 동작 불변 회귀).
7. 두 키 동시 지정 → `ValueError`, 메시지에 두 키 이름.
8. `ins_filter`만 있고 `bks_table_csv_path` 없음 → `ValueError`.
9. 알 수 없는 컬럼 (`{"t": 0.6}` 소문자 오타) → `ValueError`, 메시지에
   `FILTERABLE_COLUMNS`.
10. 매칭 0개 (`{"T": 0.5}`) → `ValueError`, 메시지에 `T`의 실제 값 목록.
11. `float()` 불가한 값 (`{"T": "hard"}`) → `ValueError`, 그 값을 지목.

**통합 — 계약을 현실에 고정하는 테스트 (가장 중요)**

12. 실제 `benchmarks/PRA2017/pra2017_bks_table.csv`에 대해
    `{"T": 0.6, "R": 0.2}`가 반환하는 리스트가
    `metadata/20260725/coarsening_crossover.yaml`의 `ins_index` 160개와
    **집합으로 완전히 일치**한다. 이 테스트가 없으면 "이 필터가 그 셀이다"가
    검증되지 않은 주장으로 남는다.
13. 같은 필터의 결과 길이가 160이고, `{"T": 0.6}`은 480이다 (격자 산술 확인).

**엔드투엔드**

```sh
# C4 config가 160개를 그대로 집는지 (실행 없이 로딩까지만)
uv run python -c "
from main import _load_config
from ffc_ddw_sum_et.orchestration import resolve_ins_index
c = _load_config('metadata/20260801/neh_cp_seq_full_compare.yaml')
print(len(resolve_ins_index(c)))
"   # -> 160
```

**정리**: `uv run ruff check`, `uv run ruff format`.
**회귀**: `uv run pytest tests/orchestration tests/scripts -q`.

## 7. 범위 밖 / 위험

- **기존 12개 config는 마이그레이션하지 않는다.** 각각이 과거 run setting
  커밋과 짝지어진 실행 이력이고, 지금 바꿔도 얻는 게 없다. 앞으로 쓰는
  config만 `ins_filter`를 쓴다. 만약 나중에 일괄 이관한다면 테스트 12가
  그 동치성 검증을 그대로 해준다.
- **`scripts/20260727/build_exp3_config.py`의 config-간 참조는 남겨둔다.**
  이미 실행이 끝난 실험의 생성 스크립트라 재실행 가치가 없다. 새 빌더
  스크립트를 쓸 일이 생기면 `ins_filter`를 직접 emit하면 된다.
- **재현성이 `pra2017_bks_table.csv`에 의존한다.** 해석된 리스트가 아니라
  필터 표현식이 run 디렉터리에 남으므로, bks_table의 `T`/`R` 컬럼이 바뀌면
  같은 config가 다른 집합을 집는다. 이 CSV는 git 추적되고 생성 격자가
  고정되어 있어 실질 위험은 낮다. 완화책으로 C2의 로그가 해석 결과 개수를
  run 로그에 남긴다.
- **부분 격자 실험은 여전히 `ins_index`가 필요하다.**
  `metadata/20260705/sw_cp_tl_profile_t8_bigN.yaml`의 190개처럼 격자 슬라이스가
  아닌 임의 집합이 실재한다 (T/R/n/c 어느 축으로도 정렬되지 않음). 그래서
  `ins_filter`는 `ins_index`의 **대체가 아니라 추가**다.
- **필터 차원은 bks_table 컬럼으로 한정한다.** Rep(복제 번호)는 컬럼이 아니라
  파일명에만 있으므로 필터 대상이 아니다. 필요해지면 그때 파일명 파싱을
  더한다 (YAGNI).

## 8. 산출물

커밋 (Conventional Commits, 논리 단위 2개, 각각 green):

1. `feat(orchestration): select instances by param filter` — C1 + C2 + C3 +
   테스트 1–13. 이 시점에 기존 config는 전부 그대로 동작한다.
2. `refactor(metadata): use ins_filter for the T06R02 cell` — C4.

실험이 아니므로 별도 실행 산출물은 없다.

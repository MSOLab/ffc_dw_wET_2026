# Plan: PRA2017 ↔ hybridflowshop Instance Matcher + Format Docs

## Context

두 디렉토리에 동일한 1440개 인스턴스가 서로 다른 파일명·형식으로 저장되어 있다.
벤치마크 결과를 cross-reference하거나 best sequence를 조회할 때 두 쪽을 연결하는 코드가 필요하다.

- `benchmarks/PRA2017/large/` — `Instance_50_5_3_0,2_0,2_10_Rep0.txt` 형식
- `~code/hybridflowshop/resources/pra/` — `1.txt` ~ `1440.txt` 형식

확인된 대응: `Instance_50_5_3_0,2_0,2_10_Rep0.txt` ↔ `1.txt`

---

## 1. Format Docs

`benchmarks/PRA2017/FORMAT.md` 및 `benchmarks/hybridflowshop_pra_FORMAT.md` 신규 작성.

---

## 2. Matching Strategy

처리 시간 행렬을 **내용 기반 해시**로 매칭한다.
- LBCmax, RELDUE, DDW 섹션은 매칭에 불필요
- O(n) 인덱스 구축, O(1) 조회
- 결과: `benchmarks/PRA2017/pra2017_hybrid_match.csv`

**`benchmarks/PRA2017/match_hybrid.py`** (모듈 + 스크립트)

```python
def parse_pra2017_times(path) -> tuple[tuple[int,...],...]
def parse_hybrid_times(path) -> tuple[tuple[int,...],...]
def build_hybrid_index(hybrid_dir) -> dict[tuple, int]
def match_pra2017_to_hybrid(pra_path, index) -> int | None
# __main__: CSV 생성
```

---

## Critical Files

| 파일 | 역할 |
|------|------|
| `benchmarks/PRA2017/large/Instance_50_5_3_0,2_0,2_10_Rep0.txt` | 기준 PRA2017 인스턴스 |
| `~/code/hybridflowshop/resources/pra/1.txt` | 대응 hybridflowshop 인스턴스 |
| `benchmarks/PRA2017/FORMAT.md` | 신규: PRA2017 형식 문서 |
| `benchmarks/hybridflowshop_pra_FORMAT.md` | 신규: hybridflowshop pra 형식 문서 |
| `benchmarks/PRA2017/match_hybrid.py` | 신규: 매칭 코드 + CSV 생성 |
| `benchmarks/PRA2017/pra2017_hybrid_match.csv` | 신규: 1440행 매칭 결과 |

---

## Verification

```bash
uv run python benchmarks/PRA2017/match_hybrid.py
# Expected: "Written 1440 matches to ..."
```

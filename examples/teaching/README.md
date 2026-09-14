# SEOCHO Teaching Curriculum

Knowledge graph + ontology-aware agents — 5 챕터 강의 노트북 + Reveal.js 슬라이드 + 깊이 부속 문서.
Canonical repo path: `examples/teaching/`.

## 빠른 시작

### 옵션 A — Colab

좌측 🔑 **Secrets** 패널에 다음 키를 등록 (notebook access ON):

| Secret name | 필수? | 값 |
|---|---|---|
| `OPENAI_API_KEY` | 필수 | OpenAI 키 |
| `NEO4J_URI` | Ch 1·2 필수 | `bolt://...` |
| `NEO4J_PASSWORD` | Ch 1·2 필수 | DB 비밀번호 |
| `MOONSHOT_API_KEY` | 선택 | Kimi K2.5 |
| `DEEPSEEK_API_KEY` | 선택 | DeepSeek |
| `XAI_API_KEY` | 선택 | Grok |

그 다음 노트북 첫 셀에서:

```python
!pip install --upgrade seocho==0.4.0 datasets openai-agents neo4j python-dotenv
```

Drive 마운트 + 폴더 이동은 `chapter-00-setup.ipynb` 의 첫 두 셀이 자동 처리.

### 옵션 B — 로컬

```bash
cd examples/teaching
cp .env.example .env          # .env 편집하여 키 채우기
pip install -e ../..          # 로컬 seocho dev 버전 (또는 pip install seocho)
pip install datasets openai-agents neo4j

# 환경 자가 진단 (선택)
python -m _shared.preflight
```

## 폴더 구조

```
examples/teaching/
├── README.md                       ← 이 파일
├── .env.example                    ← 키 템플릿
├── .gitignore                      ← data/, traces/
├── colab_bootstrap.md              ← Colab 전용 가이드
│
├── chapter-00-setup.ipynb          ← 환경 + FinDER + 4-provider smoke
│
├── chapter-01-knowledge-graph-indexing.{md,ipynb,-slides.html}
├── chapter-01-property-design.md   ← 7-카테고리 property + temporal sanity
│
├── chapter-02-knowledge-graph-qualification.{md,ipynb,-slides.html}
├── chapter-02-gds-engineering.md   ← 시간복잡도 + 메모리 + 재계산 정책
│
├── chapter-03-text2cypher.{md,ipynb,-slides.html}
├── chapter-03-cypher-failure-taxonomy.md   ← 12-패턴 + validator
│
├── chapter-04-routing-agent.{md,ipynb,-slides.html}
├── chapter-04-routing-decision-design.md   ← 결정 트리 + 임계값 + staleness
│
├── chapter-05-debate-pool.{md,ipynb,-slides.html}
├── chapter-05-debate-convergence-analysis.md   ← 수렴 + early-stop + anti-pattern
│
├── _shared/                        ← 강의 공통 헬퍼
│   ├── trace_setup.py               ← JSONL 트레이싱
│   ├── providers.py                ← 4-provider 인터페이스
│   ├── finder_loader.py            ← SDK 우선, fallback inline
│   ├── slide_template.py           ← Reveal.js 빌더
│   ├── compat.py                   ← slice_ontology 호환 shim
│   ├── preflight.py                ← `python -m _shared.preflight`
│   └── build_slides_chXX.py        ← 슬라이드 데크 빌더 (5개)
│
├── data/                           ← gitignored: finder_corpus.parquet 캐시
└── traces/                         ← gitignored: 챕터별 JSONL 트레이스

The teaching arc loads the canonical FIBO TTL from
`examples/datasets/fibo_be_minimal.ttl` — there is no per-chapter copy.
```

## 챕터 진행 순서

| # | 챕터 | 핵심 내용 | 시간 (예상) |
|---|---|---|---|
| 0 | Setup | provider/JSONL/FinDER/Neo4j 검증 | 5분 |
| 1 | Indexing | 3-layer LPG · ontology slice · Louvain · property design · temporal sanity | 60분 |
| 2 | Qualification | GDS 4지표 · `@function_tool` · agent reasoning | 50분 |
| 3 | Text2Cypher | 3-블록 prompt · 12-패턴 validator · TTL Korean labels | 50분 |
| 4 | Routing | 4-axis augmentation · RRF · citation envelope · staleness | 50분 |
| 5 | Debate | single / self-reflect / multi-LLM / hybrid · convergence curve | 60분 |

## 발표용 슬라이드

```bash
# 슬라이드 데크는 정적 HTML. 다시 빌드하려면:
cd examples/teaching
python -m _shared.build_slides_ch01
python -m _shared.build_slides_ch02
# ... ch03, ch04, ch05

# 또는 한 줄로
for i in 01 02 03 04 05; do python -m _shared.build_slides_ch$i; done
```

브라우저에서 `chapter-0X-...-slides.html` 열면 Reveal.js 데크.

## 로컬 트레이스

각 챕터는 `traces/chapter_NN.jsonl`에 기록합니다. `SEOCHO_USER_ID`와
workspace metadata로 실행 주체를 구분합니다. 별도 계정은 필요하지 않습니다.
SEOCHO가 계측한 span만 기록되며, Agents SDK의 모든 이벤트·reasoning·비용이
자동 수집된다는 의미는 아닙니다. 강의의 분석 항목은 기록된 필드가 있을 때만
계산하며, 누락된 telemetry는 별도 계측 과제로 남깁니다.

## 4-Provider 비교

| Provider | Model | Key env | 특성 |
|---|---|---|---|
| OpenAI | gpt-4o-mini | `OPENAI_API_KEY` | baseline, tool-use 안정 |
| Kimi (Moonshot) | kimi-k2.5 | `MOONSHOT_API_KEY` | long-context, 한국어 |
| DeepSeek | deepseek-chat | `DEEPSEEK_API_KEY` | 저비용, JSON-mode |
| Grok (X.AI) | grok-4.20-reasoning | `XAI_API_KEY` | reasoning, fresh web |

`from _shared.providers import compare_providers` → 같은 prompt 를 가용 provider 모두에 보내 응답/토큰/지연 DataFrame.

## FinDER 데이터셋

- HuggingFace: `Linq-AI-Research/FinDER` (5,703 records, split=`train`)
- 8 카테고리: Accounting / CompanyOverview / Financials / Footnotes / Governance / Legal / Risk / ShareholderReturn
- 셋팅 한 번: `from _shared.finder_loader import load_finder; ds = load_finder()` → `data/finder_corpus.parquet` 캐시
- Selectors: `by_category("Risk") · sample_random(5) · sample_per_category(2)`

## SDK 의존성 (0.4.0 이후)

이 강의는 `seocho >= 0.4.0` 의 다음 신규 모듈을 활용합니다:

- `seocho.gds` — 안전 GDS session 헬퍼
- `seocho.routing` — 선언적 routing policy
- `seocho.debate` — convergence telemetry
- `seocho.query.guards` — 12-패턴 Cypher validator
- `seocho.index.sanity` — temporal sanity checks
- `seocho.index.metadata` — property name constants
- `seocho.eval.benchmarks.finder` — FinDER loader

0.3.x 에서도 노트북은 동작 (`_shared/` shim 이 fallback) 하지만 부속 문서의 코드 스니펫은 0.4.0+ 가정.

## 자주 묻는 것

- **provider 키 1개만 있어도?** OpenAI 만 있으면 모두 동작 (4-provider 비교 셀이 1개 행만 출력).
- **FinDER 다운로드 너무 오래?** 첫 실행만 ~30초. 이후 parquet 캐시 (`./data/finder_corpus.parquet`).
- **`ImportError: cannot import name 'is_observability_degraded'`** → `pip install --upgrade seocho>=0.4.0`.
- **Cypher 가 동작 안함** → Neo4j 가 `apoc.*`, `gds.*` procedures 활성화 됐는지 확인 (CLAUDE.md §8).

## 라이선스

부모 SEOCHO 리포지토리와 동일 (MIT).

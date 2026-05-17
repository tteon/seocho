# Colab 사용 가이드

Colab 에서 teaching-resource 노트북을 돌릴 때 첫 셀에 아래 블록 하나만 붙이면 환경이 준비됩니다.

## 1단계 — 자격증명 (Colab Secrets 사용 권장)

좌측 🔑 아이콘 → **Secrets** 에서 다음 키들을 등록 (notebook access ON):

| Secret name | 값 |
|---|---|
| `OPENAI_API_KEY` | OpenAI 키 |
| `OPIK_API_KEY`   | Opik 키 |
| `NEO4J_URI`      | `bolt://...` |
| `NEO4J_PASSWORD` | DB 비밀번호 |
| `MOONSHOT_API_KEY` | (선택) Kimi |
| `DEEPSEEK_API_KEY` | (선택) DeepSeek |
| `XAI_API_KEY`      | (선택) Grok |

## 2단계 — 노트북 상단 부트스트랩 셀

```python
# === Colab bootstrap ===
import os, sys, subprocess

# (a) seocho SDK 설치 (PyPI 최신)
subprocess.run([sys.executable, '-m', 'pip', 'install', '-q',
                'seocho', 'datasets', 'opik', 'python-dotenv', 'neo4j', 'pandas',
                'openai-agents'],
               check=True)

# (b) teaching-resource _shared 모듈 (개별 업로드 또는 git clone)
#  옵션 1: GitHub 에서 raw 파일 가져오기  (저장소 공개일 때)
#    !git clone -q https://github.com/<your-org>/seocho-teaching /content/teaching
#    %cd /content/teaching/teaching-resource
#  옵션 2: 좌측 파일 패널에 teaching-resource/_shared/ 와 .env.example 업로드 후
#    %cd /content/teaching-resource
HERE = '/content/teaching-resource'  # 환경에 맞게 수정
if HERE not in sys.path:
    sys.path.insert(0, HERE)
os.chdir(HERE)

# (c) 자격증명 — Colab Secrets 패널에 등록한 값을 환경변수로 옮긴다
from google.colab import userdata

def _set(env_key, secret_key=None, default=None):
    val = userdata.get(secret_key or env_key) if userdata else None
    if val:
        os.environ[env_key] = val
    elif default is not None:
        os.environ[env_key] = default

_set('OPENAI_API_KEY')
_set('OPIK_API_KEY')
_set('MOONSHOT_API_KEY')
_set('DEEPSEEK_API_KEY')
_set('XAI_API_KEY')
_set('OPIK_WORKSPACE', default='seocho')
_set('OPIK_USER',      default='hardy')   # 본인 식별자
_set('NEO4J_URI')
_set('NEO4J_USER',     default='neo4j')
_set('NEO4J_PASSWORD')

# (d) 빠른 점검
from _shared.providers import providers_overview
providers_overview()
```

기대 결과: `providers_overview()` 의 `configured` 컬럼이 등록한 키만큼 True 로 바뀌어 있어야 합니다.

## 잘 안 될 때 체크리스트

1. **configured 가 모두 False** → Secrets 에 *notebook access* 가 OFF. 좌측 패널에서 각 secret 의 토글 확인.
2. **`google.colab.userdata` ImportError** → Colab 외 환경. `os.environ['KEY'] = '...'` 로 직접 할당.
3. **`Path.cwd()` 가 `/content` 이고 _shared import 실패** → 위 부트스트랩에서 `os.chdir(HERE)` + `sys.path.insert(0, HERE)` 두 줄 모두 실행됐는지 확인.
4. **seocho 신규 모듈 (gds/sanity/routing/debate/guards) ImportError** → PyPI 릴리스가 아직 우리 변경을 반영하지 않은 상태. 그래도 챕터 노트북 본문은 작동 (해당 모듈을 직접 import 하지는 않고, 부속 .md 에만 등장).
5. **`!export` 로 했었다** → 동작 X. 각 `!` 는 새 subshell. 반드시 Python 셀에서 `os.environ[...] = ...`.

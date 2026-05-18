#!/usr/bin/env bash
# code2e 개발 환경 setup — 팀원 누구나 동일 결과로 실행 가능하도록.
# Usage: ./scripts/setup-dev.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$REPO_ROOT/.venv"

echo "[1/5] Python 3.12+ 확인"
if ! command -v python3.12 >/dev/null 2>&1 && ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
    echo "  ✗ Python 3.12+ 필요. 'brew install python@3.12' 또는 pyenv 사용 후 재실행." >&2
    exit 1
fi
PY="$(command -v python3.12 || command -v python3)"
echo "  ✓ $PY ($($PY --version))"

echo "[2/5] .venv 생성"
if [ ! -d "$VENV_DIR" ]; then
    "$PY" -m venv "$VENV_DIR"
    echo "  ✓ created $VENV_DIR"
else
    echo "  ✓ 이미 존재 — 재사용"
fi

echo "[3/5] 패키지 설치 (editable + dev + demo)"
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -e "$REPO_ROOT[dev,demo]"
echo "  ✓ code2e + fastapi + uvicorn 등 설치"

echo "[4/5] activate 에 PYTHONPATH 박기 (macOS provenance xattr 영구 대비)"
# macOS Sequoia(15+) 가 .pth 파일에 com.apple.provenance xattr 을 시간차로 자동 부착하면
# Python 이 hidden 으로 처리해 editable install 이 깨짐. detect 후 박는 방식은 timing 의존
# 적이라 비결정적 → 무조건 박는다. Linux 에선 부작용 없음 (PYTHONPATH 한 줄 추가만).
ACTIVATE="$VENV_DIR/bin/activate"
MARKER="# code2e: PYTHONPATH workaround (idempotent)"
if ! grep -qF "$MARKER" "$ACTIVATE"; then
    {
        echo ""
        echo "$MARKER"
        echo "if [[ \":\$PYTHONPATH:\" != *\":$REPO_ROOT/src:\"* ]]; then"
        echo "    export PYTHONPATH=\"$REPO_ROOT/src\${PYTHONPATH:+:\$PYTHONPATH}\""
        echo "fi"
    } >> "$ACTIVATE"
    echo "  ✓ PYTHONPATH 라인 추가됨"
else
    echo "  ✓ 이미 박혀 있음 (idempotent)"
fi

echo "[5/5] .env 안내"
if [ ! -f "$REPO_ROOT/.env" ]; then
    cp "$REPO_ROOT/.env.example" "$REPO_ROOT/.env"
    echo "  ✓ .env 생성됨. ANTHROPIC_API_KEY 를 채워주세요:"
    echo "      $REPO_ROOT/.env"
    echo "    (콘솔 https://console.anthropic.com → Settings → API Keys 에서 발급)"
else
    echo "  ✓ .env 존재 — 키 채워졌는지 확인하세요"
fi

cat <<EOF

------------------------------------------------------------
setup 완료. 사용법:

    source $VENV_DIR/bin/activate
    python -m code2e doctor     # 환경 점검
    python -m code2e run "Hello world FastAPI endpoint" --budget-usd 1

Playwright 브라우저가 처음이면:
    playwright install chromium
------------------------------------------------------------
EOF

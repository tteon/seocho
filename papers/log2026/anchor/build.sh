#!/usr/bin/env bash
# 빌드 + 검증 + 버전 파일명 사본. 항상 이 스크립트로 빌드할 것.
#   papers/log2026/anchor/build.sh "수정내용-슬러그"
set -euo pipefail
cd "$(dirname "$0")"
SLUG="${1:?사용법: build.sh <수정내용-슬러그>}"
tectonic paper.tex >/dev/null
../../../.venv/bin/python check_numbers.py >/dev/null || { echo "수치 접지 실패 — 사본 생성 안 함"; exit 1; }
H=$(md5sum paper.pdf | cut -c1-8)
TS=$(date +%m%d_%H%M)
OUT="paper_${TS}_${SLUG}_${H}.pdf"
cp paper.pdf "versions/$OUT" 2>/dev/null || { mkdir -p versions; cp paper.pdf "versions/$OUT"; }
echo "정본:   $(pwd)/paper.pdf  (해시 $H)"
echo "버전본: $(pwd)/versions/$OUT"

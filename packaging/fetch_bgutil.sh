#!/bin/bash
# PO 토큰 실행 파일 bgutil-pot 을 bin/ 에 받는다 (어린이용 유튜브 영상 403 대응).
# 출처: https://github.com/jim60105/bgutil-ytdlp-pot-provider-rs (GPL-3.0). 버전과 SHA-256 을 고정해 둔다.
set -euo pipefail
cd "$(dirname "$0")/.."
VER=v0.8.1
case "$(uname -s)-$(uname -m)" in
  Darwin-arm64)  ASSET=bgutil-pot-macos-aarch64;  SHA=34b83baf0a557fecaa6d67a8177e53e169c2ccf987182883a4bae289a7176883; OUT=bin/bgutil-pot ;;
  Darwin-x86_64) ASSET=bgutil-pot-macos-x86_64;   SHA=0391175fa938c7fabbb8b40a40bd43182ef75af97e1dd3fab56eb23b4ac3e113; OUT=bin/bgutil-pot ;;
  *) echo "이 OS 는 fetch_bgutil.sh 대상이 아닙니다 (윈도우는 CI 의 pwsh 단계)"; exit 0 ;;
esac
mkdir -p bin
if [ -f "$OUT" ] && [ "$(shasum -a 256 "$OUT" | cut -d' ' -f1)" = "$SHA" ]; then echo "이미 있음: $OUT"; exit 0; fi
TMP=$(mktemp -d)
gh release download "$VER" -R jim60105/bgutil-ytdlp-pot-provider-rs -p "$ASSET" --dir "$TMP"
GOT=$(shasum -a 256 "$TMP/$ASSET" | cut -d' ' -f1)
[ "$GOT" = "$SHA" ] || { echo "SHA-256 불일치: $GOT"; exit 1; }
mv "$TMP/$ASSET" "$OUT"; chmod +x "$OUT"; rm -rf "$TMP"
echo "받음: $OUT ($VER)"

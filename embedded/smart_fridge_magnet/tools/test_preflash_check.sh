#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

"$SCRIPT_DIR/preflash_check.sh" --idf-version 6.0.2 >/dev/null
if "$SCRIPT_DIR/preflash_check.sh" --idf-version 5.5.2 >/dev/null 2>&1; then
    echo "版本不匹配测试未失败" >&2
    exit 1
fi
echo "PREFLASH CHECK PASSED"

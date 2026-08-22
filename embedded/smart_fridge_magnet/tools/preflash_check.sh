#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PINNED_FILE="${PROJECT_DIR}/.espidf-version"
MANUAL_VERSION=""

usage() {
    cat <<'EOF'
用法: preflash_check.sh [--idf-version <版本>] [--no-record]

校验活动 ESP-IDF 是否与 .espidf-version 一致。校验失败时必须停止后续烧录命令。
--idf-version 用于没有 idf.py 的离线检查；--no-record 保留兼容参数但不写入任何状态文件。
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --idf-version) MANUAL_VERSION="${2:-}"; shift 2 ;;
        --no-record) shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "未知参数: $1" >&2; usage >&2; exit 64 ;;
    esac
done

extract_version() {
    sed -n 's/.*v\{0,1\}\([0-9][0-9]*\.[0-9][0-9]*\(\.[0-9][0-9]*\)\{0,1\}\).*/\1/p' | head -n 1
}

expected="$(extract_version < "$PINNED_FILE")"
if [[ -z "$expected" ]]; then
    echo "无法读取 $PINNED_FILE" >&2
    exit 2
fi

if [[ -n "$MANUAL_VERSION" ]]; then
    actual="$(printf '%s\n' "$MANUAL_VERSION" | extract_version)"
else
    command -v idf.py >/dev/null 2>&1 || { echo "idf.py 不在 PATH 中" >&2; exit 2; }
    actual="$(idf.py --version 2>/dev/null | extract_version)"
fi

if [[ -z "$actual" ]]; then
    echo "无法解析活动 ESP-IDF 版本" >&2
    exit 2
fi
if [[ "$actual" != "$expected" ]]; then
    echo "ESP-IDF 版本不匹配: 期望 v$expected，实际 v$actual" >&2
    exit 3
fi
echo "ESP-IDF 版本校验通过: v$actual"

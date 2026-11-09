#!/usr/bin/env bash
# 下载 VerovoBench v0.2 数据集（5600 实例，seed 42）到本目录。
# 数据文件已移出 git 树（2026-11-09），两条等效获取路径：
#   1. 本脚本：从 GitHub Release 下载并校验 sha256
#   2. 本地再生：python3 generator.py --full --seed 42   # bit-exact 同一文件
# 这是显式的用户操作；generator/validate/eval_reconciler 本身仍不访问网络。
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/verovobench-instances.jsonl"
URL="https://github.com/roy-tong/Verovo/releases/download/verovobench-data-v0.2/verovobench-instances.jsonl"
SHA256="31b76a7053e97ddc8a67c3c0865d1d6045086bad049d67bc92495007e39d7bb6"

if [ -f "$OUT" ]; then
  echo "已存在 $OUT（如需重新下载请先删除）" >&2
  exit 0
fi

echo "下载 $URL"
curl -L --fail --progress-bar -o "$OUT" "$URL"

actual="$(shasum -a 256 "$OUT" 2>/dev/null || sha256sum "$OUT")"
actual="${actual%% *}"
if [ "$actual" != "$SHA256" ]; then
  echo "sha256 校验失败：期望 $SHA256，实际 $actual；已删除损坏文件" >&2
  rm -f "$OUT"
  exit 1
fi
echo "OK：$OUT（sha256 校验通过，5600 实例 / 约 90MB）"

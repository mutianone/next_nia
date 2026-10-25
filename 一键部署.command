#!/bin/bash
cd "$(dirname "$0")" || exit 1
bash scripts/deploy-mac.sh
result=$?
printf '\n'
if [ "$result" -ne 0 ]; then printf '部署未完成（退出码 %s），请查看上方错误。\n' "$result"; fi
read -r -p '按回车关闭窗口…' _
exit "$result"

#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET='root@81.70.166.164'
REMOTE_ROOT='/root/nia'
RELEASE="$(date -u +%Y%m%dT%H%M%SZ)-$$"
source "$PROJECT_ROOT/scripts/common.sh"
check_platform
# Finder may inherit an old Node even when newer runtimes are installed.
source "$PROJECT_ROOT/scripts/deploy-node.sh"
select_deploy_node
check_node
for tool in ssh scp tar git; do command -v "$tool" >/dev/null || fail "缺少命令：$tool"; done
[ "$#" -eq 0 ] || { [ "$#" -eq 1 ] && [ "${1:-}" = '--package-only' ]; } || fail '仅支持 --package-only（只构建打包，不连接服务器）。'
TMP_DEPLOY="$(mktemp -d /tmp/nia-deploy.XXXXXX)"
CONTROL="$TMP_DEPLOY/ssh"
CONNECTED=0
cleanup() {
  if [ "$CONNECTED" = 1 ]; then ssh -S "$CONTROL" -O exit "$TARGET" >/dev/null 2>&1 || true; fi
  rm -rf "$TMP_DEPLOY"
}
trap cleanup EXIT
cd "$PROJECT_ROOT"
printf '\n[1/4] 安装锁定依赖并构建页面…\n'
npm ci --no-audit --no-fund --fetch-timeout=30000 --fetch-retries=1
npm run build
printf '\n[2/4] 打包代码（不包含本机业务数据、密码或 API Key）…\n'
# Explicit allowlist: never archive the project root or local data directories.
mkdir -p "$TMP_DEPLOY/package/backend/app" "$TMP_DEPLOY/package/backend/assets" "$TMP_DEPLOY/package/scripts"
cp -R dist "$TMP_DEPLOY/package/dist"
cp backend/requirements.txt "$TMP_DEPLOY/package/backend/"
find backend/app -maxdepth 1 -name '*.py' -exec cp {} "$TMP_DEPLOY/package/backend/app/" \;
# Only tracked assets are eligible; local untracked material is not uploaded.
git ls-files -z backend/assets | while IFS= read -r -d '' asset; do
  mkdir -p "$TMP_DEPLOY/package/$(dirname "$asset")"
  cp "$asset" "$TMP_DEPLOY/package/$asset"
done
cp scripts/deploy-server.sh "$TMP_DEPLOY/package/scripts/"
COPYFILE_DISABLE=1 tar -czf "$TMP_DEPLOY/release.tar.gz" -C "$TMP_DEPLOY/package" .
if [ "${1:-}" = '--package-only' ]; then
  mkdir -p "$PROJECT_ROOT/output"
  cp "$TMP_DEPLOY/release.tar.gz" "$PROJECT_ROOT/output/nia-$RELEASE.tar.gz"
  printf '部署包已生成：%s/output/nia-%s.tar.gz\n' "$PROJECT_ROOT" "$RELEASE"
  exit 0
fi
printf '\n[3/4] 连接 %s；请输入服务器 SSH 密码（输入不显示）。\n首次连接还需核对并接受主机指纹。\n' "$TARGET"
# OpenSSH prompts directly in Terminal; no password is stored or passed as an argument.
ssh -M -S "$CONTROL" -o ControlPersist=600 -o ServerAliveInterval=30 -o ServerAliveCountMax=6 -fN "$TARGET"
CONNECTED=1
ssh -S "$CONTROL" "$TARGET" "umask 077; mkdir -p '$REMOTE_ROOT/incoming/$RELEASE'"
scp -o ControlPath="$CONTROL" "$TMP_DEPLOY/release.tar.gz" "$TARGET:$REMOTE_ROOT/incoming/$RELEASE/release.tar.gz"
scp -o ControlPath="$CONTROL" scripts/deploy-server.sh "$TARGET:$REMOTE_ROOT/incoming/$RELEASE/deploy-server.sh"
printf '\n[4/4] 安装服务并执行健康检查…\n'
ssh -S "$CONTROL" "$TARGET" "bash '$REMOTE_ROOT/incoming/$RELEASE/deploy-server.sh' '$RELEASE'"
printf '\n部署完成：http://81.70.166.164:5173\n若无法访问，请在云服务器安全组放行 TCP 5173（建议限定访问 IP）。\n首次管理员：18792636900 / 123456，请登录后修改密码。已有账号密码保持不变。\n'

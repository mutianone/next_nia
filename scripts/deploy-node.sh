#!/usr/bin/env bash
# Select a compatible Node/npm pair for this process only; never source shell profiles.
select_deploy_node() {
  local current candidate npm_path npm_dir
  current="$(command -v node 2>/dev/null || true)"
  for candidate in \
    "${current%/node}" \
    "$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin" \
    /opt/homebrew/opt/node@24/bin /usr/local/opt/node@24/bin \
    /opt/homebrew/opt/node@22/bin /usr/local/opt/node@22/bin \
    /opt/homebrew/bin /usr/local/bin \
    "${NVM_DIR:-$HOME/.nvm}"/versions/node/*/bin; do
    [ -n "$candidate" ] && [ -x "$candidate/node" ] || continue
    # The locked toolchain includes dependencies requiring 22.14+ or 24+.
    "$candidate/node" -e 'const [major, minor] = process.versions.node.split(".").map(Number); process.exit((major === 22 && minor >= 14) || major >= 24 ? 0 : 1)' >/dev/null 2>&1 || continue
    # Some bundled runtimes provide Node only; use an installed npm with that Node.
    for npm_path in "$candidate/npm" /opt/homebrew/bin/npm /usr/local/bin/npm \
      "${NVM_DIR:-$HOME/.nvm}"/versions/node/*/bin/npm "$(command -v npm 2>/dev/null || true)"; do
      [ -x "$npm_path" ] || continue
      npm_dir="$(dirname "$npm_path")"
      if ! PATH="$candidate:$npm_dir:$PATH" "$npm_path" --version >/dev/null 2>&1; then continue; fi
      export PATH="$candidate:$npm_dir:$PATH"
      hash -r
      printf '部署构建使用 Node.js %s（%s）\n' "$(node --version)" "$candidate/node"
      return 0
    done
  done
  printf '错误：未找到兼容的 Node.js/npm。请安装 Node.js 24 LTS 后重试；脚本不会更改默认 Node.js。\n' >&2
  return 1
}

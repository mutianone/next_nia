# Mac 双击部署

在完整项目文件夹中双击根目录 **一键部署.command**，输入 `root@81.70.166.164` 的 SSH 密码即可。密码由系统 SSH 读取，不写入脚本、日志或仓库。首次连接还需要核对服务器指纹并输入 `yes`；已配置 SSH 密钥的电脑可能无需密码。脚本结束后显示结果，按回车关闭。

- 服务器目录：`/root/nia`
- 访问地址：`http://81.70.166.164:5173`
- 云安全组和主机防火墙需要允许 TCP 5173，建议限定来源 IP；脚本不修改已有防火墙规则。
- 新建数据库内置管理员：`18792636900 / 123456`，首次登录请修改密码。已有账号密码不重置。
- 首次部署是空业务库，请在“系统配置”中填写服务器使用的 AI 密钥。不会上传 Mac 上的账号、业务资料、录音、数据库或密钥。

## 环境要求

Mac 需要 Node.js 22.14+（22.x）或 24+（推荐 24 LTS），以及 Git/OpenSSH。脚本会检查当前版本；遇到旧版本会继续查找 Codex、Homebrew、nvm 中的兼容 Node/npm，仅为本次部署调整 PATH，不修改系统默认版本或 shell 配置。使用 `npm ci` 安装锁定依赖，然后本机构建，不将 Mac 虚拟环境或 node_modules 传到 Linux。

服务器需要 Linux + systemd，推荐 **Ubuntu 24.04 / Debian 12**，预先具备 Python 3.11+（含 venv/ensurepip）、FFmpeg、curl、ss、flock。为避免影响其他服务，脚本**不运行 apt/dnf、不安装或升级系统软件、不修改防火墙或 Nginx**。依赖缺失时明确停止，由服务器管理员单独准备；应用 Python 依赖只安装在版本目录的虚拟环境里。

只管理本部署的 `nia.service`。若 5173 端口被其他进程占用，或同名服务不属于本部署，则停止，不杀进程、不覆盖其他服务。升级旧的 8080 部署时，仅将本应用改到 5173。构建需要访问 npm（Mac）和 PyPI（服务器）；AI 功能需要服务器可访问已配置的 AI 接口。

## 更新、目录与恢复

每次双击都会重新构建、安装新版本依赖、执行隔离数据库导入检查，再停止旧服务、备份数据并切换：

```
/root/nia/current                 当前版本的符号链接
/root/nia/releases/<版本编号>/     代码、静态文件、独立 Python 环境
/root/nia/data/                    持久数据及 AI 配置，不随更新覆盖
/root/nia/backups/                 停机后的数据目录备份、旧服务配置
/root/nia/incoming/                上传包及部署脚本
```

更新会短暂停机。健康检查失败时尝试恢复旧代码并启动旧服务，首次部署失败则停止服务；**不自动回滚业务数据库**。数据库发生不兼容迁移时需要人工从备份恢复。备份只覆盖本地 data 目录；如果已切换到 PostgreSQL，须另行使用 `pg_dump` 备份数据库。旧版本、上传包和备份不自动删除，应按磁盘容量定期维护。

服务使用 systemd 开机自启，单进程提供网页及 API，支持流式响应；没有启动 Vite 开发服务器。因目标目录在 `/root`，服务以 root 运行。服务日志保存在 journal：

```bash
ssh root@81.70.166.164
systemctl status nia --no-pager
journalctl -u nia -n 80 --no-pager
systemctl restart nia
```

仅构建部署包、不连接服务器：

```bash
bash scripts/deploy-mac.sh --package-only
```

若 Finder 提示无法执行，可在终端运行 `chmod +x 一键部署.command`，或直接运行 `bash scripts/deploy-mac.sh`。

## HTTPS 与浏览器录音

IP + HTTP 可访问普通页面，但不是安全上下文，浏览器通常不允许直接麦克风录音；上传已有录音仍可用。正式外网使用需配置域名和受信任 HTTPS 反向代理（流式接口应关闭代理缓冲）。当前未提供域名/证书，因此脚本不自动申请证书。临时在 Mac 上测试录音可建立 SSH 隧道，随后访问 `http://localhost:15173`：

```bash
ssh -N -L 15173:127.0.0.1:5173 root@81.70.166.164
```

本脚本已做本地构建打包与语法检查；实际服务器的发行版、软件源、网络和端口需首次运行验证。

## 当前服务器的 FFmpeg

2026-10-25 已在目标服务器安装隔离版 FFmpeg 7.0.2，入口为 `/root/nia/tools/bin/ffmpeg`。程序与依赖来自服务器已配置软件源的签名 RPM，仅解包到私有目录，没有执行 RPM 安装脚本、注册额外服务或创建全局命令。WAV、WebM 转单声道 16 kHz MP3 已验证。

部署脚本优先检测此入口，并通过 `NEXT_NIA_FFMPEG_BIN` 仅提供给 NIA 服务；不需要修改系统 PATH 或其他服务。请保留 `/root/nia/tools` 目录。

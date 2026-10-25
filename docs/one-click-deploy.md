# Mac 双击部署

在完整项目文件夹中双击根目录 **一键部署.command**，输入 `root@81.70.166.164` 的 SSH 密码即可。密码由系统 SSH 读取，不写入脚本、日志或仓库。首次连接还需要核对服务器指纹并输入 `yes`；已配置 SSH 密钥的电脑可能无需密码。脚本结束后显示结果，按回车关闭。

- 服务器目录：`/root/nia`
- 访问地址：`http://81.70.166.164:8080`
- 云安全组和主机防火墙需要允许 TCP 8080，建议限定来源 IP；脚本不修改已有防火墙规则。
- 新建数据库内置管理员：`18792636900 / 123456`，首次登录请修改密码。已有账号密码不重置。
- 首次部署是空业务库，请在“系统配置”中填写服务器使用的 AI 密钥。不会上传 Mac 上的账号、业务资料、录音、数据库或密钥。

## 环境要求

Mac 需要 Node.js 20.19+（20.x）或 22.12+，以及 Git/OpenSSH。脚本识别 Homebrew 和当前 Codex 的 Node 运行时路径。使用 `npm ci` 安装锁定依赖，然后本机构建，不将 Mac 虚拟环境或 node_modules 传到 Linux。

服务器需要 Linux + systemd，推荐 **Ubuntu 24.04 / Debian 12**，Python 3.11+。apt 系统自动安装 Python、venv、FFmpeg 等；dnf 系统尝试 Python 3.11 和 FFmpeg，软件源不提供时明确停止，需先补齐相应软件源。不会替换系统 Python。首次安装需要访问系统软件源、npm（Mac）和 PyPI（服务器）；AI 功能需要服务器可访问已配置的 AI 接口。

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

IP + HTTP 可访问普通页面，但不是安全上下文，浏览器通常不允许直接麦克风录音；上传已有录音仍可用。正式外网使用需配置域名和受信任 HTTPS 反向代理（流式接口应关闭代理缓冲）。当前未提供域名/证书，因此脚本不自动申请证书。临时在 Mac 上测试录音可建立 SSH 隧道，随后访问 `http://localhost:18080`：

```bash
ssh -N -L 18080:127.0.0.1:8080 root@81.70.166.164
```

本脚本已做本地构建打包与语法检查；实际服务器的发行版、软件源、网络和端口需首次运行验证。

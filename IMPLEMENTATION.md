# 出入境业务智能辅助平台

## 当前范围：第一阶段

用户确认分阶段开发。仅支持 macOS 和 Linux；本阶段交付可运行基础框架、总览工作台、五套主题的明暗模式、移动布局，以及后台环境与 AI 配置。后续业务功能明确显示规划状态，不伪装成已实现能力。

## 并行开发契约

- 前端：React + TypeScript + Vite。根代理负责 `src/App.tsx`、`src/styles.css`、`src/components/`、`src/pages/Dashboard.tsx`、`src/pages/Roadmap.tsx` 与前端基础配置。
- 配置页代理负责 `src/pages/Settings.tsx`、`src/settings.css`。使用下述 API 和全局 UI 类，不修改其他前端文件。
- 后端代理负责 `backend/`：FastAPI + SQLAlchemy；默认 SQLite，正式环境 PostgreSQL。配置独立保存于 `data/settings.json`（服务端权限 0600；密钥不返回、不记录）。数据库数据隔离，环境修改验证后保存，重启生效，不自动搬迁或覆盖数据。
- 文档与质量代理负责 `README.md`、`docs/`、`scripts/`、`.env.example`、`.gitignore`、部署文件，不修改业务源码。不得编造尚未通过的验证。
- 所有运行数据、密钥、数据库和依赖均忽略提交。无登录页；开发预览默认监听环回地址。

## HTTP 契约

`GET /api/settings` 返回：

```json
{
  "settings": {
    "general": {"system_name":"境智 · 出入境业务平台", "organization":"出入境业务中心"},
    "environment": {"mode":"test", "postgres":{"host":"127.0.0.1","port":5432,"database":"next_nia","username":"postgres","password_configured":false}},
    "ai": {"enabled":false,"default_provider":"siliconflow","providers":{
      "siliconflow":{"enabled":true,"base_url":"https://api.siliconflow.cn/v1","chat_model":"Qwen/Qwen3-8B","api_key_configured":false},
      "deepseek":{"enabled":false,"base_url":"https://api.deepseek.com","chat_model":"deepseek-flash","api_key_configured":false}
    }}
  },
  "runtime":{"active_environment":"test","database_engine":"SQLite","pending_restart":false}
}
```

`PUT /api/settings` 请求同 settings 对象（不包 settings 外层）。可提交 `api_key` 和 `password`；空值代表保留已有密钥；清空使用独立布尔字段 `clear_api_key` / `clear_password`。不要提交 `_configured` 字段。返回同 GET。general 可更新。AI 设置立即生效；环境改变需重启；正式模式先连接检查成功再保存。两个服务商 base_url 固定不可任意填写；请求不能篡改 URL。

`POST /api/settings/test-ai` 请求 `{provider, api_key?, chat_model?}`。仅用服务商固定 models 接口检查凭证和选中模型（不发业务资料）。返回 `{ok, message, models?: string[], latency_ms?}`；无密钥/失败返回适当4xx/5xx，detail 为可展示中文文本，无密钥泄露。

`POST /api/settings/test-database` 请求 postgres 字段（password 可空保留）；SELECT 1，不建表。返回 `{ok,message,latency_ms?}`。

`GET /api/health` 返回 `{status,version,stage,active_environment,database_engine,pending_restart}`。

`GET /api/dashboard` 返回结构见前端类型，后端也可按此返回：
```json
{"demo":true,"updated_at":"ISO","stats":{"people":128,"active_permits":116,"pending_alerts":8,"draft_records":4},"trend":[{"label":"周一","entries":18,"exits":12}],"activities":[{"id":"a1","title":"演示人员档案已整理","description":"仅为第一阶段界面演示数据","time":"09:30","type":"person"}],"alerts":[{"id":"e1","name":"示例人员 A","code":"DEMO-001","category":"停留期限待核查","days":3,"status":"待核查"}]}
```

演示数据不得引用实际旧数据库个人信息。正式环境 dashboard 必须 demo:false + 空统计，不能自动灌入演示数据。

## UI 接口

Settings 默认导出或具名导出 Settings，使用 `onSaved?: () => void` 属性；通过同源 fetch(`/api/...`) 访问后端。API 错误 detail 展示中文，网络失败显示明确状态，不伪造成功。

全局 CSS 变量：`--bg`, `--surface`, `--surface-2`, `--surface-3`, `--text`, `--muted`, `--border`, `--primary`, `--primary-soft`, `--accent`, `--danger`, `--success`, `--warning`, `--radius`, `--shadow`。通用类：`panel`, `btn`, `btn-primary`, `btn-secondary`, `btn-ghost`, `badge`, `field`, `input`, `select`, `muted`, `eyebrow`, `page-title`, `page-description`。配置页自行导入 settings.css，允许 lucide-react 图标。设计精致，响应式，无外部字体、图片、脚本依赖。

## 后续阶段

2. 人员档案、知识导入与法规检索；3. 预警规则与文书模板；4. 中文录音、转写和智能笔录；5. 事实画像、统计、迁移和完整交付。正式文书固定文案等待业务方审定样本。手机安装包不启动。

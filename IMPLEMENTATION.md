# 出入境业务智能辅助平台：实现契约

## 当前版本和范围

当前为 **0.3.0 / stage 3**，仅支持 macOS 和 Linux。已实现基础框架、主题与明暗模式、移动布局、环境与 AI 配置、人员事实档案、停留记录、CSV 交换、法规导入、版本审核、时效检索、受限 AI 法规问答、期限核查预警及本地模板文书。

第三阶段包含本地规则扫描、补充事实、处置历史、模板版本与审核、文书快照、人工复核、复制归档和 TXT/DOCX 导出。第四阶段语音及笔录、第五阶段事实画像与完整迁移交付未启动。正式文书标准文案仍等待业务方审定；内置模板仅为内部核查工作单。手机安装包未启动；当前无登录，仅用于本机验收。

## 架构与数据

- 前端：React + TypeScript + Vite，同源 `fetch('/api/...')`；本地字体、图标与脚本，无 CDN 运行依赖。
- 后端：FastAPI + SQLAlchemy，入口 `backend.app.main:app`。
- 测试环境：SQLite；正式模式：PostgreSQL。所有业务查询使用进程启动时的活动数据库。
- 服务端独立配置：默认 `backend/data/settings.json`，原子保存、权限 `0600`。密钥为本地明文，公开接口只返回是否配置。
- `NEXT_NIA_DATA_DIR` 指定绝对数据目录；不自动读取 `.env`。
- 启动时 `Base.metadata.create_all` 创建不存在的表；不自动迁移已有表结构或跨数据库搬迁资料。
- 人员、停留、法规原件、条款、版本元信息、预警规则/补充事实/历史、文书模板/快照/历史和审计保存在活动数据库中。运行数据、密钥、依赖和模型权重不提交版本库。
- 业务写入与对应审计同事务；通用审计仅记录对象标识、数量等允许的元信息，不写个人详情和密钥。处置备注和复核意见存放在各自业务历史表中。

## 配置 API

`GET /api/settings` 返回 `{settings, runtime}`。`settings` 包含 `general`、`environment`、`ai`，`runtime` 包含当前活动环境、数据库引擎和 `pending_restart`。

`PUT /api/settings` 提交完整设置对象，不包 `settings` 外层。可提交 `api_key` 和 `password`；空值保留已有秘密，清空使用 `clear_api_key` / `clear_password`。请求不包含 `_configured` 字段。系统信息与 AI 配置立即可用，数据库环境改变重启生效；正式模式保存前必须连接验证通过。

`POST /api/settings/test-ai` 接受 `{provider, api_key?, chat_model?}`，只通过固定服务商的模型列表接口检查凭据和模型名，不发送业务资料。

`POST /api/settings/test-database` 接受 PostgreSQL 配置，空密码可保留原值；只执行 `SELECT 1`，不建表。`GET /api/health` 返回状态、`version: "0.3.0"`、`stage: 3` 及活动环境信息。

服务商地址固定：硅基流动 `https://api.siliconflow.cn/v1`，DeepSeek `https://api.deepseek.com`。密钥不得出现在响应、错误或日志中。错误 `detail` 使用可直接展示的中文文本，不暴露底层 SQL 或原始请求内容。

## 人员 API

路由前缀 `/api/people`。

| 方法和路径 | 行为 |
| --- | --- |
| `GET /api/people` | `q` 搜索，`archived=false/true` 筛选，`page=1`、`page_size=20` 分页；每页最多 100 条 |
| `GET /api/people/{id}` | 返回扁平人员对象，附 `stays` 数组 |
| `POST /api/people` | 新建人员，返回详情，状态码 201 |
| `PUT /api/people/{id}` | 完整更新可编辑业务字段，返回详情 |
| `POST /api/people/{id}/archive` | `{archived: bool}`，可逆归档，返回详情 |
| `POST /api/people/{id}/stays` | 新建停留记录，返回该记录，状态码 201 |
| `PUT /api/people/{id}/stays/{stay_id}` | 更新归属该人员的停留记录，返回该记录 |
| `GET /api/people/template.csv` | UTF-8 BOM 模板，虚构示例不写入数据库 |
| `GET /api/people/export.csv` | 导出当前 `q` 和 `archived` 筛选下全部资料，防护电子表格公式注入 |
| `POST /api/people/import/preview` | `{csv_text}` 预检，不写入 |
| `POST /api/people/import` | `{csv_text}` 重新校验后单事务导入，返回 `{imported}` |

列表返回 `{items,total,page,page_size,stats}`。`stats.total` 为全库人数，`verified` / `pending` 为未归档人数，`archived` 为已归档人数。列表对象不含 `stays`，详情包含。

人员必填 `name`、`nationality`、`document_number`、`source`；可编辑字段还包括 `english_name`、`gender`、`birth_date`、`document_type`、`document_expiry`、`phone`、`address`、`purpose`、`verification`、`notes`。`id`、`code`、归档状态及时间戳由服务端管理。

证件号码去除空白后转大写，与类型组合唯一。停留字段包括必填 `entry_date`、`source`，以及 `permitted_until`、`exit_date`、`permit_type`、`permit_number`、`purpose`、`notes`。可选日期接受 `YYYY-MM-DD`、`null` 或空串。只验证事实日期关系，不自动判断违法；未知期限保持 `null`。

CSV 上限 1 MiB、500 数据行。预检返回 `{rows,errors:[{row,message}],total,valid_count}`，`rows` 是归一化有效行。整批有校验错误时不写入，重复返回 409，其他资料错误返回 422，超限拒绝。CSV 列仅为人员可编辑业务字段，不包含服务端生成标识或归档字段。

## 法规知识库 API

路由前缀 `/api/knowledge`。

| 方法和路径 | 行为 |
| --- | --- |
| `GET /api/knowledge` | 标题/文号搜索、状态筛选、分页与统计；返回实际检索模式 |
| `POST /api/knowledge/import` | multipart 文件及元信息；导入为草稿且默认不允许外发 |
| `GET /api/knowledge/{id}` | 元信息及分段原文 |
| `PATCH /api/knowledge/{id}` | 修改元信息、审核发布/归档、外发许可 |
| `GET /api/knowledge/{id}/file` | 下载原件 |
| `POST /api/knowledge/search` | `{query, as_of?, limit?}`，本地检索 |
| `POST /api/knowledge/ask` | `{question, as_of?, confirm_external:false}`，受限法规问答 |

文件支持 TXT、Markdown、可提取文字的 PDF 和 DOCX，单文件最多 10 MiB。扫描 PDF 需预先使用另行部署的本地 OCR，当前未包含 OCR 引擎。

元信息包含标题、版本、发布机关、文号、分类、来源、法规系列标识、发布日期、生效/失效日期、状态与 `allow_external`。发布要求来源和生效日期，禁止同一系列已发布版本的生效区间重叠。版本唯一性和更新审计在本地数据库维护。

检索仅选择已发布且在 `as_of` 日期有效的版本；日期默认北京时间当天。默认 BM25 与字词精确匹配融合，无外部检索接口。可选 SentenceTransformer 通过 `NEXT_NIA_EMBEDDING_MODEL_DIR` 读取完整本地模型；默认未安装重型依赖和权重，未进行真实模型验收，不自动下载。加载失败必须返回实际词法模式信息。

## 法规问答边界

AI 调用前检查总开关、默认服务商、凭据、有效已发布材料及外发标记，并要求本次 `confirm_external=true`。只应为适合外发的公开法规设置 `allow_external`；该字段不是自动公开性或隐私检测。

只发送本次问题和获准外发的检索片段，不自动加入人员或停留数据。原文件、其他未获准资料和数据库配置不参与问答请求。问题中的非公开资料不会被自动识别或脱敏，因此输入范围仍需操作者把关。

模型输出的每项结论须引用本次候选材料中的来源标识；不存在的标识或异常响应拒绝展示。引用编号由服务端生成。标识校验不等于语义支持验证或法律正确性保证。没有依据时提示资料不足，网络失败不得伪造回答。

真实在线模型调用、问答质量、费用与真实 PostgreSQL 尚未完成集成验证；自动测试替身不构成这些验证。

## 期限核查预警 API

路由前缀 `/api/alerts`，所有接口操作当前活动数据库，不调用外部 API。

| 方法和路径 | 行为 |
| --- | --- |
| `GET /rules` | 直接返回规则字段及 `last_scan_at`、`next_scan_at`、`last_error` |
| `PUT /rules` | 仅接受 `enabled`、`upcoming_days`、`urgent_days`、`scan_interval_minutes` |
| `POST /scan` | 无请求体，按当前北京时间扫描，返回 `{created,updated,closed,scanned,scanned_at}` |
| `GET /` | `q` 姓名/档案编号、`status`、`category`、`page`、`page_size`；返回 `{items,total,page,page_size,stats,last_scan_at}` |
| `GET /{id}` | 平铺提醒属性与 `history` |
| `POST /{id}/actions` | `{action:start\|close\|reopen\|note,note}`；所有动作备注必填，返回完整详情 |
| `GET /stays/{stay_id}/facts` | 直接返回补充事实；未登记时返回默认值 |
| `PUT /stays/{stay_id}/facts` | 保存补充事实，返回对应字段，不代表已执行扫描 |

状态筛选为 `active`（open + in_progress）、`open`、`in_progress`、`closed`、`all`。类别为 `due_soon`、`due_today`、`past_due`、`needs_review`、`all`。统计 `{active,due_soon,due_today,past_due,needs_review}` 为全库当前未关闭记录，独立于列表筛选。

提醒包括 `id,person_id,person_name,person_code,stay_id,category,status,priority,deadline,days_remaining,visa_valid_until,reason,source_summary,last_seen_at,created_at,updated_at,auto_closed`。`priority` 为 urgent/normal/review。签证日期独立展示，未知或未核实事实不会强行显示截止日期与倒计时。历史包含 `id,action,note,created_at`。

补充事实为 `visa_valid_until,extension_status:none|pending|approved|rejected,extension_until,receipt_valid_until,verified,source,notes`。来源最多 500 字，备注最多 5000 字；已核实必须有来源，已核实批准延期必须有截止日期。延期与回执日期不得早于入境；已核实批准延期不得早于原准许停留期限。原始停留改动后扫描会重新核对关系。

规则默认 true/30/7/60；提前天数 0–365，重点天数 0–提前天数，间隔 1–10080 分钟。自动任务在后端运行期间约每 30 秒检查到期，停止服务就停止扫描，重启后对到期任务补做当前扫描。关闭自动扫描仍可手动执行。提醒不是违法结论；并行开放停留、延期待审批、回执或未知/矛盾日期进入事实待核查。人员归档、已实际离境、未来入境或超出提醒窗口不再保持当前提醒。

每条停留一条提醒，扫描幂等更新和记录历史；手工关闭在关键事实/分类/期限变化时重新打开，单纯倒计时或关注等级变化不重开。扫描及历史在同一事务；数据库异常不伪造成功。

## 本地文书 API

路由前缀 `/api/documents`。

| 方法和路径 | 行为 |
| --- | --- |
| `GET /templates/variables` | 返回 `{items:[{name,label,source}]}` 白名单变量 |
| `GET /templates` / `GET /templates/{id}` | 列表 `{items}` / 完整模板 |
| `POST /templates` | 新建 draft 模板，名称与版本组合唯一 |
| `PATCH /templates/{id}` | `expected_revision` + 修改字段/状态，批准需 `confirmed`、来源及复核意见 |
| `GET /` | `q,status:draft\|reviewed\|archived\|all,page,page_size`，返回列表和全库统计 |
| `POST /` | `{template_id,person_id,stay_id?,alert_id?,title,fields}`，创建快照草稿 |
| `GET /{id}` | 返回属性、快照、人工字段、渲染文本、缺项、历史和导出标识 |
| `PATCH /{id}` | `expected_revision` + 标题/人工字段/状态/复核意见，reviewed 需明确确认 |
| `POST /{id}/copy` | 保留原模板与人员快照，复制为新草稿 |
| `GET /{id}/export.txt` / `GET /{id}/export.docx` | 完整性检查后生成本地文件并记录导出审计 |

模板字段为 `name,version,kind:internal|official,source,body,review_note,status:draft|approved|archived,revision`。支持白名单 `{{变量}}`，不执行表达式/脚本；正文最多 50000 字、占位符最多 200 个。approved 模板只可归档，修改须新建版本。启动仅初始化内部核查工作单，不提供未经业务确认的法定标准文案。

文书创建时固定模板及人员/停留快照，并校验预警关联归属；人员与停留字段只读，`fields` 仅接受允许的人工字段，每项最多 6000 字、合计 40000 字。`document_date`/`deadline` 采用 YYYY-MM-DD。实际使用变量未填、人工核定期限早于文书日期时不得审核/导出。

人工审核要求源模板仍已批准且与快照一致、缺项已补全、有复核意见并明确确认。reviewed 内容不可再修改，只能归档或复制；archived 不可编辑或导出。复制保留旧快照，更新事实必须重新建稿。模板和文书使用 `expected_revision` 乐观锁及数据库事务，冲突返回 409，不覆盖他人新版本。

TXT/DOCX 完全本地生成，无新增外部转换依赖。draft 导出保留“草稿·待人工审核，不得作为正式文书使用”，reviewed 保留“人工核对稿·未签发”；DOCX 正文与页眉均保留标识。当前无正式签发、电子签章、送达和 PDF 导出，不把通用排版当作业务确认的法定制式。

## 工作台与界面

`GET /api/dashboard` 从当前数据库汇总人员、停留、法规、预警和文书，读取最近登记事实与本地操作记录。返回 `demo:false`；只读汇总本身不触发扫描或生成业务记录。历史兼容字段 `draft_records` 当前承载文书草稿数量，不表示智能笔录已经实现。

前端错误直接展示中文 `detail`；网络失败显示真实错误，不伪造成功。全局主题变量包括 `--bg`、`--surface`、`--text`、`--muted`、`--border`、`--primary`、`--accent`、`--danger`、`--success`、`--warning` 等；新增页面同时适配明暗模式与移动布局。

表单接入 `settings-dirty-change` 的统一路由保护及 beforeunload，关闭时确认未保存修改。预警至文书的人员/停留/预警上下文只在当前前端状态传递，不写入 localStorage 或 sessionStorage。数据库错误、冲突和请求失败须显示真实反馈。

完整用户说明见 `README.md`、`docs/phase-3.md`；`docs/phase-2.md` 保留第二阶段历史范围。检查命令见 README，验证结论以本次实际运行结果为准，不记录未执行的通过状态。

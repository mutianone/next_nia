# 第五阶段 · 事实画像、统计与交付

版本 0.5.0，面向 macOS / Linux。新增功能全部使用本地数据库和前端资源，不增加外部接口，不自动发送人员资料至 AI。

## 页面使用

### 人员画像

选择在用或已归档人员，查看档案信息、停留记录、关联笔录、文书及期限核查时间线。入出境使用登记的事实日期，笔录/文书使用创建时间，预警使用最近更新时间；不同时间口径在页面说明。

事实支持分类、标签、内容、来源说明、可选事实日期、核验说明和可逆归档。关联来源仅可选择该人员本人的停留、笔录、文书；登记时保存来源标识、版本/日期快照。来源后续更新不会悄悄改写已登记事实；需人工重新核对。笔录摘要与 AI 要素不会自动成为已核验事实。

人工关系须关联已建档的另一个人员，并填写关系及来源。核验必须有说明；图谱仅显示未归档的、经人工核验的直接关系，不按国籍、住址或相似描述推断关系。点击节点进入对应画像；图形最多展示 8 条，完整关系保留在下方清单。修改关系须从登记方画像操作。已归档人员只读。更新使用 revision 比较，拒绝过期版本覆盖。

来源弹框可查看登记时快照、关联笔录文字摘录（最多 12,000 字，完整笔录到笔录模块查看）及迁入原始资料。档案当前状态不等于某个历史时点的完整状态；当前事实编辑记录审计标识及修订号，未提供任意历史版本的画像还原。

### 统计分析

选择日期区间（最多 366 天），查看登记入境/离境日趋势、区间新建业务的当前办理状态、当前人员存量、国籍和事由分布。时间边界使用 Asia/Shanghai。

- 存量和分布不受日期筛选影响，取当前档案状态。
- 日趋势按实际登记入境/离境日期计人次；未知日期不计，不用签证到期日代替。
- 区间新增按创建时间计数；预警、文书、笔录状态是该区间内创建记录的当前状态，不伪造历史快照。
- 默认排除归档人员及归档文书/笔录；可选择包含。未关联人员的笔录仍计入。
- 预警取最近扫描结果，展示扫描时间，不视为违法人数。
- CSV 导出保留日期、归档口径、指标和说明，防止电子表格公式注入；图表提供数据表和大屏弹框。

国籍和事由按录入原文分组，没有自动归一化为国家代码。当前资料缺少核验过的行政区编码/坐标，未绘制地理分布或调用外部地理编码；图表不冒充真实地图。地域地图联动需在取得这些结构化字段和本地图件后另行实现。

## 旧系统迁移

旧系统 `QianZhengData` / `ChuRuJingData` 存在日期语义不一致、国籍缺失和演示数据。工具只读这两类业务表，不执行旧系统代码，不读取人脸比对、在逃、舆情或旧账号数据。

1. 维护人员将旧 SQLite 文件保持只读，用下面命令生成待核对 JSON：

```bash
backend/.venv/bin/python scripts/maintenance.py legacy-stage \
  --source /absolute/path/ai_system.db \
  --output /absolute/path/review-batch.json
```

2. 人工检查 `source_key`（每个独立来源使用不同值）与 `legacy_id`，补齐 `person.nationality`、证件类型和号码、来源。工具不假设 `idCard` 一定是护照。迁入人员的 `verification` 固定为 `pending`，入库后再核验。
3. `original` 保留完整原记录，`stays` 默认空。签证到期日不能直接映射为准许停留截止日，要求离境日不能映射为实际离境日；仅在核实含义后手工填写停留事实。
4. 在“后台配置 → 数据与维护”选择 JSON，可编辑后预检。每批最多 500 人、2 MiB。一次批次所有记录校验通过后再确认导入。
5. 提交时再次检查并绑定预检内容摘要；任一错误整批不导入。相同来源/原记录且内容一致时跳过；已迁入内容改变、重复证件或批次内重复均拒绝自动覆盖、合并。

示例结构（仅用于说明，不会自动插入）：

```json
{
  "source_key": "legacy-reviewed-batch-a",
  "items": [{
    "legacy_id": "qian_zheng_data:1",
    "person": {
      "name": "虚构测试人员",
      "nationality": "测试国",
      "document_type": "other",
      "document_number": "SYNTHETIC-001",
      "source": "经核对的合成来源",
      "verification": "pending"
    },
    "stays": [],
    "original": {"id": 1, "expiryDate": "原字段值，未经解释"}
  }]
}
```

本次交付包含可执行迁移流程和合成测试，**没有自动将真实旧库全量导入日常预览**。照片文件、旧文书路径不会被自动抓取；路径及原字段作为来源记录保留，需要人工整理对应原件。超过 500 条的旧库当前需先分批导出，不静默截断。

## 备份与恢复

后台“下载完整备份”以一致性事务快照导出全部 22 张业务表（包括原始录音、法规原件、历史版本、关系、迁入原始资料和审计）。ZIP 内为逐表 JSONL + 清单 + SHA-256。SQLite 与 PostgreSQL 使用相同数据归档格式；不包含 `settings.json`、API 密钥和数据库密码。备份是明文资料，校验和用于发现损坏，不是加密或真实性签名。用户需独立、安全保管配置文件。

离线命令（从项目根目录执行）：

```bash
# 导出已配置的数据目录，输出文件必须尚不存在
backend/.venv/bin/python scripts/maintenance.py backup \
  --data-dir /absolute/path/current-data --output /absolute/path/backup.zip

# 校验格式、列结构、行数与逐表内容摘要
backend/.venv/bin/python scripts/maintenance.py verify /absolute/path/backup.zip

# 恢复至尚不存在的新目录；会创建默认 AI 关闭的 SQLite 配置
backend/.venv/bin/python scripts/maintenance.py restore /absolute/path/backup.zip \
  --new-data-dir /absolute/path/restored-data

# 使用预先准备的正式配置恢复至空 PostgreSQL；不要启动目标应用后再恢复
backend/.venv/bin/python scripts/maintenance.py restore-postgres /absolute/path/backup.zip \
  --config-dir /absolute/path/prepared-production-config
```

恢复先验证备份，拒绝其他表结构及非空目标，按外键依赖顺序在事务中导入并检查关联完整性。不提供在线覆盖当前库的按钮。SQLite 恢复已完成合成数据、原始音频字节和关联记录演练；真实 PostgreSQL 需在目标服务器单独验证，不能将本机 SQLite 结果视为正式库验收。

升级前停止业务写入并保留现有配置/数据库；恢复验收后，停止当前服务，将 `NEXT_NIA_DATA_DIR` 指向新目录并重启。核对 `/api/health`、人员数量、音频下载校验、来源和关系。出现问题时停止服务，改回原数据目录和对应代码版本；勿覆盖原数据库。

后台导出和 CLI 不自动调度备份、不上传云端。不包含高可用、日志平台或自动容灾服务。大规模库的备份耗时、磁盘容量和并发仍需目标环境容量测试。

## 构建后运行与内网交付

```bash
npm run build
export NEXT_NIA_DATA_DIR=/absolute/path/business-data
# 可选：export NEXT_NIA_PORT=8080
bash scripts/serve.sh
```

该脚本使用一个 Uvicorn 进程在 `127.0.0.1:8080` 同源提供构建静态页面及 `/api`，不使用 Vite 开发服务。macOS/Linux 均可执行；后端启动创建三张新增表，不改变既有表列。默认无登录，继续仅环回监听。内网多用户访问应由部署方配置受控入口、可信 HTTPS 和访问控制；本脚本不会自动开放网络。手机录音仍要求安全上下文。

服务守护可由服务器既有 systemd/launchd 管理：工作目录设为项目根目录，执行 `bash scripts/serve.sh`，只运行一个实例，环境注入数据目录与端口；停止后重新启动，健康检查 `/api/health`。不在本机擅自安装系统服务、证书或修改防火墙。

完整离线依赖包、真实 PostgreSQL、线上 AI 和华为/鸿蒙真机验收仍需要部署环境。本次没有启动手机安装包开发，也未确认法定文书标准措辞。

## 动效与验证

横幅桌面主要高度约 258–280 px（内容与宽度会影响最终高度），移动端压缩为紧凑上下布局。无人操作时持续旋转、悬浮、轨道光点与光环运动；新增基座轨道刻度、边缘灯带、窗格、天线、文档排版与麦克风网格。仍支持暂停、手动拖动和系统/应用减少动效；后台或离开视口暂停绘制。

```bash
npm test
npm run build
backend/.venv/bin/python -m pytest backend/tests -q
bash -n scripts/common.sh scripts/setup.sh scripts/dev.sh scripts/serve.sh
```

自动测试覆盖来源归属、核验条件、过期修订冲突、归档只读、统计日期边界、CSV 公式防护、迁移预检与重复提交、事务回滚、备份篡改、非空拒绝覆盖和恢复字节一致性。浏览器验收使用单独的合成数据目录，不污染日常数据库。

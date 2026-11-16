# 第五阶段验证与稳定性报告

日期：2026-11-16。分支：`fix/record-fill-stage5-validation`。基于第四阶段及开始时已有工作区修改进行验证；没有覆盖或纳入其他未提交业务改动。

## 结论与边界

可以继续在隔离 SQLite、合成材料上进行受控功能验证；**尚不具备生产正式回填或 PostgreSQL 部署放行条件**。本轮没有连接生产库、执行生产迁移、调用真实云端服务、下载字体或修改原始 DOCX。生产正式回填开关未启用。

| 测试 | 修改前 | 修改后 |
|---|---|---|
| 后端全量 | 583 passed / 46 failed / 1 error | 608 passed / 35 failed / 10 skipped / 0 error |
| 前端全量 | 97 passed / 5 failed / 1 error | 未修改前端，复测同基线 |
| 新增冲突与竞争测试 | — | 8 passed（SQLite） |
| 离线评测与 HTTP 链路 | — | 5 passed |
| 临时库部署回退演练 | — | 1 passed |
| PostgreSQL 专项 | — | 10 skipped，未配置独立实例 |
| 演示接口专项 | 2 failed、1 teardown error | 14 passed |

新增 14 项可运行测试和 10 项 PostgreSQL 测试；修复原有 11 项失败及 1 项错误。全量没有新增失败。完整基线失败逐项分类见 [backend-failures.json](qa-stage5-evidence/backend-failures.json)。测试日志保存在本机 `/private/tmp/stage5-{baseline,release-validation,ui}.log`。

## Word 中文渲染

检查三份原模板及第四阶段 before/applied/restored 九份文档，共 12 份、68 页。原始和生成文件 XML 中中文均存在，故障发生在 LibreOffice 渲染环境：默认 Fontconfig 无法加载正确配置/可写缓存，替代字体缺少中文覆盖。PDF 原先只包含 LinuxLibertineG、Carlito、NotoSansLisu 等；显式使用本机已有字体目录和临时缓存后，PDF 包含 STSongti-SC、PingFangSC，中文恢复。

模板 eastAsia/ascii 属性含仿宋_GB2312、宋体、楷体、黑体、方正小标宋简体及 LinuxLibertineG。系统不具备全部原指定字体，因此这次验证证明中文可见和本机分页可检查，**不证明目标 Word/WPS 字体完全一致**。

| 模板 | 原始 / before / applied / restored 页数 | 本机检查 |
|---|---|---|
| bride | 4 / 4 / 4 / 4 | 中文、长回答、页脚单行页码、签名区可见 |
| bride_family | 4 / 4 / 4 / 4 | 中文、段落、页脚与签名区可见 |
| border_case | 9 / 9 / 9 / 9 | 长答案跨页保留，原黄色标记保留；原文件无页眉页脚 |

已查看原始、应用前、应用后逐页拼图；恢复与应用前的页图逐字节相同。检查未发现本轮新增中文缺失、长答案截断或签名区覆盖。原模板已有跨页问题段落、空白区保留，没有擅自调整。三份模板没有 Word 表格节点，不能宣称验收了不存在的表格。所有输入文件哈希保持不变。

证据：[字体和哈希](qa-stage5-evidence/word-render.json)、[PDF 中文文本统计](qa-stage5-evidence/word-text.json)。页面、PDF 和拼图位于本机 `/private/tmp/stage5-word/`。渲染工具为 `scripts/check_record_word_rendering.py`，需传入现有 document skill renderer、Python、`--font-dir /System/Library/Fonts --font-dir /Library/Fonts` 和输出目录；仅设置子进程 FONTCONFIG_FILE，不修改全局字体配置。

### Word/WPS 人工验收清单（尚未完成）

- 记录目标操作系统、Word/WPS 版本、已授权安装字体及替代提示。
- 原始与生成文件并排打开，并进入打印预览逐页核对中文、标点和特殊符号。
- 核对长答案首尾及跨页连续性、段落顺序、固定问题、样例清除及未匹配内容留存。
- 核对页数、页码与总页数同一行、页眉页脚、签名横线与边距；有表格的其他模板另行验收。
- 核对应用前与恢复后的内容和打印分页一致，签名区没有被自动填写或遮挡。
- 记录验收人、日期、截图和差异；未完成前不能宣布正式打印版式通过。

## PostgreSQL 验证

项目已有 SQLAlchemy/psycopg PostgreSQL 支持，但本机没有发现可用独立 PostgreSQL 实例及 postgres/initdb/docker 工具。本轮没有读取生产连接配置或尝试连接生产数据库。专项 10 项全部明确跳过，SQLite 竞争测试不能替代 PostgreSQL 行锁验证。

测试仅读取 `STAGE5_TEST_PG_URL`；限制 localhost 和 `crj_stage5_test` 前缀数据库，每例新建 UUID schema，结束仅清理该 schema。覆盖同候选同/异幂等键、不同候选并发、应用/恢复与人工编辑竞争、事务及提交失败、响应丢失重试、字段版本、开关关闭。

在已安装 Docker 的独立测试机器上可执行（以下尚未执行）：

```sh
docker run --name crj-stage5-test -e POSTGRES_USER=stage5 -e POSTGRES_PASSWORD=stage5-test-only -e POSTGRES_DB=crj_stage5_test -p 127.0.0.1:55432:5432 -d postgres:16
docker exec crj-stage5-test pg_isready -U stage5 -d crj_stage5_test
cd /Users/liuxitong/Desktop/Crj
STAGE5_TEST_PG_URL='postgresql+psycopg://stage5:stage5-test-only@127.0.0.1:55432/crj_stage5_test' backend/.venv/bin/python -m pytest backend/tests/test_stage5_postgres.py -q
docker stop crj-stage5-test
```

密码仅供新建本机测试容器，禁止用于生产。容器名称已存在时先核实归属，不删除现有数据库或卷。等待 pg_isready 成功再运行测试。保存实际结果后才能评估 PG 放行。

## 离线端到端评测

工具：`scripts/evaluate_record_pipeline.py`；样例：`scripts/evaluation_examples/synthetic_jinzi.json`。

```sh
python3 scripts/evaluate_record_pipeline.py scripts/evaluation_examples/synthetic_jinzi.json --output /private/tmp/record-metrics.json
backend/.venv/bin/python -m pytest backend/tests/test_pipeline_evaluation.py -q
```

输入标注包含 utterance 的 ID、原文、匿名说话人、业务角色，QA 问答来源 ID、字段 ID/值/回答来源、粘连疑点 ID，以及预期导出全文或片段。automatic/reviewed 两套观察结果独立计分。可提供录音路径和 SHA256，工具检查一致性及 WAV 时长；不会上传录音或调用 ASR。样例是计分器自检，不是真实模型评测。

输出：字符错误率、角色准确率与未知数、匿名说话人同组配对 P/R/F1（不是 DER）、QA 边界 P/R/F1、字段和值及来源完全匹配 P/R/F1、粘连检测假阳性/假阴性、导出全文或片段次数、实际填写的复核秒数。缺少全文/耗时则为 null，不能把片段一致当整篇一致或把未测耗时当零。

HTTP 集成测试真实执行临时 DB、角色确认、QA 草稿、候选审核、正式应用及 DOCX 导出，但输入为四句模拟供应商结果，没有录音。未人工确认时 4 句均保持 UNKNOWN；人工确认后 2 组 QA、2 个字段正确，导出姓名/住址各出现一次。详见 [HTTP 评测](qa-stage5-evidence/http-evaluation.json)。这里的 100% 仅适用于这两组人工确认后的合成用例；自动文本 CER=0 来自预置文本，不能作为 Fun-ASR 准确率。真实录音完整性、口音噪音、自动角色准确率、人工耗时及生产吞吐仍待合规样本评测。

## 新旧链路冲突审计与修复

| 入口 | 风险 / 处理 |
|---|---|
| records_ai / record_jobs / record_capture 共用 filled_template | 新增公共保护层，旧生成不能覆盖 structured_fill_sources 对应字段和值/依据 |
| 转写来源变化 | 保留已确认值及旧 source_hash，沿用过期校验阻止把旧内容当成新鲜内容导出；需要人工复核、恢复或重新生成 |
| 旧候选确认/拒绝 | 写版本前比较正式字段，企图改写已应用结构化值返回 409，事务不落部分修改 |
| 手工 template-values | 沿用版本和人工锁；修改字段清除旧结构化来源归属，避免把人工改写冒充原 QA |
| 队列异步任务 | 现有 revision/digest 检查在正式应用后拒绝旧任务结果，新增回归验证 |
| 恢复与人工编辑 | 乐观版本竞争仅一方成功，另一方 409；不恢复覆盖后续人工修改 |
| 删除音频 | 有正式应用历史的笔录拒绝音频删除，避免现有清理流程破坏历史依据和恢复能力 |

没有重构正式匹配算法或放宽审核要求，没有新增表或迁移。来源改变后需要人工处理的提示/流程仍可继续改善；本轮不自动解除过期保护。音频删除限制为整条有应用历史的笔录，较保守，后续可在完整保留审计的前提下设计精细化清理。

## 全量失败分类

| 范围 | 基线数 | 当前数 | 分类与处置 |
|---|---:|---:|---|
| test_api | 9 | 0 | 请求夹具剔除只读字段，不放松校验 |
| test_presentation | 2 + 1 error | 0 | 修复模拟协议及未批准材料夹具，禁止外发断言保留 |
| test_alerts | 27 | 27 | 非笔录核心：证件到期新规则与停留日期旧测试冲突，需业务定案 |
| test_dashboard | 2 | 2 | 同预警规则差异 |
| test_auth | 3 | 3 | 夹具/契约：新 root 账号及不存在资源的状态码；并非已证实越权，但全量权限验收尚未全绿 |
| test_demo | 2 | 2 | 非核心演示返回结构/测试契约不一致 |
| test_people | 1 | 1 | 停留日期关系校验缺口，数据质量待修复 |

前端保留失败：DemoData 的取消清理、清理成功更新、生产禁用生成三项；Presentation 的 stale local_processing 凭据/许可和配置入口两项。还存在 RecordTemplateManager 的 undefined.some 未处理错误（通用 mock 返回 counts 而非 items）。主要涉及旧 UI 选择器、mock 契约，不能直接当生产 UI 缺陷，也不能删除断言掩盖。未改动这些已有工作区功能。

## 测试部署与回退演练

`backend/tests/test_stage5_deployment.py` 通过。仅 pytest 临时 SQLite，合成用户通过测试中间件注入，不代表真实浏览器登录验收。

1. 创建旧历史记录及无第一至四阶段附表的临时基线，使用 sqlite backup 保存迁移前备份。
2. 第一至四阶段迁移连续执行两次，历史记录仍可读取。
3. 验证默认开关关闭，经模拟管理员真实配置接口启用测试开关。
4. 合成 QA 确认、影子审核、正式应用、Word 导出。
5. 恢复历史生成新版本，再次导出。
6. 关闭开关，正式预览/应用入口被拒绝，原手工字段及旧导出仍可用。
7. 检查备份保留迁移前记录；没有回写正式库。

这证明临时 SQLite 的迁移/应用恢复流程，不是生产部署、PG 备份恢复或浏览器端完整操作演练。事务中途及提交失败、响应丢失幂等重试沿用第四阶段测试，另增加人工编辑并发回归。

## 修改文件清单

- `backend/app/record_formal_guard.py`：旧回填保护层。
- `backend/app/records.py`：保护层接入、旧候选版本保护、手工来源归属、音频历史保护。
- `backend/tests/test_api.py`、`backend/tests/test_presentation.py`：仅修复相关夹具。
- `backend/tests/test_stage5_conflicts.py`、`test_stage5_postgres.py`、`test_stage5_deployment.py`、`test_pipeline_evaluation.py`：回归、隔离演练和评测。
- `scripts/check_record_word_rendering.py`、`scripts/evaluate_record_pipeline.py`、`scripts/evaluation_examples/synthetic_jinzi.json`：离线工具与模拟标注。
- 本报告及 `docs/qa-stage5-evidence/*.json`：验证证据、失败明细。

## 剩余上线阻断

1. 独立 PostgreSQL 10 项实测及该环境备份恢复演练未完成。
2. 目标 Word/WPS 原字体/打印分页人工签收未完成。
3. 合规录音集的真实自动识别、完整性、粘连检出、字段覆盖、复核耗时没有实测数据。
4. 后端 35 失败、前端 5 失败及 1 错误仍需按清单处理；权限夹具问题须完成实证闭环。
5. 当前工作区存在大量本轮之前的未提交业务改动，不能直接把工作区当已冻结发布包；需独立整合、冻结版本后再跑发布验证。

因此本轮交付是稳定性改进及可复用验证工具，不是生产上线批准。保持默认关闭，只允许继续隔离测试。

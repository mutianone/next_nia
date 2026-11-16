# 第四阶段：正式模板回填受控接入

验收日期：2026-11-16。开发分支：`feat/record-fill-apply-stage4`，基于第三阶段 `e77e3c3`。

实现已完成，功能开关默认关闭。没有执行生产数据库迁移、启用生产开关或调用真实云端服务。所有新增业务测试使用临时 SQLite 数据库及合成问答。当前仅具备隔离测试环境受控联调条件；完整上线验收尚未通过，原因见末节。

## 修改文件

新增：

- `backend/app/record_fill_apply.py`：正式应用校验、事务内写入、字段版本、幂等、应用历史及恢复。
- `backend/app/record_fill_apply_api.py`：应用接口及管理员开关接口。
- `backend/migrations/20261116_fill_apply.py`：可重复执行的三表增量迁移。
- `backend/tests/test_fill_apply.py`：27 项应用与异常测试。
- `backend/tests/test_fill_apply_word.py`：三模板各两项端到端测试，共 6 项。
- `src/components/RecordFillApply.tsx`：差异预览、独立确认、历史及恢复。
- `src/components/RecordFillApply.test.tsx`：5 项前端测试。
- 本文档。

修改：

- `backend/app/records.py`：注册新增接口，保留旧正式回填。
- `backend/app/maintenance.py`：兼容旧备份；部署开关不导出，不随业务数据恢复。
- `backend/tests/test_record_template_flow.py`：修正三份原模板测试的角色及问题来源夹具。
- `src/components/RecordFillPreview.tsx`、`RecordQADrafts.tsx`、`RecordQAReview.tsx`：增量挂载应用区域、更新正式笔录。

没有修改原 DOCX、第一二阶段识别/QA 数据结构或旧正式回填服务。既有其他未提交修改未纳入本阶段提交。

## 基线修复

原 `test_original_word_template_filled_and_samples_removed` 的模拟数据未把民警问题与被询问人回答作为独立的、正确角色的来源提供，缺少对应问题引用，不能通过现有严格来源校验。

修复测试夹具：分别提供民警问题、被询问人回答和姓名陈述，并关联正确的 `question_segment_id`。没有降低业务校验门槛。三份原模板用例均通过。

## 数据库及事务

新增表：

| 表 | 用途 |
| --- | --- |
| record_fill_apply_settings | 部署开关、修订号；不存在记录时视为 false |
| record_fill_apply_batches | 应用/恢复批次、操作者、前后笔录修订、请求摘要及幂等键 |
| record_fill_apply_items | 批次、字段、候选及审核修订关联 |

唯一约束：`(record_id, idempotency_key)`、`(candidate_id, candidate_revision)`。恢复条目不重用候选唯一键。正文旧值和新值复用现有 `RecordVersion` 快照，不在新增批次表重复存储。

迁移文件只建新表、`checkfirst=True`，不改旧表，不删历史；临时库已验证执行两次。生产库未执行。业务备份保留应用历史，但排除 `record_fill_apply_settings`，恢复到新库仍为关闭状态。

使用现有 `write_session`：SQLite 写事务序列化、PostgreSQL 笔录行锁；事务内重新读取来源、候选、模板和字段版本，全部校验成功后更新正式快照、增加笔录修订、记录应用与审计，统一提交。异常统一回滚。PostgreSQL 分支沿用现有锁实现，本次未连接 PostgreSQL 实例进行并发实测。

字段修订从各正式 `RecordVersion` 快照计算，包含值、来源、锁定及结构化来源变化；即使改值后又改回原值，修订仍变化，恢复不会越过这类后续修改。

## API

所有路径前缀为 `/api/records`，沿用登录和原有笔录角色权限。

| 方法及路径 | 行为 |
| --- | --- |
| GET /_features/structured-fill | 读取开关和开关 revision |
| PUT /_features/structured-fill | 仅管理员；expected_revision + record_structured_fill_apply_enabled；变化写审计 |
| POST /{id}/fill-apply/preview | fill_batch_id、candidate_ids；返回差异和预期版本 |
| POST /{id}/fill-apply | 独立确认后正式应用 |
| GET /{id}/fill-apply/history | 历史、前后差异及允许恢复时的版本信息 |
| POST /{id}/fill-apply/{applicationId}/restore | 独立确认和原因，恢复该批次涉及的字段 |

应用请求包含 `fill_batch_id`、`candidate_ids`、`expected_record_revision`、`expected_template_revision`、`expected_field_revisions`、`expected_candidate_revisions`、`idempotency_key`、`confirmed: true`。模板预期版本为完整当前模板快照摘要；每字段、每候选另有修订检查。客户端不能提交任意正式字段值，额外 `values` 被拒绝。

恢复请求包含相同的笔录/模板/字段预期版本、幂等键、独立 `confirmed: true` 和非空 `reason`。操作者由会话获取。版本/锁定/过期来源等冲突返回 409；未单独确认返回 400；未知请求字段返回 422；数据库故障回滚并返回 503。

开关关闭时，后端独立拒绝应用与恢复，不影响旧正式链路和影子候选。前端隐藏或禁用应用动作，已有历史仍可查看。普通用户不能启用开关。没有自动启用逻辑。

## 校验与回填规则

- 必须使用已确认、来源有效的 QA 草稿及最新审核通过的候选。
- 重新核对候选的 QA、问题、原始答案、source_refs、草稿修订/摘要及模板 ID/版本；审核接受原文不可悄悄改值。
- 人工修改候选保存 `HUMAN` 来源，单独审核和正式应用。
- 已锁定字段、未解决关键疑点、来源变化、旧正式模板来源过期一律阻断。
- 相同候选审核版本不可再次应用。相同请求幂等键返回已存结果；同键不同请求返回冲突。
- 幂等重试在可变版本检查前查询已完成结果，因此提交成功但响应丢失不会因旧 revision 重复写入。开关及登录权限仍生效。
- 多答案仅允许 `allow_multiple` 字段，按 QA 原顺序排列，保留完整回答；有效目标之间重新检查共享来源/互斥规则。
- 通用模板 free_qa 保留原问题与回答；原 Word 模板不擅自创建自由问答段落。不适配的内容留在候选中，由人工处理。
- 不修改原始识别、record_segments 或确认后的 QA。
- 恢复只恢复该应用涉及的字段，保留其他字段后续编辑。目标字段后续修改、锁定、模板定义变化时拒绝恢复。恢复产生新修订及新审计，不删除旧应用。

## 新旧链路对照

| 维度 | 旧链路 | 新受控链路 |
| --- | --- | --- |
| 输入 | 原有已保存笔录分析/生成流程 | 已确认 QA + 已审核候选 |
| 授权 | 原有生成操作 | 候选审核、正式应用分别确认 |
| 正文来源 | 保持既有行为 | 结构化候选来源引用与人工审核记录 |
| 并发 | 保持原有检查 | 笔录、模板、字段、候选四层预期版本 |
| 恢复 | 保留既有版本功能 | 按本次应用字段恢复，检查后续修改 |
| 启用 | 不改变 | 后端默认关闭、管理员控制 |

合成标准样例正式结果：`q_name = 张某。`、`q_address = 我住在依安县某小区。`，两组问题保持独立。长回答与追问只有配置允许的目标字段才顺序容纳，不把民警追问文字混入回答。此结果验证结构化回填逻辑，不代表真实音频识别准确率。

## 测试结果

| 范围 | 修改前 | 修改后 |
| --- | --- | --- |
| 后端全量 | 547 passed / 49 failed / 1 error | 583 passed / 46 failed / 1 error |
| 前端全量 | 92 passed / 5 failed / 1 error | 97 passed / 5 failed / 1 error |
| 新后端专项 | — | 33 passed |
| 新旧审核界面专项 | — | 20 passed |
| 前端构建 | — | 通过 |

失败集合逐项比较：后端只减少三份原模板失败，无新增失败；前端保留原有失败和异步错误。现有失败涉及旧接口/演示数据/权限等基线问题，未通过改宽业务规则掩盖。全项目测试尚未全绿。

专项验证：未确认/未审核/过期/锁定/归档阻断；四层版本冲突；互斥冲突；同键并发两次返回同一批次；不同键并发只有一次成功；版本写入后异常回滚；提交阶段异常回滚；响应丢失重试；人工修改后恢复拒绝（含改回原值）；保留无关字段修改；客户端绕过候选写值被拒绝；开关默认关闭和管理员审计；备份恢复不携带启用状态。

可复现命令：

```sh
backend/.venv/bin/python -m pytest backend/tests/test_fill_apply.py backend/tests/test_fill_apply_word.py -q
backend/.venv/bin/python -m pytest backend/tests -q
npm test -- --run
npm run build
```

本次完整日志：`/private/tmp/fill-stage4-baseline.log`、`fill-stage4-full-final.log`、`fill-stage4-core-final.log`、`fill-stage4-ui-baseline.log`、`fill-stage4-ui-full.log`、`fill-stage4-ui-targeted.log`、`fill-stage4-build-final.log`。临时文件不作为永久部署资料。

## 三份 Word 端到端验收

三份原模板分别覆盖：独立问答、长回答、追问、多答案按原顺序、人工改答、重复应用、导出、恢复后导出、锁定后拒绝恢复。

| 模板 | 字段/来源/恢复/资源测试 | 渲染页数（前/应用/恢复） | 最终打印版式 |
| --- | --- | --- | --- |
| bride | 通过 | 3 / 4 / 3 | 未通过视觉验收：中文渲染缺失 |
| bride_family | 通过 | 3 / 4 / 3 | 同上 |
| border_case | 通过 | 7 / 8 / 7 | 同上 |

生成 9 份 DOCX，并使用文档技能的 LibreOffice 渲染器转 PDF/PNG，共 42 页。已查看三份应用后接触图，中文字符没有正确呈现；原因尚未确定，不能据此判断实际 Word/WPS 中的打印效果。原始和恢复渲染 PNG 逐文件完全相同，但这不能替代中文完整的版式验收。DOCX XML 中字段文本完整，段落/字符/节属性和其他 ZIP 资源比较通过；没有把 ZIP 完整性等同于版式正确。

文件：`/private/tmp/fill-stage4-docx/`；渲染：`/private/tmp/fill-stage4-render/`。三模板原 DOCX 未修改。

## 未解决与启用条件

1. 本地渲染中文缺失使最终打印版式验收未完成。需在具备正确字体/渲染支持的 Word/WPS 或 LibreOffice 环境核对分页、页脚与长答案。当前不得宣称打印就绪。
2. 原全量测试仍有 46 个后端失败及 1 个错误、5 个前端失败及 1 个错误。虽无新增回归，仍不具备全系统上线验收条件。
3. PostgreSQL 并发分支未做真实实例验证；本次事务和并发实测是 SQLite。
4. 不包含真实录音识别质量测试，不宣称问答识别或字段语义准确率达到生产指标。
5. 多答案/共享来源等高级配置沿用第三阶段解析器；没有扩展模板管理界面，也不自动为原 Word 新增自由问答区域。

可在独立测试副本迁移后，由管理员明确开启，使用合成材料验证“审核→差异预览→单独确认→正式导出→恢复”。本轮未替用户开启。关闭开关即可停止新应用/恢复请求，不删除历史，不影响原正式链路。正式部署应先补齐打印、现有回归和目标数据库验收。

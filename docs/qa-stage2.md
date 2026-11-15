# 智能笔录第二阶段验收记录

日期：2026-11-15。分支：`fix/record-qa-drafts-stage2`。

本阶段提供识别来源 → 人工角色映射 → 讲话轮次 → 结构化问答 → 独立草稿。现有云端 Fun-ASR、音频采集、原始模板、正式回填和 Word 导出链路不切换。未调用真实云端接口、未使用真实案件材料、未执行生产数据库迁移。

## 基线检查

第一阶段 `record_recognition_runs`、`record_recognition_sentences` 和迁移存在；上传和连续录音均保存逐句来源，原始数据与可编辑正文分离，运行重复结果幂等，人工编辑保护测试通过。

检查了 `record_asr_results.py`、`record_capture.py`、`record_qa_review.py`、`record_candidates.py`、`record_selection.py`、`record_jobs.py`、`records_ai.py`。正式链路仍通过可编辑片段、原有候选与分析入口处理；新草稿没有接入该链路。第一阶段专项基线为 **139 passed**，未发现阻断本轮的回归。

现有 `record_qa_pairs` 已用于正式模板候选，不能将新结构混入。因此本轮使用独立 `record_qa_draft_pairs`，保留原表及其读写逻辑。

工作区原有大量未提交修改均保留。原本未跟踪的审核组件、已有修改的维护模块分别先保存基线提交，之后才添加本轮变更。

## 行为与边界

- 匿名说话人作用域是识别运行、通道、说话人编号的组合。人工批量映射只影响这一组合，不跨运行或跨通道传播。
- 映射和逐句纠错追加版本，保存操作人、时间、修订号；逐句纠错优先，包括主动改回 UNKNOWN。
- 角色与讲话意图区分。文字提示仅作疑点建议，不能确认身份；必须明确选择角色和意图才能生成可确认的对应关系。
- 不同已确认角色不自动合并。未知角色独立保留。同号、同通道、同运行且时间连续的明确“继续发言”可以合并；独立回答不会因为音色相同而直接拼成一个 Turn。
- 一句原文可人工按字符范围拆分，也可合并同一句内的拆分范围。必须完整覆盖原文，不能重复或遗漏。拆分后的精确音频时间为空，保留原句回听位置与多区间映射。
- 独立问题分别保留，长回答可以关联同一问题。追问生成另一组并记录关联。被询问人的反问保留为澄清，不转为民警或回答。
- 多个待答问题之后出现回答时，不猜测对应题目；人工关联可指定问题和回答。跨批次关联必须明确勾选确认，不自动跨运行合并匿名身份。
- 草稿绑定识别版本、角色与意图修订、正文校验摘要。同一输入重复构建复用同一草稿，不重复插入轮次、配对或疑点。
- 人工调整配对生成新版本，不修改已有版本。确认检查来源摘要、草稿版本和待处理疑点；UNKNOWN 不能仅靠填写疑点处理备注变成已确认。
- 已确认草稿保留；来源或映射变化产生新草稿与冲突提示。正式正文有人工修改时提示差异，不将识别原文自动覆盖回正文。
- 疑点记录来源、原因、处理建议。销号记录操作人和说明，已处理说明不可再次覆盖。普通操作审计只写标识与操作人，不写笔录全文。
- 保持原有删除音频语义：显式删除音频时，同时清理引用它的草稿、映射等派生数据；普通重新识别、重新构建不会删除历史。

## 迁移

脚本：`backend/migrations/20261115_qa_drafts.py`，仅新增：

1. `record_speaker_role_mappings`
2. `record_sentence_role_reviews`
3. `record_speaker_turns`
4. `record_qa_draft_pairs`
5. `record_qa_review_issues`
6. `record_qa_drafts`

`upgrade(engine)` 使用 `checkfirst=True`，可以重复执行，不修改旧表或回填虚构历史信息。本轮只在临时测试库执行。原应用的 `Base.metadata.create_all` 注册机制仍然保留；正式部署前应按现有部署流程备份并审核迁移。

维护模块增加这些增量表的旧备份兼容，另对第一阶段两个表补充可缺省识别。已验证旧备份可恢复到全新临时数据库，新表自动为空；不向历史数据补造角色或时间戳。

## 接口

全部在现有 `/api/records/{id}` 前缀下，沿用鉴权、权限中间件与写事务锁。写请求要求 `expected_revision`；映射、逐句审核、草稿确认、疑点处理还检查对应对象的修订号。操作人从登录会话取值，不能由请求体指定。

| 方法 | 后缀 | 功能 |
| --- | --- | --- |
| GET | `/speaker-mappings` | 匿名映射、识别版本、逐句范围、修改历史 |
| POST | `/speaker-mappings` | 保存单个运行/通道/编号的人工映射 |
| POST | `/speaker-sentences/{sentenceId}/review` | 保存逐句角色、意图、拆分范围 |
| GET | `/speaker-turns` | 读取当前来源的讲话轮次 |
| GET | `/qa-drafts` | 读取独立草稿版本、轮次、配对及来源变化状态 |
| POST | `/qa-drafts/rebuild` | 重建草稿；可指定 `run_ids`，同一音频只选一个版本 |
| POST | `/qa-drafts/{draftId}/pairs/review` | 人工调整问答关联，产生新草稿 |
| POST | `/qa-drafts/{draftId}/confirm` | 来源及版本校验后确认草稿 |
| GET | `/qa-review-issues` | 读取疑点与处理状态 |
| POST | `/qa-review-issues/{issueId}/resolve` | 保存疑点处理结果 |

未增加第三方调用。查看人员无写权限，已归档/已核对笔录和进行中的受保护录音遵循现有修改限制。

## 前端使用

在原“整理问答”旁新增 **角色与 QA 草稿**，使用原有弹窗样式，未重排页面。

1. 如需比较重新识别结果，在“识别版本”选择对应运行；同一音频只选一个。
2. 将匿名说话人映射到角色。
3. 在“逐句核对与拆分”确认角色和讲话意图，必要时回听、拆分、合并。
4. 构建 QA 草稿，查看问答及来源。
5. 如存在连续多问或跨批次回答，通过“调整问答关联”生成新草稿。
6. 处理疑点并确认草稿。这里的确认不会修改正式笔录或触发模板回填。

逐句保存不会清除其他句子尚未保存的修改。保存冲突保留本地编辑。原有“整理问答 → 确认并保存修订”操作仍然保留。

## 测试结果

全部新增后端测试使用临时数据库和模拟文本；前端使用模拟 API，没有进行真实云端语音准确率评测。

| 检查 | 修改前 | 修改后 |
| --- | --- | --- |
| 第一阶段专项回归 | 139 通过 | 核心专项及新增功能联合回归通过；最终全量仍覆盖这些用例 |
| 新增后端验收 | 无 | **31 通过** |
| 新增前端验收 | 无 | **5 通过** |
| 新旧审核 UI 专项 | 原有 6 项 | **11 通过** |
| 后端全量 | 481 通过、49 失败、1 错误 | **512 通过、49 失败、1 错误** |
| 前端全量 | 83 通过、5 失败 | **88 通过、5 失败**，另有原设置页异步异常 |
| TypeScript + Vite | — | **构建通过** |

后端失败用例集合与基线一致，涉及预警 27、设置接口 9、鉴权 3、展示测试 2 及 1 错误、模板流程 3、工作台 2、演示数据 2、人员 1。本轮没有顺带修改这些模块。

前端仍失败的是 `DemoData.test.tsx` 的 3 项和 `Presentation.test.tsx` 的 2 项。全量运行还记录 `RecordTemplateManager` 的 `undefined.some` 异步错误；已将修改前审核组件复制到独立临时基线目录，复现相同错误，确认与本轮新增入口无关。不能将当前整个项目描述为“全量测试全绿”。

新增测试覆盖：角色/通道/运行隔离、批量和逐句覆盖优先级、并发修订冲突、标准与连续问答、多问歧义、长回答和停顿继续、追问、反问、告知、未知身份、第三人插话、跨批次切换、人工范围拆分、时间重叠与不连续区间、重复来源、重复构建、人工配对、确认后重新识别、正文人工修改保护、历史记录、旧备份恢复、增量迁移、音频删除派生清理、处理记录保护。第一阶段测试同时覆盖原模板 Word 导出兼容；原有 3 个模板流程失败仍在基线内。

本机日志（不包含真实音频或真实笔录）：

- `/private/tmp/qa-stage2-baseline-focused.log`
- `/private/tmp/qa-stage2-baseline-full.log`
- `/private/tmp/qa-stage2-new-final.log`
- `/private/tmp/qa-stage2-final-full.log`
- `/private/tmp/qa-stage2-ui-tests.log`
- `/private/tmp/qa-stage2-final-frontend.log`
- `/private/tmp/qa-stage2-baseline-presentation-check.log`
- `/private/tmp/qa-stage2-build.log`

## 典型示例

人工确认角色、意图后：

```text
民警 / QUESTION：你叫什么名字？
被询问人 / ANSWER：张某。
民警 / QUESTION：你住在哪里？
被询问人 / ANSWER：我住在依安县某小区。
```

输出两组独立草稿：姓名问答、居住地问答，各自引用对应原始句子。

同样文字如果角色为 UNKNOWN，包括文字中带“问：”“答：”，也不能被当作已确认身份或已完成问答。保留来源并提示核对。

```text
民警 / QUESTION：你当时在哪里？
被询问人 / ANSWER：我在家里。
民警 / FOLLOW_UP：具体几点？
被询问人 / ANSWER：晚上八点左右。
```

形成两组问答，后一组引用前一组作为追问关系；“具体几点”不会进入前一回答。

## 当前无法自动解决与下一阶段

- 云端将两个人识别为同一 speaker、同一句内夹杂多次换人、缺字、重叠说话，仍需回听和人工拆分/校对；没有新增声纹模型。
- 跨运行同号不代表同一个人。每个运行需确认映射，跨批次 QA 需人工确认关联；没有自动身份追踪。
- 多问后合并回答无法可靠自动决定每个答案属于哪题。可以拆分源句并人工关联，不能为了完整率猜配。
- 现阶段只给出保守意图提示和疑点，角色与讲话意图仍需人工核对；未实现“只点开始结束即可无人审核完整回填”。
- 草稿采用识别原文范围。原有人工正文修订若与识别原文不同，会标记冲突，不能默默替代或丢弃修订。
- 缺少第一阶段逐句来源的旧记录继续使用原有人工审核，不伪造 speaker/words/时间戳。

下一阶段建议让 TemplateFillService 只消费已确认且来源未变化的草稿配对，按模板问题匹配并保存字段候选及证据范围；重复投影以草稿版本和配对 ID 幂等，已确认字段保持锁定。先以三份原模板固定验收集对比逐题准确率、遗漏和人工修改量，再决定是否切换正式链路。本轮不实施该切换。

## 修改文件

新增：

- `backend/app/record_speaker_roles.py`
- `backend/app/record_speaker_turns.py`
- `backend/app/record_qa_state_machine.py`
- `backend/app/record_qa_adhesion.py`
- `backend/app/record_qa_drafts.py`
- `backend/app/record_qa_draft_api.py`
- `backend/migrations/20261115_qa_drafts.py`
- `backend/tests/test_speaker_roles.py`
- `backend/tests/test_speaker_turns.py`
- `backend/tests/test_qa_state_machine.py`
- `backend/tests/test_qa_drafts.py`
- `src/components/RecordQADrafts.tsx`
- `src/components/RecordQADrafts.test.tsx`
- `docs/qa-stage2.md`

修改：

- `backend/app/records.py`：注册独立审核路由。
- `backend/app/record_asr_results.py`：显式删除音频时联动清理新派生数据。
- `backend/app/record_qa_review.py`：旧审核声音分组增加通道隔离。
- `backend/app/maintenance.py`：旧备份兼容新增表。
- `src/components/RecordQAReview.tsx`：增加独立弹窗入口。

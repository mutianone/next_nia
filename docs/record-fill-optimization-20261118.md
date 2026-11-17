# 普通模板回填优化与真人录音复测

2026-11-18，基于提交 `56ef71a` 后的代码。当前运行服务继续使用硅基流动 `Qwen/Qwen3-ASR-1.7B` 转写。

## 改了什么

1. **保留停顿后的同人补充。** 普通模板对相邻、角色已确认的回答，在同次识别、同通道、同声音编号且无声音疑点时，允许跨越 2–10 秒停顿。原音时间和会话映射都必须满足范围，所有片段分别引用；结果提示“回答中有较长停顿，请回听核对关联”。超过 10 秒、时间重叠、未知声音或角色、跨批次仍不合并。询问人问话合并及声音相近提示仍使用原来的 2 秒条件。
2. **不再仅因句尾问号挡住回答。** 角色已确认、仅一个句尾问号且没有疑问词、询问提示或混入下一问时，保留原文、问号及不确定语气，添加回听提示。比如“猪啊？”、“莫非是王羲之？”可以作为候选；“你说哪天？”、“带没带？”和夹带下一问的内容仍拦截。模型和规则回填共用这一判断。
3. **没有可靠问答就直接留空。** 仅含问答字段的普通模板若没有可选问答，直接输出本地核对提示，避免无意义的模型请求、格式失败和猜测。包含基本信息的模板、Word 分组生成仍沿用原流程，模型返回结构与原文依据的校验未放宽。
4. **防止未确认的补充被丢掉。** 已确认回答后紧跟同次识别中角色未确认的片段时，整轮待核对，不能把前半句当作完整回答保存。

质量策略版本更新为 `extractive-qa-v9`，旧分组生成结果须按新策略重新核对后才能复用。

## 同一批 Qwen3-ASR 的 12 条结果

本轮修改的是回填，不是声学模型。使用此前冻结的同一批真实转写，保留原音、声音编号、文字错漏和时间戳；没有重新调用 ASR，因此文字错误率与分人指标不变。

| 指标 | 修改前 | 修改后 |
| --- | ---: | ---: |
| 字段有值 | 5/12 | **6/12** |
| 字段与参考回答逐字一致¹ | 4/12 | **5/12** |
| 接口成功但字段留空 | 6/12 | 6/12 |
| 接口最终失败 | 1/12 | **0/12** |
| 需要请求文字模型的样本 | 12/12 | **6/12** |

¹ 忽略标点和空白，但保存值本身保留原有标点与换行；这不是事实真实性判断，也不表示无需人工确认。

第 11 条原来被判为“回答中混入询问内容”，现在完整保存为：

> 天下第一行书，
> 莫非是王羲之？

同时保留 `tentative_answer` 提示，不改写成确定的“是王羲之”。第 07 条的两段回答仍完整保存；其他原已保存的字段未丢失。

02、03、06、08、10、12 共 6 条没有可靠问答，本次通过实际接口返回空字段和 `no_qa_options` 核对提示，记录到的外部模型请求数为 0。第 10 条此前因文字模型输出结构错误返回 502，现在直接留空。这是消除不必要的失败，**不是把原来识别不准的录音变成正确回答**。

## 两条针对性真实录音验证

| 样本 | 原问题 | 修改后 |
| --- | --- | --- |
| XingChen 03：姓名不确定并补充 | 两段回答间停顿 4.18 秒，整项留空 | 两段完整转写按顺序保存，分别引用，带 `paused_reply` 提示 |
| SenseVoice 05：太乙真人的坐骑 | “猪啊？”被问号拦截 | 保存原文“猪啊？”，带 `tentative_answer` 提示 |

XingChen 03 仍漏了“是个外国人”，且原生分人将双方同号；测试先按参考标注逐段确认了角色。这里验证的是已确认角色后补充不再被漏填，不能解释成 XingChen 识别质量改善。当前默认模型仍是 Qwen3-ASR。

## 验证方法与结果

- 共复测 **14 条实际模板流程**：Qwen3-ASR 全部 12 条，加上述 2 条针对性样本。均通过真实 FastAPI 路由、临时数据库上传原音和保存 ASR 版本，再按原参考标注确认角色。普通模板为每段对应主问题的单题模板，不代表所有正式模板均已验收。
- 需要文字匹配的样本真实调用硅基流动 `Qwen/Qwen3-8B`；不需要调用的样本计为本地处理，没有冒充云端测试成功。每条均重新读取字段、候选、正文及原文引用。
- **303 项后端回归通过**，覆盖问答选择、质量规则、候选、说话人、录音、识别版本、分句、模型响应校验、Word 模板和分组任务。新增了长停顿、未知片段、跨批次、重叠时间、语气问号、真正疑问和零外部请求等回归。
- 已重启本地后端使修改生效，健康检查通过；读取运行配置确认转写模型仍为 `Qwen/Qwen3-ASR-1.7B`。

证据：[前后逐例结果](record-fill-optimization-evidence-20261118/results.json)、[汇总](record-fill-optimization-evidence-20261118/summary.json)。原始转写、参考标注与音频哈希仍使用[三模型报告的冻结证据](asr-model-comparison-20261118.md)。可离线复核：

```sh
python3 scripts/verify-record-fill-optimization.py
```

后端回归命令：

```sh
backend/.venv/bin/python -m pytest -q \
  backend/tests/test_record_selection.py backend/tests/test_record_quality.py \
  backend/tests/test_record_candidates.py backend/tests/test_speaker_turns.py \
  backend/tests/test_records.py backend/tests/test_record_speech.py \
  backend/tests/test_record_qa_review.py backend/tests/test_qa_drafts.py \
  backend/tests/test_asr_results.py backend/tests/test_record_capture.py \
  backend/tests/test_fun_asr.py backend/tests/test_record_ai_parsing.py \
  backend/tests/test_record_template_flow.py backend/tests/test_record_jobs.py \
  backend/tests/test_record_voice_alignment.py backend/tests/test_speaker_roles.py \
  backend/tests/test_qa_state_machine.py
```

## 仍需优化

短答的未知声音、同一人换号、ASR 漏词以及原生时间戳超出原音末尾等问题仍在。本轮没有用扩大角色推断或改写原话提高通过数。后续应继续针对实际询问室的弱声、混响与边界切分做声学验证；10 秒续接上限是当前工程约束，尚不是经大规模现场语料标定的最优参数。

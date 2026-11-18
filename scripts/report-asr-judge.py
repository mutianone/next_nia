"""Offline scoring: references are used here only, after judge calls finish."""
import argparse
from collections import Counter
from html import escape
import json
from pathlib import Path
from rapidfuzz.distance import Levenshtein
from backend.app.record_dual_asr import normalized
from backend.app.record_asr_results import fingerprint


def score(decisions):
    frozen=json.loads(Path('docs/dual-asr-evidence-20261118/cases.json').read_text())
    by_id={c['asr']['id']:c['asr'] for c in frozen};rows=[];methods=Counter();chars=0;errors=Counter()
    for decision in decisions:
        source=by_id[decision['id']];groups=[s['dual_review'] for s in source['segments']]
        assert decision['source_fingerprint']==fingerprint([g['candidates'] for g in groups])
        assert [d['group'] for d in decision['decisions']]==[g['group'] for g in groups]
        methods.update(d['method'] for d in decision['decisions'])
        selected=[]
        for group,d in zip(groups,decision['decisions']):
            if d['provider']:
                selected.append(next(c['text'] for c in group['candidates'] if c['provider']==d['provider']))
        row={'id':decision['id'],'complete':len(selected)==len(groups),'selected_windows':len(selected),'windows':len(groups)}
        if row['complete']:
            ref=normalized(''.join(r['text'] for r in source['refs']));chars+=len(ref)
            row['edits']={p:Levenshtein.distance(ref,normalized(o['text'])) for p,o in source['raw_result']['engines'].items()}
            row['edits']['selected']=Levenshtein.distance(ref,normalized(''.join(selected)))
            errors.update(row['edits'])
        rows.append(row)
    return {'cases':rows,'complete_cases':sum(r['complete'] for r in rows),'windows':sum(r['windows'] for r in rows),
            'selected_windows':sum(r['selected_windows'] for r in rows),'methods':dict(methods),
            'model_calls':sum(d['calls'] for d in decisions),'selected_subset_characters':chars,'selected_subset_errors':dict(errors)}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--verify',action='store_true')
    parser.add_argument('--output',type=Path,default=Path('output/双引擎自动择选-20261118'));args=parser.parse_args()
    evidence=Path('docs/asr-auto-selection-evidence-20261118.json')
    if args.verify:
        data=json.loads(evidence.read_text());assert score(data['decisions'])==data['summary'];print('Automatic-selection evidence verified');return
    decisions=[json.loads((args.output/'data'/f'{i:02}.json').read_text()) for i in range(1,13)]
    summary=score(decisions)
    evidence.write_text(json.dumps({'decisions':decisions,'summary':summary},ensure_ascii=False,indent=2)+'\n')
    notes={'01':'Qwen 独立短答','02':'姓名差异，放弃','03':'名字相关差异，放弃','04':'Qwen 独立短答',
           '05':'DeepSeek 选 Qwen 边界；全文错字反而更多','06':'否定词差异，放弃','07':'DeepSeek 无法确认声音边界，放弃',
           '08':'火山分段及声音信息','09':'Qwen 独立“对”','10':'模型选择存在删减内容，被规则否决',
           '11':'逐窗口选择，声音身份仍隔离','12':'DeepSeek 选火山；澄清反问仍须确认'}
    table='\n'.join(f"| {r['id']} | {'已预选' if r['complete'] else '待核对'} | {notes[r['id']]} |" for r in summary['cases'])
    text=f'''# 双引擎自动择选与 DeepSeek 实测（2026-11-18）

可以自动比较并选择已有候选草稿，但文字裁判不能保证选出更接近原音的版本。已实现本地规则、规则加默认文字模型、规则加 DeepSeek 三种模式；推荐不会改写原文或自动确认业务角色。

## 本轮验证

使用上一轮冻结的 12 条公共真人对话的两家 ASR 响应，不再次调用音频模型。规则及 DeepSeek 只接收候选文字、分句、匿名声音和时间信息，未收到参考标注、上一轮人工选择或回填结果。参考标注仅在事后离线评分时使用。这是已有小样本验证集，不能视为独立保留集上的泛化证明。

实际使用已配置的 DeepSeek `deepseek-flash`，4 次真实文字接口调用成功。两份文字一致的窗口用本地规则；肯否、数字或身份等关键差异先保留待核对，文字外发关闭时不请求模型。异文候选匿名为 A/B，按内容散列决定顺序，不把服务商名称或历史输赢告诉模型。

- 16 个窗口中自动预选 11 个：9 个本地规则选择，2 个模型选择；5 个放弃选择。
- 12 条录音中 7 条全部窗口完成预选，5 条仍须人工选择。
- 仅在这 7 条、457 个字符的子集上，Qwen 错误 32 字，火山 24 字，自动预选 29 字。**自动预选未优于火山的全文字符准确率。**剩余 5 条不能当作“自动正确”或从总体效果中忽略。
- 7 条完整预选中，5 条的两家文字错误数相同；第 12 条选中错误更少的火山，第 05 条选中全文错误更多的 Qwen。不能把其中 6 条“不高于最低错误数”包装成 6/7 正确率。

| 样本 | 结果 | 说明 |
| --- | --- | --- |
{table}

第 05 条是重要反例：DeepSeek 偏向 Qwen 保留独立短答和问答边界，但 Qwen 全文有 10 个错误，火山有 5 个。边界较好与文字更准是两个指标。第 12 条虽然自动选了火山，原有“宋代的书法家吗？”澄清反问仍可能导致模板留空，选出候选不等于完成问答核对。

本轮只验证自动择选，不重新统计模板回填成功率。上一轮“9/12 有值、5/12 逐字一致”来自标注辅助人工选择，不是本轮自动模式的效果。

## 功能行为

新双引擎转写自动比较，核对窗口预选推荐版本并显示理由，用户可改选。旧版本可点“自动比对并预选候选”；该操作只更新当前候选选择，不修改已保存原文或正文。模型只允许返回 A、B 或 ABSTAIN，不接受拼写修正、替代原文、合并发言或新增事实。

明显肯否、号码、身份差异保留待核对；选择增加疑似粘连，或只是删掉另一候选中的内容时，规则会否决。文字模型的判断不是原音证据，也不输出未经校准的“准确率/置信度”。模型失败、权限关闭、凭据不可用、响应缺项或越界时，两份 ASR 仍保留。人工确认与角色/模板门禁继续生效。

使用入口：系统设置 → AI 服务 → 笔录语音转写选择双引擎 → 双引擎自动比对选择“规则 + DeepSeek”。使用已配置 DeepSeek 模型，不改变默认回填模型；音频仍发给两家 ASR，文字比较另计费。原有配置升级默认使用本地规则，未擅自切换当前单引擎服务。

后端相关 89 项测试、前端 15 项测试及生产构建通过。测试覆盖关键差异放弃、异常不丢原文、漏补充否决、独立选择 DeepSeek、旧版本推荐不解锁回填、界面自动预选及人工改选。

```sh
PYTHONPATH=. backend/.venv/bin/python scripts/report-asr-judge.py --verify
# 真实模型调用，读取指定配置；不会将标注发给模型，不修改业务数据库
PYTHONPATH=. backend/.venv/bin/python scripts/evaluate-asr-judge.py --settings-dir /path/to/settings-directory --judge deepseek
```

证据保存在 `docs/asr-auto-selection-evidence-20261118.json`；原始两家响应由 `docs/dual-asr-evidence-20261118/cases.json` 的指纹关联。未提交密钥或私有录音。

若要进一步提高实际择优准确率，下一步应引入针对争议片段的原音复核，并在新保留集上评估错误选择率、放弃率和实际模板保真度。仅叠加文字模型无法证明哪个同音字、人名或肯否表达才是原话。
'''
    Path('docs/asr-auto-selection-20261118.md').write_text(text)
    rows=''.join(f'<tr><td>{r["id"]}</td><td>{"已预选" if r["complete"] else "待核对"}</td><td>{escape(notes[r["id"]])}</td></tr>' for r in summary['cases'])
    details=''.join(f'<details><summary>样本 {d["id"]} · 模型请求 {d["calls"]} 次</summary>'+''.join(f'<p>窗口 {x["group"]+1}：{escape(x["provider"] or "放弃")} · {escape(x["reason"])}</p>' for x in d['decisions'])+'</details>' for d in decisions)
    (args.output/'报告.html').write_text(f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>自动择选与 DeepSeek 实测</title><style>body{{max-width:1050px;margin:40px auto;padding:20px;font:16px/1.8 system-ui;color:#17243b;background:#f5f7fb}}h1{{font-size:32px}}.cards{{display:flex;gap:20px}}.card,details{{background:white;padding:20px;border-radius:12px;border:1px solid #d8deea;margin:12px 0}}.card{{flex:1}}strong{{font-size:28px;color:#3852a5}}.note{{padding:18px;background:#fff2db;border-left:4px solid #bd821d}}table{{width:100%;border-collapse:collapse;background:white}}td,th{{text-align:left;padding:12px;border-bottom:1px solid #d8deea}}summary{{cursor:pointer}}</style><main><p>2026-11-18 · 12 条冻结真人转写 · DeepSeek Flash 真实接口</p><h1>自动选择候选草稿：可以做，但不是正确性保证</h1><div class="cards"><div class="card"><strong>11 / 16</strong><br>窗口已自动预选</div><div class="card"><strong>7 / 12</strong><br>录音全部窗口已预选</div><div class="card"><strong>4 次</strong><br>真实 DeepSeek 调用</div></div><p class="note">模型未获得标注。自动模式仍有选错：第 05 条选择了问答边界更清楚、全文错字却更多的 Qwen。当前仍保留人工确认和角色核对。</p><p>7 条已预选子集共 457 字：Qwen 错 32 字，火山错 24 字，自动预选错 29 字。因此不能宣称增加 DeepSeek 后整体识别准确率提高。其余 5 条保留待核对。</p><table><tr><th>样本</th><th>选择状态</th><th>观察</th></tr>{rows}</table><h2>逐段理由</h2>{details}<h2>配置与边界</h2><p>系统设置 → AI 服务 → 双引擎 → 自动比对选择“规则 + DeepSeek”。新转写自动预选，旧版本可重新比对。选择可修改；模型不改写原文。单独计文字费用，遵守文字外发许可。</p><p>本轮未重跑模板回填；上一轮 9/12 是人工标注辅助选择的结果，不能算作自动模式效果。当前默认服务未切换。全文方法与证据已保存到项目 docs/asr-auto-selection-20261118.md。</p></main></html>''')
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__': main()

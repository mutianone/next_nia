"""Build a reproducible report from public replay evidence; no network access."""
import argparse
from collections import Counter
from html import escape
import json
from pathlib import Path
from rapidfuzz.distance import Levenshtein
from backend.app.record_dual_asr import normalized


def summarize(cases):
    total = sum(len(normalized(''.join(r['text'] for r in c['asr']['refs']))) for c in cases)
    edits = Counter(); statuses = Counter(); rows = []
    for case in cases:
        data, fill = case['asr'], case['fill']
        ref = normalized(''.join(r['text'] for r in data['refs']))
        for provider, result in data['raw_result']['engines'].items():
            edits[provider] += Levenshtein.distance(ref, normalized(result['text']))
        selected_text = ''.join(s['text'] for s in fill['selected_segments'])
        edits['reviewed_selection'] += Levenshtein.distance(ref, normalized(selected_text))
        statuses.update(s['dual_review']['status'] for s in data['segments'])
        answer = '\n'.join(s['text'] for s in fill['selected_segments'][fill['cut']:])
        rows.append({'id':data['id'], 'provider':fill['choices'][0], 'groups':len(data['segments']),
            'saved':bool(fill['value']), 'complete_selected_answer':bool(fill['value']) and fill['value']==answer,
            'faithful':bool(fill['value']) and normalized(fill['value'])==normalized(fill['reference_answer']),
            'checks':[c['code'] for c in fill['quality_checks']], 'status':fill['status']})
    return {'cases':rows, 'characters':total, 'character_edits':dict(edits), 'windows':dict(statuses),
        'asr_both_succeeded':sum(len(c['asr']['raw_result']['engines'])==2 and not c['asr']['raw_result']['errors'] for c in cases),
        'pending_fill_blocked':sum(c['fill']['pending_fill_status']==409 for c in cases),
        'saved':sum(r['saved'] for r in rows), 'faithful':sum(r['faithful'] for r in rows),
        'complete_selected_answer':sum(r['complete_selected_answer'] for r in rows),
        'analysis_calls':sum(c['fill']['external_analysis_calls'] for c in cases),
        'asr_elapsed_seconds':round(sum(c['asr']['elapsed_seconds'] for c in cases),2)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path('output/双引擎核对实测-20261118'))
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    evidence = Path('docs/dual-asr-evidence-20261118')
    if args.verify:
        cases = json.loads((evidence/'cases.json').read_text())
        actual = summarize(cases)
        assert actual == json.loads((evidence/'summary.json').read_text())
        manifest = {c['id']:c for c in json.loads(Path('docs/asr-model-evidence-20261118/manifest.json').read_text())}
        assert all(c['asr']['sha256'] == manifest[c['asr']['id']]['sha256'] for c in cases)
        assert len(cases)==12 and actual['asr_both_succeeded']==12 and actual['pending_fill_blocked']==12
        print(json.dumps(actual, ensure_ascii=False, indent=2)); return
    cases = [{'asr':json.loads((args.output/'data'/f'{i:02}.json').read_text()),
              'fill':json.loads((args.output/'data'/f'{i:02}-api.json').read_text())} for i in range(1,13)]
    summary = summarize(cases)
    evidence.mkdir(exist_ok=True)
    (evidence/'cases.json').write_text(json.dumps(cases,ensure_ascii=False,separators=(',',':'))+'\n')
    (evidence/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    names={'siliconflow':'Qwen','volcengine':'火山'}
    notes={
        '01':'短答“我看过啊”独立，火山把问答合在一段。',
        '02':'名字及问答边界仍存疑，留空。',
        '03':'完整保留停顿后的第二段补充，仍有识别错字。',
        '04':'“知道知道”完整独立回填。',
        '05':'“猪啊”完整独立回填，前面问话仍有错字。',
        '06':'“肯定不能啊”尾部缺少时间/声音依据，未填成“肯定不”。',
        '07':'两段回答一起回填，重复字仍保留供核对。',
        '08':'采用火山的独立回答；整句与标注仍有文字差异。',
        '09':'“对”独立回填，未采用火山越界 30ms 的粘连段。',
        '10':'长回答及后续补充完整回填，口语字词仍有误差。',
        '11':'保留“莫非是王羲之？”的试探语气。',
        '12':'“宋代的书法家吗？”澄清反问边界仍待核对，留空。'}
    table = '\n'.join(f"| {r['id']} | {names[r['provider']]} | {'有值' if r['saved'] else '留空'} | {'是' if r['faithful'] else '否'} | {notes[r['id']]} |" for r in summary['cases'])
    md = f'''# 火山 + Qwen3-ASR 双引擎核对实测（2026-11-18）

双引擎适合提供互补候选，并未实现“无需原音就自动知道哪个正确”。本轮两边均成功 12/12；核对前 12/12 被服务端阻止回填。按公开标注辅助选择候选、人工角色确认的回放中，9/12 实际模板有值，9 项都完整保留所选回答；只有 5/12 与标注逐字一致。覆盖提高，不等于字词准确率已经提高。

## 材料与方法

- 沿用冻结的 12 条 MagicData 真人日常对话，4 场、8 位说话人、249.642 秒。逐条校验音频 SHA-256；不是现场民警询问录音，也不新增合成语音来冒充真实效果。
- 本轮重新请求火山 `volc.seedasr.auc` 与硅基流动 `Qwen/Qwen3-ASR-1.7B`，每条在两个工作线程并行；两边接收同一规范化音频副本。Qwen 端沿用本机分人辅助，原音不变。12 条依次执行合计 {summary['asr_elapsed_seconds']} 秒，不能据此当作并发吞吐或价格测量。
- 选择策略见 `scripts/evaluate-dual-fill.py`：标注辅助挑选能独立保留短答、或完整保留补充的引擎，不改识别文字，不凭标点推定身份。它是离线核对示例，**不是系统自动择优准确率，也不是盲测或真实用户回听实验**。
- 每条通过真实 FastAPI 双引擎选择接口建立新版本，保留两边原结果，再在隔离 SQLite 库确认角色、调用实际普通模板分析接口，并重新读取保存值及引用。文字模型为硅基流动 Qwen3-8B，共 {summary['analysis_calls']} 次外部分析请求。未修改业务数据库或用户当前语音设置。

## 本轮结果

| 指标 | 结果 |
| --- | --- |
| 两个服务都成功 | 12/12 |
| 未核对时拦截回填 | 12/12 |
| 候选核对后模板有值 | 9/12 |
| 有值项完整保留所选回答 | 9/9 |
| 回填值与人工标注一致（去标点） | 5/12 |
| 5 条短答完整进入模板 | 4/5 |
| 4 条补充回答完整进入模板 | 3/4 |
| 模板接口成功返回 | 12/12（包含留空） |

全文字符错误率按 961 个去标点字符计算：Qwen {summary['character_edits']['siliconflow']}/961（{summary['character_edits']['siliconflow']/961:.2%}），火山 {summary['character_edits']['volcengine']}/961（{summary['character_edits']['volcengine']/961:.2%}），核对选择后 {summary['character_edits']['reviewed_selection']}/961（{summary['character_edits']['reviewed_selection']/961:.2%}）。选择偏向问答边界和回答完整性，并非逐字错误最小的理想答案。

对照上一轮冻结的单引擎实测：Qwen 模板有值 6/12、逐字正确 5/12；火山有值 5/12、逐字正确 1/12。本轮混合核对是 9/12、5/12。两项基线来自此前单引擎实际接口结果；本轮未重新跑两套单引擎模板，因此不把差值当作受控的自动算法收益。此前火山全文 52 个错误，本轮 51 个，云端结果本身有波动。

| 样本 | 核对选择 | 实际模板 | 逐字正确 | 说明 |
| --- | --- | --- | --- | --- |
{table}

## 可用性与边界

16 个核对窗口中，8 个去标点文字一致，1 个文字不同；7 个因部分字词缺时间而退为整条录音核对。时间窗口按重叠及 250ms 边界容差组成，只用于展示，不制造句词时间。文字一致没有声纹一致或角色一致的含义；例如 01、04 两边文字一致，火山仍把问答标成同一声音。

每个窗口必须明确选择一份候选并确认核对；不给默认“正确答案”。两边原文、匿名编号、时间、失败状态独立保留。选中后建立新来源版本，声音按引擎及原识别运行、通道隔离；再确认业务角色。原音范围之外的时间仅在新投影中置为未知并标记复核，原响应不改、不裁造时间。

单边失败显示失败状态并要求核对，不称为双引擎一致；双方凭据先一起检查。两边独立计费。已有人工正文或其他已应用版本时拒绝覆盖。尚不支持连续录音中的双引擎自动回填，请先保存/上传录音；原有单引擎连续录音保留。

## 验证与复用

相关后端 272 项、前端 17 项通过，生产构建通过。浏览器验证了设置选项、候选展示、原音播放、逐段选择、确认前禁用保存及提交回调。核对组件使用本轮公共样例与固定接口响应；真实数据库保存与云端回填由上述 API 实测完成，未冒充生产浏览器端到端验收。

证据：`docs/dual-asr-evidence-20261118/cases.json` 保存两边响应、分句、窗口与实际模板结果；`summary.json` 为可复算指标。音频、凭据、TOS 地址和临时库不提交仓库。

```sh
PYTHONPATH=. backend/.venv/bin/python scripts/report-dual-asr.py --verify
# 以下会真实调用配置的云服务并计费；使用相同公开语料和隔离测试库
PYTHONPATH=. backend/.venv/bin/python scripts/evaluate-dual-asr.py --settings-dir /path/to/settings-directory
PYTHONPATH=. backend/.venv/bin/python scripts/evaluate-dual-fill.py --settings-dir /path/to/settings-directory
```

使用入口：系统设置 → AI 服务 → 笔录语音转写 → 火山 + Qwen3-ASR 双引擎核对。识别完成进入文字修订 → 双引擎核对 → 选择候选 → 确认角色 → 生成模板。当前默认语音服务保持用户原设置；本功能不是自动事实裁决器。
'''
    Path('docs/dual-asr-comparison-20261118.md').write_text(md)
    cards = []
    for case, score in zip(cases, summary['cases']):
        data, fill = case['asr'], case['fill']
        engines = ''.join(f'<div><h3>{names[engine]}</h3><pre>{escape(o["text"])}</pre></div>' for engine,o in data['raw_result']['engines'].items())
        cards.append(f'<details><summary>{data["id"]} · {escape(notes[data["id"]])}</summary><p>参考回答：{escape(fill["reference_answer"])}</p><div class="columns">{engines}</div><h3>实际模板</h3><pre>{escape(fill["value"] or "留空，待核对")}</pre></details>')
    rows = ''.join(f'<tr><td>{r["id"]}</td><td>{names[r["provider"]]}</td><td>{"有值" if r["saved"] else "留空"}</td><td>{"是" if r["faithful"] else "否"}</td><td>{escape(notes[r["id"]])}</td></tr>' for r in summary['cases'])
    html = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>双引擎核对 · 12 条真人实测</title><style>body{{font:16px/1.7 system-ui;margin:40px auto;max-width:1120px;padding:0 24px;color:#17243b;background:#f5f7fb}}h1{{font-size:32px}}.metrics,.columns{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}}.columns{{grid-template-columns:1fr 1fr}}.card,details{{background:white;border:1px solid #dce3ee;border-radius:12px;padding:20px;margin:16px 0}}.card strong{{display:block;font-size:32px;color:#3852a5}}table{{width:100%;border-collapse:collapse;background:white}}th,td{{padding:12px;text-align:left;border-bottom:1px solid #dce3ee}}pre{{white-space:pre-wrap;word-break:break-word;font:inherit}}summary{{cursor:pointer;font-weight:600}}.note{{border-left:4px solid #c58b1d;background:#fff7e7;padding:16px}}small{{color:#516079}}@media(max-width:700px){{.metrics,.columns{{grid-template-columns:1fr}}}}</style><main><small>2026-11-18 · 同一批公开语料 · 实际云端转写与模板接口</small><h1>火山 + Qwen：互补候选，逐段核对</h1><p>提高回答完整性；不能保证自动选出更准确的原话。</p><div class="metrics"><div class="card"><strong>12 / 12</strong>双引擎均转写成功</div><div class="card"><strong>9 / 12</strong>核对选择后模板有值</div><div class="card"><strong>5 / 12</strong>与标注逐字一致</div></div><p class="note">本轮按公开标注辅助选择候选并确认角色，不是自动择优，也不是盲测。未核对时 12/12 都被服务端拦截；有值的 9 项全部保留所选回答。3 条仍留空。</p><p>短答完整回填 4/5；补充回答完整回填 3/4。全文 CER：Qwen 5.72%，火山 5.31%，核对选择后 5.41%。此前 Qwen 单引擎模板有值 6/12、逐字正确 5/12；本轮完整性改善，逐字正确项未增加。</p><p>同一规范化音频并行请求两个引擎，两边分别计费。16 个核对窗口：文字一致 8、文字差异 1、时间不足按整段核对 7。文字一致不代表声音或角色一致。</p><table><thead><tr><th>样本</th><th>选择</th><th>模板</th><th>逐字正确</th><th>观察</th></tr></thead><tbody>{rows}</tbody></table><h2>逐条原文与实际字段</h2>{''.join(cards)}<h2>使用方式</h2><p>系统设置 → AI 服务 → 笔录语音转写 → 火山 + Qwen3-ASR 双引擎核对。上传录音转写后，在文字修订中先核对两份原文，再确认角色并生成模板。当前默认语音服务保持原设置。</p><p>暂不用于连续录音自动回填。已有人工修订不自动覆盖。核对组件浏览器验收用固定响应，实际保存与回填在隔离数据库完成。</p><img src="核对界面.jpg" alt="使用公共样例的双引擎核对组件浏览器验收" style="max-width:100%;border-radius:12px"><p>完整方法、限制、回归命令及证据已提交至项目 docs/dual-asr-comparison-20261118.md。</p></main></html>'''
    (args.output/'报告.html').write_text(html)
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()

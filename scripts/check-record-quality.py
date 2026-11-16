#!/usr/bin/env python3
"""Repeatable quality gate checks; optional real-cloud probe uses fictional audio text only."""
import argparse,json,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.app.record_templates import CASE_TEMPLATES
from backend.app.record_quality import check_answers,POLICY

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--cloud',action='store_true')
    parser.add_argument('--transcript',type=Path,help='Explicit fictional fixture for a controlled comparison')
    parser.add_argument('--model',help='In-memory override for comparison; does not change application settings')
    parser.add_argument('--runs',type=int,default=1)
    args=parser.parse_args()
    if not 1<=args.runs<=3:parser.error('runs must be 1..3')
    cases=json.loads((ROOT/'backend/tests/fixtures/record_acceptance/quality_cases.json').read_text())
    report={'policy':POLICY,'rule_cases':[],'cloud_runs':[]}
    for c in cases:
        t=next(t for t in CASE_TEMPLATES if t['id']==c['template_id'])
        answer=c.get('quote',c['answer'])
        fact={'label':c['field'],'value':c.get('value',answer),'segment_ids':['test'],'evidence':[] if c.get('no_evidence') else [{'segment_id':'test','question':c['question'],'answer':answer}]}
        accepted,checks=check_answers([fact],[{'segment_id':'test','text':c['question']+c['answer']}],t)
        report['rule_cases'].append({'id':c['id'],'passed':bool(accepted)==c['expected'],'checks':checks})
    if args.cloud:
        from backend.app.config import SettingsStore
        from backend.app.records_ai import analyze_segments,provider_config
        from backend.app.record_cloud import retry
        settings=SettingsStore(ROOT/'backend/data').load()
        if not settings.ai.external_permissions.analysis:parser.error('Analysis external permission is disabled')
        if args.model:getattr(settings.ai.providers,settings.ai.default_provider).chat_model=args.model
        provider,model,_=provider_config(settings,'analysis')
        report.update(provider=provider,model=model)
        t=next(t for t in CASE_TEMPLATES if t['id']=='bride')
        t={**t,'fields':[f for f in t['fields'] if f['id'] in ('q002','q014','q021')],'grouped_generation':True}
        text=(args.transcript or ROOT/'artifacts/jinzi-fullflow/transcript.txt').read_text()
        for i in range(args.runs):
            started=time.monotonic()
            try:
                result=retry(lambda:analyze_segments([{'segment_id':'fictional-audio','text':text}],settings,t))
                report['cloud_runs'].append({'seconds':round(time.monotonic()-started,2),'fields':result['key_facts'],'quality_checks':result['quality_checks'],'usage':result['usage'],'error':''})
            except Exception as e:
                report['cloud_runs'].append({'seconds':round(time.monotonic()-started,2),'error':getattr(e,'message',type(e).__name__)})
            args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    expected={'q002':['我看过了','不用再读'],'q014':['护照'],'q021':['我认为是真的','核查']}
    for run in report['cloud_runs']:
        fields={f['label']:f['value'] for f in run.get('fields',[])}
        run['metrics']={'filled_fields':len(fields),'expected_fields':len(expected),
            'omission_rate':sum(not fields.get(k) for k in expected)/len(expected),
            'reference_phrase_match_rate':sum(all(term in fields.get(k,'') for term in terms) for k,terms in expected.items())/len(expected),
            'blocked_fields':sum(c['status']=='blocked' for c in run.get('quality_checks',[])),
            'human_edit_seconds':None,'billed_cost':None}
    if len(report['cloud_runs'])>1:
        valid=[{f['label']:f['value'] for f in run.get('fields',[])} for run in report['cloud_runs'] if not run.get('error')]
        report['repeat_exact_match_rate']=sum(values==valid[0] for values in valid[1:])/(len(valid)-1) if len(valid)>1 else None
    report['note']='Rule cases measure specific safeguards, not model accuracy. Cloud fields may be empty when evidence cannot be verified. Audio noise/accent performance and human editing time require separate ASR/human evaluation. Costs are not estimated without provider billing.'
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({'rule_passed':sum(x['passed'] for x in report['rule_cases']),'rule_total':len(cases),'cloud_runs':len(report['cloud_runs']),'output':str(args.output)},ensure_ascii=False))
if __name__=='__main__':main()

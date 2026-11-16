#!/usr/bin/env python3
"""Run fixed synthetic samples against native or a Dify workflow; never business DB.
Dify workflow inputs: system_prompt (paragraph), payload (paragraph).
Dify output: result (LLM text). Configure same model/temperature/token limit.
"""
import argparse
import json
import os
from pathlib import Path
import statistics
import sys
import time
import httpx
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.config import SettingsStore
from backend.app.record_templates import CASE_TEMPLATES, extraction_groups
from backend.app.records_ai import analysis_payload, analyze_segments, parse_analysis_response, grounded_template_facts, provider_config, RecordAIError


def dify(payload, base, key):
    result = None
    with httpx.stream('POST', base.rstrip('/')+'/workflows/run', headers={'Authorization':'Bearer '+key},
                     json={'inputs':{'system_prompt':payload['messages'][0]['content'], 'payload':payload['messages'][1]['content']},
                           'response_mode':'streaming','user':'crj-synthetic-acceptance'},
                     timeout=httpx.Timeout(600, connect=10), follow_redirects=False, trust_env=False) as response:
        if response.status_code != 200:
            raise RuntimeError(f'Dify HTTP {response.status_code}')
        received=0
        for line in response.iter_lines():
            received+=len(line)
            if received>4*1024*1024: raise RuntimeError('Dify response exceeds limit')
            if not line.startswith('data:'): continue
            event=json.loads(line[5:].strip())
            if event.get('event')=='error':raise RuntimeError('Dify workflow error; inspect its local run log')
            if event.get('event')=='workflow_finished':
                data=event['data']
                if data.get('status')!='succeeded':raise RuntimeError('Dify workflow did not succeed')
                result=data.get('outputs',{}).get('result')
    if not isinstance(result,str):raise RuntimeError('Dify must return a string output named result')
    return {'choices':[{'finish_reason':'stop','message':{'content':result}}]}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine',choices=['native','dify'],required=True)
    parser.add_argument('--data-dir',type=Path,default=ROOT/'backend/data')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--runs',type=int,default=3)
    parser.add_argument('--confirmed-cloud',action='store_true')
    args=parser.parse_args()
    if not args.confirmed_cloud:parser.error('Add --confirmed-cloud to send only the fixed synthetic samples')
    if not 1<=args.runs<=10:parser.error('--runs must be 1..10')
    settings=SettingsStore(args.data_dir).load()
    if not settings.ai.enabled or not settings.ai.external_permissions.analysis:parser.error('Enable analysis and its external permission first')
    provider,model,key=provider_config(settings,'analysis')
    base=os.environ.get('DIFY_API_BASE','');dify_key=os.environ.get('DIFY_API_KEY','')
    if args.engine=='dify' and (not base or not dify_key):parser.error('Set DIFY_API_BASE and DIFY_API_KEY in your terminal; do not commit keys')
    samples=json.loads((ROOT/'backend/tests/fixtures/record_acceptance/samples.json').read_text())
    results=[]
    for sample in samples:
      template=next(t for t in CASE_TEMPLATES if t['id']==sample['template_id'])
      inputs=[{'segment_id':'synthetic-segment-1','text':sample['text']}]
      for run in range(args.runs):
        start=time.monotonic();values={};groups=[];error=''
        try:
          for fields in extraction_groups(template):
            group={**template,'fields':fields,'grouped_generation':True,'all_field_ids':[f['id'] for f in template['fields']]}
            before=time.monotonic()
            if args.engine=='native': output=analyze_segments(inputs,settings,group)
            else:
                raw=dify(analysis_payload(inputs,settings,group),base,dify_key)
                parsed=parse_analysis_response(raw,inputs,group,key,provider,model)
                grounded_template_facts(parsed,inputs);output=parsed.model_dump()
            values.update({f['label']:f['value'] for f in output['key_facts']})
            groups.append(round(time.monotonic()-before,2))
        except (RecordAIError,RuntimeError,httpx.HTTPError,ValueError) as exc:
            error=exc.message if isinstance(exc,RecordAIError) else type(exc).__name__
        checks={field: (not values.get(field) if rule.get('empty') else all(x in values.get(field,'') for x in rule['contains'])) for field,rule in sample['checks'].items()}
        result={'sample':sample['id'],'run':run+1,'success':not error,'seconds':round(time.monotonic()-start,2),'groups_seconds':groups,'checks':checks,'error':error,'values':values}
        results.append(result)
        report={'engine':args.engine,'provider':provider,'model':model,'runs':results,'completed':len(results),
                'success_rate':sum(r['success'] for r in results)/len(results),
                'mean_seconds':round(statistics.mean(r['seconds'] for r in results),2),
                'note':'Field checks cover only listed facts. Human review of question matching, negatives and uncertainty remains required; failed partial outputs are not usable records.'}
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(sample['id'],run+1,'ok' if not error else 'failed',result['seconds'],flush=True)

if __name__=='__main__':main()

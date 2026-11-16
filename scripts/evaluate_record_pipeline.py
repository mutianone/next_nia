"""Offline evaluation: JSON annotations versus observed outputs. Never calls a provider.

Usage: python scripts/evaluate_record_pipeline.py fixture.json --output metrics.json
Audio is optional: when supplied it must match the annotation SHA256. No speech
model is run; observations must come from an independently captured pipeline run.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

UNKNOWN = {None, '', 'UNKNOWN', 'unknown', 'REVIEW_REQUIRED'}


def score(expected, actual):
    expected, actual = Counter(expected), Counter(actual)
    tp = sum((expected & actual).values()); gold=sum(expected.values()); predicted=sum(actual.values())
    return {'correct':tp,'expected':gold,'predicted':predicted,
            'precision':tp/predicted if predicted else None,'recall':tp/gold if gold else None,
            'f1':2*tp/(gold+predicted) if gold+predicted else None}


def distance(a,b):
    prior=list(range(len(b)+1))
    for i,x in enumerate(a,1):
        current=[i]
        for j,y in enumerate(b,1):current.append(min(current[-1]+1,prior[j]+1,prior[j-1]+(x!=y)))
        prior=current
    return prior[-1]


def pair_key(pair):
    return (tuple(pair['question_sources']),tuple(pair['answer_sources']))


def evaluate(data,base=Path('.')):
    if data.get('data_class') not in {'synthetic','approved_deidentified'}:raise ValueError('Only synthetic or approved deidentified input is allowed')
    gold=data['expected'];utterances=gold['utterances'];ids=[u['id'] for u in utterances]
    if len(set(ids))!=len(ids):raise ValueError('Duplicate annotation IDs')
    known=set(ids);audio=data.get('audio');audio_report={'status':'not_supplied','accuracy_claim':False}
    if audio:
        path=(base/audio['path']).resolve();raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=audio['sha256']:raise ValueError('Audio checksum mismatch')
        audio_report={'status':'checksum_verified','bytes':len(raw),'accuracy_claim':False}
        if path.suffix.lower()=='.wav':
            import wave
            with wave.open(str(path)) as w:audio_report['duration_seconds']=w.getnframes()/w.getframerate()
    report={'case_id':data['case_id'],'audio':audio_report,'stages':{}}
    for stage in ('automatic','reviewed'):
        observed=data.get(stage)
        if observed is None:report['stages'][stage]={'status':'not_measured'};continue
        items=observed.get('utterances',[])
        if len({u['id'] for u in items})!=len(items) or any(u['id'] not in known for u in items):raise ValueError('Unknown or duplicate observation IDs')
        by_id={u['id']:u for u in items}
        correct=sum(by_id.get(u['id'],{}).get('role')==u['role'] and u['role'] not in UNKNOWN for u in utterances)
        pending=sum(by_id.get(u['id'],{}).get('role') in UNKNOWN for u in utterances)
        eligible=[u for u in utterances if u.get('role') not in UNKNOWN]
        # Anonymous cluster names need not match: score same-speaker relations instead.
        cluster_gold=[];cluster_actual=[]
        for i,a in enumerate(utterances):
            for b in utterances[i+1:]:
                if a.get('speaker') in UNKNOWN or b.get('speaker') in UNKNOWN:continue
                key=(a['id'],b['id'])
                if a['speaker']==b['speaker']:cluster_gold.append(key)
                x=by_id.get(a['id'],{}).get('speaker');y=by_id.get(b['id'],{}).get('speaker')
                if x not in UNKNOWN and y not in UNKNOWN and x==y:cluster_actual.append(key)
        for p in observed.get('qa',[]):
            if not set(p['question_sources']+p['answer_sources'])<=known:raise ValueError('QA contains unknown source')
        fields=lambda rows:[(x['field_id'],tuple(x['answer_sources']),x['value']) for x in rows]
        truth=''.join(u['text'] for u in utterances);text=''.join(by_id.get(u['id'],{}).get('text','') for u in utterances)
        if max(len(truth),len(text))>20000:raise ValueError('Split long evaluation cases into bounded samples')
        issues=set(observed.get('adhesion_issues',[]));expected_issues=set(gold.get('adhesion_issues',[]))
        out={'status':'measured','role_accuracy':correct/len(eligible) if eligible else None,'role_correct':correct,'role_total':len(eligible),
             'unknown_or_missing':pending,'speaker_pairwise':score(cluster_gold,cluster_actual),
             'character_error_rate':distance(truth,text)/len(truth) if truth else None,
             'qa_boundaries':score(map(pair_key,gold['qa']),map(pair_key,observed.get('qa',[]))),
             'field_exact':score(fields(gold['fields']),fields(observed.get('fields',[]))),
             'adhesion':{**score(expected_issues,issues),'false_positive':len(issues-expected_issues),'false_negative':len(expected_issues-issues)},
             'export_exact':observed.get('word_text')==gold['word_text'] if 'word_text' in observed and 'word_text' in gold else None,
             'export_fragments':{fragment:observed['word_text'].count(fragment) for fragment in gold.get('export_fragments',[])} if 'word_text' in observed else None,
             'review_seconds':None}
        if stage=='reviewed' and 'review_seconds' in observed:
            seconds=observed['review_seconds']
            if not isinstance(seconds,(int,float)) or seconds<0:raise ValueError('Invalid measured review duration')
            out['review_seconds']=seconds
        report['stages'][stage]=out
    report['note']='Automatic and reviewed metrics are separate. Synthetic self-checks do not establish real ASR quality. Missing timing is not zero.'
    return report


def main():
    p=argparse.ArgumentParser();p.add_argument('input',type=Path);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    result=evaluate(json.loads(args.input.read_text()),args.input.parent)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')

if __name__=='__main__':main()

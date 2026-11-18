"""Real text-judge calls on frozen public ASR outputs; references never sent."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import time
from backend.app.config import SettingsStore
from backend.app.record_asr_judge import recommend
from backend.app.record_asr_results import fingerprint
from backend.app import records_ai
from backend.app.schemas import reveal


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--settings-dir',type=Path,required=True)
    parser.add_argument('--judge',choices=['default','deepseek','rules'],default='deepseek')
    parser.add_argument('--output',type=Path,default=Path('output/双引擎自动择选-20261118'))
    args=parser.parse_args();(args.output/'data').mkdir(parents=True,exist_ok=True)
    settings=SettingsStore(args.settings_dir).load().model_copy(deep=True)
    settings.ai.speech.dual_judge=args.judge
    assert settings.ai.external_permissions.analysis
    original=records_ai.bounded_post;calls=[]
    def tracked(*a,**kw):
        calls.append(1)
        return original(*a,**kw)
    records_ai.bounded_post=tracked
    source=Path('docs/dual-asr-evidence-20261118/cases.json')
    for item in json.loads(source.read_text()):
        identifier=item['asr']['id'];dest=args.output/'data'/f'{identifier}.json'
        if dest.exists(): print(identifier,'retained',flush=True);continue
        # Pass candidate data only, never sample.refs or previous human decisions.
        data={'segments':deepcopy(item['asr']['segments'])}
        original_sources=fingerprint([s['dual_review']['candidates'] for s in data['segments']])
        count=len(calls);started=time.monotonic()
        print(identifier,'START',flush=True)
        result=recommend(data,settings)
        assert original_sources==fingerprint([s['dual_review']['candidates'] for s in result['segments']])
        decisions=[{'group':s['dual_review']['group'],**s['dual_review']['recommendation']} for s in result['segments']]
        saved={'id':identifier,'judge':args.judge,'seconds':round(time.monotonic()-started,2),'calls':len(calls)-count,
               'source_fingerprint':original_sources,'decisions':decisions}
        serialized=json.dumps(saved,ensure_ascii=False,indent=2)
        assert all(not reveal(getattr(settings.ai.providers,n).api_key) or reveal(getattr(settings.ai.providers,n).api_key) not in serialized for n in ('siliconflow','deepseek'))
        dest.write_text(serialized)
        print(identifier,[(d['provider'],d['method']) for d in decisions],flush=True)


if __name__=='__main__': main()

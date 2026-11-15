"""Beijing Fun-ASR: private temporary upload, resumable polling, bounded responses."""
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import urlparse
from uuid import uuid4
import httpx

BASE='https://dashscope.aliyuncs.com/api/v1'
MAX_RESPONSE=2*1024*1024
MAX_WAIT=180


def _oss_url(value):
    if not isinstance(value,str):raise ValueError()
    u=urlparse(value)
    if u.scheme!='https' or u.username or u.password or u.port not in (None,443) or not (u.hostname or '').endswith('.oss-cn-beijing.aliyuncs.com'):
        raise ValueError()
    return value


def _cache(fingerprint,task=None,delete=False):
    path=Path(os.environ.get('CRJ_FUN_ASR_CACHE',str(Path(__file__).resolve().parents[1]/'data'/'fun-asr-tasks.sqlite3')))
    path.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(path,timeout=10) as db:
        path.chmod(0o600)
        db.execute('CREATE TABLE IF NOT EXISTS tasks (fingerprint TEXT PRIMARY KEY,task TEXT NOT NULL,created REAL NOT NULL)')
        db.execute('DELETE FROM tasks WHERE created < ?', (time.time()-23*3600,))
        if delete:db.execute('DELETE FROM tasks WHERE fingerprint=?',(fingerprint,));return
        if task:db.execute('INSERT OR REPLACE INTO tasks VALUES (?,?,?)',(fingerprint,task,time.time()));return
        row=db.execute('SELECT task FROM tasks WHERE fingerprint=?',(fingerprint,)).fetchone()
        return row[0] if row else None


def transcribe(raw,fmt,mime,key,workspace='',request_key=''):
    from .records_ai import RecordAIError
    from .record_asr_results import normalize_funasr
    from .documents import plain_text
    base=f'https://{workspace}.cn-beijing.maas.aliyuncs.com/api/v1' if workspace else BASE
    fingerprint=hashlib.sha256(key.encode()+b'\0'+workspace.encode()+b'\0'+request_key.encode()+b'\0'+raw).hexdigest()
    deadline=time.monotonic()+MAX_WAIT
    auth={'Authorization':f'Bearer {key}'}
    def request(client,method,url,*,decode=True,**kwargs):
        remaining=deadline-time.monotonic()
        if remaining<=0:raise RecordAIError(504,'Fun-ASR任务仍在处理，任务已保留，可稍后重试查询。')
        with client.stream(method,url,timeout=min(30,remaining),**kwargs) as response:
            if response.status_code in (401,403):raise RecordAIError(400,'阿里云凭据或模型权限无效，请检查北京地域API Key。')
            if response.status_code==429:raise RecordAIError(429,'阿里云请求频率受限，请稍后重试。')
            if response.status_code not in (200,201,204):raise RecordAIError(502,'Fun-ASR接口请求失败，原文未修改。')
            data=bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data)>MAX_RESPONSE:raise RecordAIError(502,'Fun-ASR响应超过大小限制。')
            return json.loads(data) if decode else None
    try:
        with httpx.Client(follow_redirects=False,trust_env=False) as client:
            task=_cache(fingerprint)
            if not task:
                policy=request(client,'GET',BASE+'/uploads',headers=auth,params={'action':'getPolicy','model':'fun-asr'})['data']
                host=_oss_url(policy['upload_host'])
                directory=policy['upload_dir']
                if not isinstance(directory,str) or not re.fullmatch(r'[A-Za-z0-9_./-]+',directory) or '..' in directory:raise ValueError()
                if len(raw)>float(policy['max_file_size_mb'])*1024*1024:raise RecordAIError(422,'音频超过阿里云临时上传限制。')
                name=f'{uuid4().hex}.{fmt}';object_key=directory+'/'+name
                form={'OSSAccessKeyId':policy['oss_access_key_id'],'Signature':policy['signature'],'policy':policy['policy'],
                    'x-oss-object-acl':'private','x-oss-forbid-overwrite':'true','key':object_key,'success_action_status':'200'}
                # API credentials must never be forwarded to the OSS host.
                request(client,'POST',host,decode=False,data=form,files={'file':(name,raw,mime)})
                output=request(client,'POST',base+'/services/audio/asr/transcription',
                    headers={**auth,'X-DashScope-Async':'enable','X-DashScope-OssResourceResolve':'enable'},
                    json={'model':'fun-asr','input':{'file_urls':['oss://'+object_key]},'parameters':{'channel_id':[0],'diarization_enabled':True}})['output']
                task=output['task_id']
                if not isinstance(task,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',task):raise ValueError()
                _cache(fingerprint,task)
            while True:
                output=request(client,'GET',base+'/tasks/'+task,headers=auth)['output']
                status=output['task_status']
                if status=='SUCCEEDED':break
                if status not in ('PENDING','RUNNING'):
                    _cache(fingerprint,delete=True)
                    raise RecordAIError(502,'Fun-ASR任务失败，请检查音频和模型权限；原音已保留。')
                if time.monotonic()+2>=deadline:raise RecordAIError(504,'Fun-ASR任务仍在处理，任务已保留，可稍后重试查询。')
                time.sleep(2)
            results=output['results']
            if len(results)!=1 or results[0].get('subtask_status')!='SUCCEEDED':raise ValueError()
            data=request(client,'GET',_oss_url(results[0]['transcription_url']))
            if key in json.dumps(data,ensure_ascii=False):raise ValueError()
            return normalize_funasr(data, task, hashlib.sha256((key+'\0'+workspace).encode()).hexdigest())
    except RecordAIError:raise
    except httpx.TimeoutException:raise RecordAIError(504,'Fun-ASR请求超时；已提交任务可在重试时继续查询。') from None
    except (httpx.HTTPError,ValueError,KeyError,TypeError,IndexError,sqlite3.Error,OSError):
        raise RecordAIError(502,'Fun-ASR响应或本地任务保存异常，原音已保留；未采用不完整结果。') from None

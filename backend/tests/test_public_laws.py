from datetime import date
from unittest.mock import Mock
import json
import httpx
import pytest
from backend.app import public_laws as laws
from backend.tests.test_knowledge import client, enable_ai, upload, publish


def page(item):
    # Real structural shape, synthetic content; does not embed external legal text.
    def cn(n):
        digits='零一二三四五六七八九'
        return digits[n] if n<10 else ('十' if n//10==1 else digits[n//10]+'十')+(digits[n%10] if n%10 else '')
    return '<h1>'+item['title']+'</h1><p>'+item['marker']+'</p>'+''.join(f'<p>第{cn(i)}条 外国人非法就业及签证延期管理的相关规定。</p>' for i in range(1,item['count']+1))


def test_structure_validation():
    item=laws.CATALOGUE[0]
    assert len(laws.parse_page(page(item),item))==93
    with pytest.raises(ValueError): laws.parse_page(page(item).replace('第九十三条','末条'),item)
    with pytest.raises(ValueError): laws.parse_page(page(item).replace(item['marker'],'未知版本'),item)


def test_cache_dates_revocations_and_failure(tmp_path,monkeypatch):
    fetch=Mock(side_effect=page);monkeypatch.setattr(laws,'fetch_page',fetch)
    result,_=laws.search_public(tmp_path,'外国人非法就业',date(2026,1,1))
    assert result and all(s['public_source'] and s['source'].startswith('https://') for s in result)
    assert fetch.call_count==2
    laws.search_public(tmp_path,'外国人非法就业',date(2026,1,1));assert fetch.call_count==2
    assert not laws.search_public(tmp_path,'外国人非法就业',date(2010,1,1))[0]
    assert not laws.search_public(tmp_path,'外国人非法就业',date(2026,1,1),[i['title'] for i in laws.CATALOGUE])[0]
    fetch.side_effect=httpx.ConnectError('offline')
    state=laws.sync(tmp_path,force=True)
    assert all(i['error'] and i['articles'] for i in state['items'])
    assert not laws.search_public(tmp_path,'外国人非法就业',date(2026,1,1))[0]


def test_fixed_requests_no_redirects(monkeypatch):
    original=httpx.Client;requests=[]
    def handler(request):
        requests.append(request)
        return httpx.Response(302,headers={'location':'http://127.0.0.1/private'})
    monkeypatch.setattr(httpx,'Client',lambda **kw: original(transport=httpx.MockTransport(handler),**kw))
    with pytest.raises(httpx.HTTPStatusError): laws.fetch_page(laws.CATALOGUE[0])
    assert len(requests)==1 and str(requests[0].url)==laws.CATALOGUE[0]['url']
    assert not requests[0].content and 'authorization' not in requests[0].headers


def test_fallback_both_routes_and_local_priority(client,monkeypatch):
    enable_ai(client)
    settings=client.app.state.store.current.model_copy(deep=True);settings.ai.public_laws_enabled=True;client.app.state.store.save(settings)
    fetch=Mock(side_effect=page);monkeypatch.setattr(laws,'fetch_page',fetch)
    def answer(q,s,c,**kwargs):return {'status':'answered','answer':'核查原文','sources':s,'claims':[],'provider':None,'model':None}
    monkeypatch.setattr('backend.app.knowledge.answer_with_sources',answer)
    monkeypatch.setattr('backend.app.knowledge.stream_answer',lambda q,s,c,**kw:iter([{'type':'complete','answer':answer(q,s,c)}]))
    for suffix in ('','/stream'):
        r=client.post('/api/knowledge/ask'+suffix,json={'question':'外国人非法就业','as_of':'2026-01-01','confirm_external':True})
        assert r.status_code==200
        data=r.json() if not suffix else [json.loads(x[6:]) for x in r.text.splitlines() if x.startswith('data: ')][-1]['answer']
        assert data['sources'][0]['public_source'] and data['retrieval']['mode']=='public_catalogue'
    assert fetch.call_count==2
    doc=upload(client,content='第一条 外国人非法就业应当核查证据。').json();publish(client,doc,allow_external=True)
    monkeypatch.setattr('backend.app.knowledge._public_fallback',Mock(side_effect=AssertionError('local hit must not fetch')))
    r=client.post('/api/knowledge/ask',json={'question':'外国人非法就业','confirm_external':True})
    assert r.status_code==200 and not r.json()['sources'][0].get('public_source')


def test_disabled_and_consent_block(client,monkeypatch):
    enable_ai(client)
    fetch=Mock(side_effect=AssertionError('must not fetch'));monkeypatch.setattr(laws,'fetch_page',fetch)
    assert client.post('/api/settings/public-laws/refresh').status_code==403
    r=client.post('/api/knowledge/ask',json={'question':'外国人非法就业','confirm_external':True})
    assert r.json()['status']=='insufficient'
    settings=client.app.state.store.current.model_copy(deep=True);settings.ai.public_laws_enabled=True;client.app.state.store.save(settings)
    assert client.post('/api/knowledge/ask',json={'question':'外国人非法就业','confirm_external':False}).status_code==400
    fetch.assert_not_called()

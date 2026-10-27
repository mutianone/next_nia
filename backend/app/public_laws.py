"""Fixed official catalogue. Questions never leave the server during retrieval."""
from contextlib import closing
from datetime import date, datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
import json
import re
import sqlite3
from threading import RLock

import httpx
from fastapi import APIRouter, HTTPException, Request
from .retrieval import rank_sources

router = APIRouter(prefix='/api/settings/public-laws', tags=['public-laws'])
_LOCK = RLock()
TTL = 86400
RETRY = 300
CATALOGUE = (
    dict(id='exit-entry-law', title='中华人民共和国出境入境管理法', version='2012年通过',
         url='https://www.nia.gov.cn/n794014/n1050181/n1050479/c1013311/content.html',
         effective_from='2013-07-01', count=93, end='第九十三条', marker='2013年7月1日起施行'),
    dict(id='foreigners-regulation-2025', title='中华人民共和国外国人入境出境管理条例', version='2025年修订',
         url='https://xzfg.moj.gov.cn/front/law/detail?LawID=1776',
         effective_from='2025-10-01', count=39, end='第三十九条', marker='根据2025年8月7日'),
)

class TextParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []; self.skip = 0
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript'): self.skip += 1
        if not self.skip and tag in ('p','div','br','li','h1','h2'): self.parts.append('\n')
    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript'): self.skip = max(0, self.skip-1)
        if not self.skip and tag in ('p','div','li','h1','h2'): self.parts.append('\n')
    def handle_data(self, data):
        if not self.skip: self.parts.append(data)


def parse_page(html, item):
    parser = TextParser(); parser.feed(html)
    text = ''.join(parser.parts)
    compact = re.sub(r'\s+', '', text)
    if item['title'] not in compact or item['marker'] not in compact:
        raise ValueError('官方页面版本标识不符，请核查目录版本。')
    lines = [re.sub(r'\s+', ' ', line).strip() for line in text.splitlines() if line.strip()]
    text = '\n'.join(lines)
    matches = list(re.finditer(r'(?m)^(第[一二三四五六七八九十百]+条)\s*', text))
    if len(matches) != item['count'] or matches[0][1] != '第一条' or matches[-1][1] != item['end']:
        raise ValueError('官方页面条文结构变化，未替换缓存。')
    chunks = []
    for index, match in enumerate(matches):
        end = matches[index+1].start() if index+1 < len(matches) else text.find('\n', match.end())
        # The terminal article is one paragraph; exclude site navigation/footer.
        if end < 0: end = len(text)
        value = text[match.start():end].strip()
        value = re.sub(r'\n第[一二三四五六七八九十]+[章节][^\n]*', '', value)
        if len(value) < 12 or len(value) > 12000: raise ValueError('条文内容不完整。')
        chunks.append({'article':match[1], 'text':value})
    return chunks


def fetch_page(item):
    # No configurable URLs, redirects, query text, cookies or AI credentials.
    with httpx.Client(timeout=httpx.Timeout(10, connect=4), follow_redirects=False, trust_env=False) as client:
        with client.stream('GET', item['url'], headers={'Accept':'text/html'}) as response:
            response.raise_for_status()
            if 'text/html' not in response.headers.get('content-type',''): raise ValueError('官方页面类型不支持。')
            raw = bytearray()
            for block in response.iter_bytes():
                raw.extend(block)
                if len(raw) > 2_000_000: raise ValueError('官方页面过大。')
    return bytes(raw).decode('utf-8-sig', errors='strict')


def _db(directory):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'public-laws.sqlite3'
    conn = sqlite3.connect(path, timeout=15)
    path.chmod(0o600)
    conn.row_factory = sqlite3.Row
    conn.execute('CREATE TABLE IF NOT EXISTS cache (id TEXT PRIMARY KEY, payload TEXT, fetched_at TEXT, attempted_at TEXT, error TEXT)')
    conn.commit()
    return conn


def sync(directory, force=False):
    with _LOCK, closing(_db(directory)) as conn:
        now = datetime.now(timezone.utc)
        for item in CATALOGUE:
            row = conn.execute('SELECT * FROM cache WHERE id=?', (item['id'],)).fetchone()
            if not force and row:
                stamp = row['fetched_at'] if not row['error'] else row['attempted_at']
                if stamp and (now-datetime.fromisoformat(stamp)).total_seconds() < (TTL if not row['error'] else RETRY): continue
            try:
                chunks = parse_page(fetch_page(item), item)
                conn.execute('INSERT INTO cache VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, fetched_at=excluded.fetched_at, attempted_at=excluded.attempted_at,error=NULL', (item['id'], json.dumps(chunks,ensure_ascii=False), now.isoformat(), now.isoformat(), None))
            except (httpx.HTTPError, ValueError, UnicodeError):
                conn.execute('INSERT INTO cache VALUES (?,NULL,NULL,?,?) ON CONFLICT(id) DO UPDATE SET attempted_at=excluded.attempted_at,error=excluded.error', (item['id'],now.isoformat(),'官方来源暂不可用或正文校验未通过；已有缓存保留，暂不用于回答。'))
            conn.commit()
    return status(directory)


def status(directory):
    with _LOCK, closing(_db(directory)) as conn:
        rows = {row['id']:dict(row) for row in conn.execute('SELECT * FROM cache')}
    return {'items':[{**{k:v for k,v in item.items() if k not in ('marker','count','end')}, 'fetched_at':rows.get(item['id'],{}).get('fetched_at'), 'error':rows.get(item['id'],{}).get('error'), 'articles':len(json.loads(rows[item['id']]['payload'] or '[]')) if item['id'] in rows else 0} for item in CATALOGUE]}


def search_public(directory, question, as_of, blocked_titles=()):
    state = sync(directory)
    sources = []
    with _LOCK, closing(_db(directory)) as conn:
        for item in CATALOGUE:
            if item['title'] in blocked_titles or as_of < date.fromisoformat(item['effective_from']): continue
            row = conn.execute('SELECT * FROM cache WHERE id=?', (item['id'],)).fetchone()
            if not row or row['error'] or not row['payload']: continue
            for chunk in json.loads(row['payload']):
                digest = sha256(chunk['text'].encode()).hexdigest()[:12]
                sources.append(dict(id=f"public-{item['id']}-{digest}", document_id='', public_source=True,
                    title=item['title'], version=item['version'], source=item['url'], effective_from=item['effective_from'],
                    effective_to=None, page=None, allow_external=True, fetched_at=row['fetched_at'], **chunk))
    matches, _ = rank_sources(question, sources, 8)
    # Long factual descriptions can drown out sanction terms. Retrieve a small
    # second set for the explicitly named behaviour, without inferring guilt.
    behaviours = [word for word in ('非法就业', '非法居留', '非法入境', '非法聘用') if word in question]
    if matches and behaviours and any(word in question for word in ('处罚', '处理', '罚', '拘留')):
        penalties, _ = rank_sources(' '.join(behaviours) + ' 处罚 罚款 拘留', sources, 2)
        matches = list({item['id']:item for item in matches[:6] + penalties + matches[6:]}.values())[:8]
    failed = any(item['error'] for item in state['items'])
    message = '已检索官方法规目录缓存；引用可查看原文及获取时间。' if matches else '官方法规目录中也未匹配到依据，请补充事实或导入对应法规。'
    if failed: message += ' 部分官方来源更新失败，未使用该来源。'
    return matches, {'mode':'public_catalogue','message':message}


@router.get('')
def get_status(request: Request):
    return status(request.app.state.store.directory)

@router.post('/refresh')
def refresh(request: Request):
    if not request.app.state.store.current.ai.public_laws_enabled:
        raise HTTPException(403, '请先开启并保存官方法规补充检索。')
    return sync(request.app.state.store.directory, force=True)

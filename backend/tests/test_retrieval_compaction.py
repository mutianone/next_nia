from backend.app.retrieval import compact_sources


def source(id, text, **kw):
    return dict(id=id, text=text, document_id='doc', version='v1', article='第一条', page=None, allow_external=True, **kw)


def test_merge_overlap_both_orders_preserves_text_and_provenance():
    overlap='共有内容，条件不得遗漏。'*10
    a=source('a','前文'+overlap)
    b=source('b',overlap+'后文')
    for candidates in ([a,b],[b,a]):
        result=compact_sources(candidates)
        assert len(result)==1
        assert result[0]['text']=='前文'+overlap+'后文'
        assert set(result[0]['merged_chunk_ids'])=={'a','b'}
    assert a['text']=='前文'+overlap


def test_distinct_rules_versions_pages_and_small_differences_stay_separate():
    a=source('a','罚款五百元；其他条件。'*10)
    for field,value in [('article','第二条'),('version','v2'),('page',2),('document_id','other'),('allow_external',False)]:
        b={**a,'id':'b',field:value}
        assert len(compact_sources([a,b]))==2
    assert len(compact_sources([a,source('b',a['text'].replace('五百','一千'))]))==2


def test_duplicates_do_not_consume_slots():
    a=source('a','正文')
    b=source('b','正文')
    c=source('c','另一项规则')
    assert len(compact_sources([a,a,b,c],2))==2

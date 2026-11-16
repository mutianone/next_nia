from zipfile import ZipFile
from io import BytesIO
from xml.etree import ElementTree as ET
from backend.app.decision_layout import make_docx, ASSET, W, layout

def test_empty_export_preserves_every_original_text_and_property():
    with ZipFile(ASSET) as original,ZipFile(BytesIO(make_docx({}))) as actual:
        for name in original.namelist():
            if name=='word/document.xml':
                assert ET.tostring(ET.fromstring(original.read(name)))==ET.tostring(ET.fromstring(actual.read(name)))
            else:assert original.read(name)==actual.read(name)

def test_fill_changes_only_blanks_and_keeps_fixed_wording_and_properties():
    with ZipFile(ASSET) as source,ZipFile(BytesIO(make_docx({'person_name':'测试人员','law_article':'一','law_clause':'二','court':'测试','deadline':'2026-11-01'}))) as actual:
        root=ET.fromstring(actual.read('word/document.xml'));old=ET.fromstring(source.read('word/document.xml'))
        text=''.join(root.itertext())
        for wording in ['经查，被处罚人','（此处填写具体违法行为','以上事实有','六十日内向','人民法院提起行政诉讼。','被处罚人应当在规定期限内自行离境。','依安县公安局']:
            assert wording in text
        assert '第一条第二款' in text and '2026年11月1日' in text
        for tag in ['pPr','rPr','sectPr']:
            assert [ET.tostring(x) for x in root.iter(W+tag)]==[ET.tostring(x) for x in old.iter(W+tag)]
    assert '执行要求：' in ''.join(r['text'] for p in layout()['paragraphs'] for r in p['runs'])


def test_signature_and_date_values_export_in_original_blanks():
    values={'organization':'测试办案单位','officer':'测试承办人','approver':'测试批准人','document_date':'2026年10月28日','deadline':'2026年11月10日'}
    with ZipFile(BytesIO(make_docx(values))) as archive:
        root=ET.fromstring(archive.read('word/document.xml'))
        text=''.join(root.itertext())
        for value in values.values():assert value in text
        assert '20XX年XX月XX日' not in text


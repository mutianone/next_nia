import pytest
from backend.tests.test_records import client,cloud,segments,editable
from backend.tests.test_record_template_flow import generated
from backend.app import record_pdf


def test_pdf_uses_same_docx_and_preserves_export_guards(client,cloud,monkeypatch):
    row=generated(client,cloud);word=client.get(f"/api/records/{row['id']}/export.docx").content
    seen=[]
    monkeypatch.setattr(record_pdf,'convert',lambda raw:seen.append(raw) or b'%PDF-1.7\nsynthetic')
    pdf=client.get(f"/api/records/{row['id']}/export.pdf")
    assert pdf.status_code==200 and pdf.headers['content-type']=='application/pdf'
    from zipfile import ZipFile
    from io import BytesIO
    assert ZipFile(BytesIO(seen[0])).read('word/document.xml')==ZipFile(BytesIO(word)).read('word/document.xml')
    assert pdf.headers['cache-control']=='no-store'
    row=client.patch(f"/api/records/{row['id']}",json={'expected_revision':row['revision'],'status':'archived'}).json()
    assert client.get(f"/api/records/{row['id']}/export.pdf").status_code==409


def test_pdf_unavailable_is_clear_and_no_silent_text_export(client,cloud,monkeypatch):
    row=generated(client,cloud)
    monkeypatch.setattr(record_pdf,'binary',lambda:None)
    response=client.get(f"/api/records/{row['id']}/export.pdf")
    assert response.status_code==503 and 'LibreOffice' in response.json()['detail']
    assert client.get('/api/records/capabilities').json()['pdf_export_available'] is False


def test_revision_change_during_pdf_conversion_discards_result(client,cloud,monkeypatch):
    row=generated(client,cloud)
    def convert(_):
        assert segments(client,row,[editable(row['segments'][0],text='并发合成修订')]).status_code==200
        return b'%PDF-1.7\nsynthetic'
    monkeypatch.setattr(record_pdf,'convert',convert)
    assert client.get(f"/api/records/{row['id']}/export.pdf").status_code==409

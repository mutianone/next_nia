"""User-supplied Word layout for unsigned, human-reviewed departure drafts."""
from pathlib import Path
from zipfile import ZipFile
from io import BytesIO
from xml.etree import ElementTree as ET
from uuid import uuid5, NAMESPACE_URL

TEMPLATE_ID = '56cf5392-0208-4ae3-9db6-37c50ad08010'
ASSET = Path(__file__).resolve().parents[1] / 'templates' / 'departure_decision.docx'
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
EXTRA_FIELDS = [('english_name','外文名'),('gender','性别'),('birth_date','出生日期'),('document_type','证件类型'),('address','现住址'),('evidence','证据'),('rights_notice','权利告知（人工核对）'),('execution_requirements','执行要求（人工核对）'),('approver','批准人')]
PARAGRAPHS = {
 0:'{{organization}}', 2:'{{document_number_label}}',
 4:'姓名：{{person_name}}（外文名：{{english_name}}）',5:'性别：{{gender}}',6:'出生日期：{{birth_date}}',
 7:'国籍：{{nationality}}',8:'证件类型及号码：{{document_type}} {{document_number}}',9:'现住址：{{address}}',
 12:'{{facts}}',13:'',14:'{{evidence}}',15:'',
 18:'根据{{legal_basis}}，{{decision}}。要求其于{{deadline}}前离境。',
 21:'{{rights_notice}}',22:'',23:'',24:'',27:'{{execution_requirements}}',
 30:'办案单位：{{organization}}（盖章）',31:'承办人签名：{{officer}}',32:'批准人签名：{{approver}}',33:'日期：{{document_date}}',
}

def template_body():
    with ZipFile(ASSET) as z:
        paragraphs=ET.fromstring(z.read('word/document.xml')).find(W+'body').findall(W+'p')
        return '\n'.join(PARAGRAPHS.get(i,''.join(t.text or '' for t in p.iter(W+'t'))) for i,p in enumerate(paragraphs))

def ensure_template(session):
    from .documents import DocumentTemplate
    template=session.get(DocumentTemplate,TEMPLATE_ID)
    if template is None:
        template=DocumentTemplate(id=TEMPLATE_ID,name='责令限期出境决定书',version='用户模板 1.0',kind='official',status='draft',source='用户提供：责令限期出境决定书_规范排版.docx',body=template_body(),review_note='待人工核对模板、权利告知和执行要求；自动任务只创建未签发草稿。')
        session.add(template);session.flush()
    return template

def ensure_draft(session, task, person, alert, now):
    from .documents import DocumentDraft, template_json, record_history
    template = ensure_template(session)
    identifier=str(uuid5(NAMESPACE_URL,'crj:departure:'+task.id))
    row=session.get(DocumentDraft,identifier)
    if row is None:
        snapshot={k:getattr(person,k) for k in ('id','code','name','nationality','document_number','verification','source','archived')}
        snapshot.update(stay=None,captured_at=now.isoformat(),document_expiry=person.document_expiry.isoformat())
        fields={'english_name':person.english_name,'gender':{'male':'男','female':'女'}.get(person.gender,''),'birth_date':person.birth_date.isoformat() if person.birth_date else '', 'document_type':{'passport':'护照'}.get(person.document_type,person.document_type),'address':person.address}
        row=DocumentDraft(id=identifier,template_id=template.id,person_id=person.id,stay_id=None,alert_id=alert.id,title='责令限期出境决定书 · '+person.code,status='draft',template_snapshot=template_json(template),person_snapshot=snapshot,fields=fields,review_note='自动触发依据仅为登记证件有效期超过七天，事实认定和处置内容待人工核对。')
        session.add(row);session.flush();record_history(session,row,'created')
    task.status='draft_created'
    return row

def export_layout(row,label):
    from .documents import PLACEHOLDER
    values={'person_name':row.person_snapshot['name'],'nationality':row.person_snapshot['nationality'],'document_number':row.person_snapshot['document_number'],**row.fields}
    output=BytesIO()
    with ZipFile(ASSET) as source, ZipFile(output,'w') as target:
        root=ET.fromstring(source.read('word/document.xml'))
        paragraphs=root.find(W+'body').findall(W+'p')
        for index,text in PARAGRAPHS.items():
            paragraph=paragraphs[index]
            runs=paragraph.findall(W+'r')
            props=runs[0].find(W+'rPr') if runs else None
            for child in list(paragraph):
                if child.tag != W+'pPr': paragraph.remove(child)
            run=ET.SubElement(paragraph,W+'r')
            if props is not None: run.append(props)
            node=ET.SubElement(run,W+'t');node.set('{http://www.w3.org/XML/1998/namespace}space','preserve')
            node.text=PLACEHOLDER.sub(lambda m:values.get(m[1]) or '【待填写】',text)
        # Mark the unsigned draft visibly without changing page margins/styles.
        run=ET.SubElement(paragraphs[1],W+'r');props=ET.SubElement(run,W+'rPr');ET.SubElement(props,W+'sz',{W+'val':'18'})
        ET.SubElement(run,W+'br');ET.SubElement(run,W+'t').text=label
        for item in source.infolist():
            target.writestr(item,ET.tostring(root,encoding='utf-8',xml_declaration=True) if item.filename=='word/document.xml' else source.read(item.filename))
    return output.getvalue()

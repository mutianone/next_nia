"""Fill only original Word blanks, preserving fixed text and run formatting."""
import re
from io import BytesIO
from zipfile import ZipFile
from xml.etree import ElementTree as ET
from .departure_documents import ASSET, W

LABELS={'person_name':'姓名','english_name':'外文名','gender':'性别','birth_year':'出生年','birth_month':'出生月','birth_day':'出生日','nationality':'国籍','identity':'证件类型及号码','address':'现住址','facts':'具体违法事实','evidence':'证据','law_article':'法律条号','law_clause':'法律款号','deadline':'离境日期','review_authority':'复议机关','court':'人民法院','organization':'办案单位','officer':'承办人','approver':'批准人','document_date':'文书日期','document_number_label':'文书编号'}
# Binding addresses are checked against the supplied immutable template.
BLANKS={(4,1):'person_name',(5,1):'gender',(6,1):'birth_year',(7,1):'nationality',(8,1):'identity',(9,1):'address',(12,1):'facts',(14,0):'evidence',(21,1):'review_authority',(23,0):'court',(30,1):'organization',(31,1):'officer',(32,1):'approver'}

def defaults(values):
    fields=dict(values)
    if not fields.get('identity'):fields['identity']=' '.join(filter(None,[fields.get('document_type'),fields.get('document_number')]))
    if fields.get('birth_date'):
        parts=fields['birth_date'].split('-')
        if len(parts)==3:
            for key,value in zip(('birth_year','birth_month','birth_day'),parts):
                if not fields.get(key):fields[key]=value
    return fields

def bindings(i,j,text):
    if (i,j) in BLANKS:return [('field',BLANKS[i,j],text)]
    if i==2:return [('field','document_number_label',text)]
    patterns={4:[('            ','english_name')],6:[('     ','birth_month'),('     ','birth_day')],18:[('____','law_article'),('____','law_clause'),('20XX年XX月XX日','deadline')],33:[('20XX年XX月XX日','document_date')]}
    items=[];cursor=0
    for marker,key in patterns.get(i,[]):
        pos=text.find(marker,cursor)
        if pos<0:continue
        items.append(('text','',text[cursor:pos]));items.append(('field',key,marker));cursor=pos+len(marker)
    items.append(('text','',text[cursor:]));return items

def xml_root():
    with ZipFile(ASSET) as z:return ET.fromstring(z.read('word/document.xml'))

def style(node):
    result={}
    if node is None:return result
    font=node.find(W+'rFonts');size=node.find(W+'sz');bold=node.find(W+'b');under=node.find(W+'u')
    if font is not None:result['fontFamily']=font.get(W+'eastAsia') or font.get(W+'ascii')
    if size is not None:result['fontSize']=str(int(size.get(W+'val'))/2)+'pt'
    if bold is not None:result['fontWeight']='400' if bold.get(W+'val')=='0' else '700'
    if under is not None:result['textDecoration']='underline'
    return result

def layout():
    root=xml_root();body=root.find(W+'body');paragraphs=[]
    for i,p in enumerate(body.findall(W+'p')):
        ps={};pr=p.find(W+'pPr')
        if pr is not None:
            align=pr.find(W+'jc');spacing=pr.find(W+'spacing');indent=pr.find(W+'ind')
            if align is not None:ps['textAlign']={'both':'justify'}.get(align.get(W+'val'),align.get(W+'val'))
            if spacing is not None:
                for key,css in [('before','marginTop'),('after','marginBottom')]:
                    if spacing.get(W+key):ps[css]=str(int(spacing.get(W+key))/20)+'pt'
                if spacing.get(W+'line'):
                    ps['lineHeight']=str(int(spacing.get(W+'line'))/20)+'pt' if spacing.get(W+'lineRule')=='exact' else str(int(spacing.get(W+'line'))/240)
            if indent is not None:
                for key,css in [('firstLine','textIndent'),('left','marginLeft'),('right','marginRight')]:
                    if indent.get(W+key):ps[css]=str(int(indent.get(W+key))/20)+'pt'
        runs=[]
        for j,r in enumerate(p.findall(W+'r')):
            text=''.join(t.text or '' for t in r.iter(W+'t'))
            for kind,key,content in bindings(i,j,text):runs.append({'kind':kind,'key':key,'text':content,'style':style(r.find(W+'rPr'))})
        paragraphs.append({'style':ps,'runs':runs})
    sect=body.find(W+'sectPr');m=sect.find(W+'pgMar');sz=sect.find(W+'pgSz')
    page={'width':str(int(sz.get(W+'w'))/20)+'pt','minHeight':str(int(sz.get(W+'h'))/20)+'pt'}
    for key in ['top','right','bottom','left']:page['padding'+key.title()]=str(int(m.get(W+key))/20)+'pt'
    return {'page':page,'paragraphs':paragraphs}

def make_docx(values):
    values=defaults(values)
    with ZipFile(ASSET) as source:
        raw=source.read('word/document.xml')
        for _,(prefix,uri) in ET.iterparse(BytesIO(raw),events=['start-ns']):
            if not re.match(r'ns\d+$',prefix):ET.register_namespace(prefix,uri)
        root=ET.fromstring(raw)
        for i,p in enumerate(root.find(W+'body').findall(W+'p')):
            for j,r in enumerate(p.findall(W+'r')):
                nodes=list(r.iter(W+'t'));text=''.join(t.text or '' for t in nodes)
                parts=bindings(i,j,text);changed=False;pieces=[]
                for kind,key,original in parts:
                    value=values.get(key,'') if kind=='field' else ''
                    if value:
                        if key in {'deadline','document_date'} and re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):
                            y,m,d=value.split('-');value=f'{y}年{int(m)}月{int(d)}日'
                        pieces.append(value);changed=True
                    else:pieces.append(original)
                if changed and nodes:
                    nodes[0].text=''.join(pieces);nodes[0].set('{http://www.w3.org/XML/1998/namespace}space','preserve')
                    for node in nodes[1:]:node.text=''
        output=BytesIO()
        with ZipFile(output,'w') as target:
            for item in source.infolist():target.writestr(item,ET.tostring(root,encoding='utf-8',xml_declaration=True) if item.filename=='word/document.xml' else source.read(item.filename))
        return output.getvalue()

"""Fill original Word text ranges; retain paragraph/run properties and package assets."""
from pathlib import Path
from zipfile import ZipFile
from io import BytesIO
from copy import deepcopy
from xml.etree import ElementTree as ET
import json,re
ROOT=Path(__file__).resolve().parent.parent/'templates'/'records'
W='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'

def definition(identifier):
    if identifier not in {'bride','bride_family','border_case'}:return None
    return json.loads((ROOT/(identifier+'.json')).read_text())

def replacement(field,values):
    value=values.get(field['id'],'').strip()
    if not value and 'blank' in field:return field['blank']
    return field.get('prefix','')+' '+(value or '________')+' '

def render_original(template):
    paragraphs=list(template['word_paragraphs']);values=template.get('values',{})
    for field in sorted(template['fields'],key=lambda f:(f['paragraph'],f['start']),reverse=True):
        i=field['paragraph'];text=paragraphs[i];value=replacement(field,values)
        if text[field['end']:].startswith('问：'):value+='\n'
        paragraphs[i]=text[:field['start']]+value+text[field['end']:]
    return '\n'.join(paragraphs)

def replace_range(paragraph,start,end,value):
    nodes=list(paragraph.iter(W+'t'));offset=0;inserted=False
    for node in nodes:
        text=node.text or '';stop=offset+len(text)
        if not inserted and (offset<=start<stop or (node is nodes[-1] and start==stop)):
            node.text=text[:start-offset]+value+(text[end-offset:] if end<=stop else '')
            node.set('{http://www.w3.org/XML/1998/namespace}space','preserve');inserted=True
        elif inserted and offset<end:
            node.text=text[max(0,end-offset):] if end<stop else ''
        offset=stop
    if not inserted:raise ValueError('原模板回填位置不存在')

def export_original(template,label,appendix=''):
    spec=definition(template['id'])
    if not spec or template.get('word_asset')!=spec['asset']:raise ValueError('原模板版本不匹配')
    with ZipFile(ROOT/spec['asset']) as source:
        raw=source.read('word/document.xml')
        for _,(prefix,uri) in ET.iterparse(BytesIO(raw),events=['start-ns']):
            if not re.match(r'ns\d+$',prefix):ET.register_namespace(prefix,uri)
        root=ET.fromstring(raw);body=root.find(W+'body');paragraphs=list(body.iter(W+'p'))
        for field in sorted(template['fields'],key=lambda f:(f['paragraph'],f['start']),reverse=True):
            value=replacement(field,template.get('values',{}))
            if template['word_paragraphs'][field['paragraph']][field['end']:].startswith('问：'):value+='\n'
            replace_range(paragraphs[field['paragraph']],field['start'],field['end'],value)
        # Newlines inside filled answers must become Word line breaks.
        for run in root.iter(W+'r'):
            for node in list(run):
                if node.tag==W+'t' and '\n' in (node.text or ''):
                    index=list(run).index(node);parts=node.text.split('\n');run.remove(node)
                    for number,part in enumerate(parts):
                        if number:run.insert(index,ET.Element(W+'br'));index+=1
                        t=ET.Element(W+'t',{'{http://www.w3.org/XML/1998/namespace}space':'preserve'});t.text=part;run.insert(index,t);index+=1
        def paragraph(text):
            p=ET.Element(W+'p');run=ET.SubElement(p,W+'r')
            props=paragraphs[0].find('.//'+W+'rPr')
            if props is not None:run.append(deepcopy(props))
            ET.SubElement(run,W+'t').text=text
            return p
        body.insert(0,paragraph(label))
        if appendix:
            for line in appendix.splitlines():body.insert(len(body)-1,paragraph(line))
        output=BytesIO()
        with ZipFile(output,'w') as target:
            for item in source.infolist():target.writestr(item,ET.tostring(root,encoding='utf-8',xml_declaration=True) if item.filename=='word/document.xml' else source.read(item.filename))
        return output.getvalue()

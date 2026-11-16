"""Inspect immutable DOCX inputs and render with explicitly installed local fonts.
No fonts are downloaded, copied or distributed. Supply the existing document
skill renderer and bundled Python executable via arguments.
"""
import argparse,hashlib,json,os,subprocess
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile
from html import escape
W='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def inspect_docx(path):
    with ZipFile(path) as z:
        fonts={};text=[]
        for name in z.namelist():
            if name.startswith('word/') and name.endswith('.xml'):
                root=ET.fromstring(z.read(name))
                for f in root.iter(W+'rFonts'):
                    for k,v in f.attrib.items():fonts.setdefault(k.removeprefix(W),set()).add(v)
                if name=='word/document.xml':text=[x.text or '' for x in root.iter(W+'t')]
        return {'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'fonts':{k:sorted(v) for k,v in fonts.items()},'cjk_characters':sum('\u4e00'<=c<='\u9fff' for c in ''.join(text))}


def main():
    p=argparse.ArgumentParser();p.add_argument('files',nargs='+',type=Path);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--font-dir',action='append',type=Path,required=True);p.add_argument('--renderer',type=Path,required=True);p.add_argument('--python',required=True)
    args=p.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    for d in args.font_dir:
        if not d.is_dir():raise SystemExit('Font directory does not exist')
    cache=args.output/'font-cache';cache.mkdir(exist_ok=True)
    config=args.output/'fonts.conf'
    config.write_text('<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig>'+''.join('<dir>'+escape(str(d.resolve()))+'</dir>' for d in args.font_dir)+'<cachedir>'+escape(str(cache.resolve()))+'</cachedir></fontconfig>')
    env={**os.environ,'FONTCONFIG_FILE':str(config.resolve())};report=[]
    for source in args.files:
        before=inspect_docx(source);out=args.output/source.stem
        run=subprocess.run([args.python,str(args.renderer),str(source.resolve()),'--output_dir',str(out.resolve()),'--emit_pdf'],env=env,capture_output=True,text=True)
        (args.output/(source.stem+'.log')).write_text(run.stdout+run.stderr)
        after=inspect_docx(source)
        report.append({'file':str(source),'input':before,'unchanged':before==after,'render_exit':run.returncode,'pages':len(list(out.glob('page-*.png')))})
        (args.output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        if run.returncode:raise SystemExit('Rendering failed; inspect local log')

if __name__=='__main__':main()

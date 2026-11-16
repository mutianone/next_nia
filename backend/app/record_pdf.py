"""Bounded local DOCX rendering. No document content leaves the server."""
from io import BytesIO
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from threading import BoundedSemaphore
from pypdf import PdfReader

_slots=BoundedSemaphore(2)

class PDFError(Exception):pass

def binary():
    configured=os.environ.get('CRJ_SOFFICE_BIN','soffice')
    found=shutil.which(configured)
    if not found and configured=='soffice':
        app=Path('/Applications/LibreOffice.app/Contents/MacOS/soffice')
        if app.is_file():found=str(app)
    return found


def available():return binary() is not None


def convert(docx:bytes):
    executable=binary()
    if not executable:raise PDFError('PDF 导出需要服务器安装 LibreOffice，或设置 CRJ_SOFFICE_BIN；仍可下载 Word。')
    if not _slots.acquire(blocking=False):raise PDFError('PDF 转换正在处理其他文书，请稍后重试。')
    try:
        with tempfile.TemporaryDirectory(prefix='crj-pdf-') as folder:
            root=Path(folder);source=root/'record.docx';source.write_bytes(docx);source.chmod(0o600)
            env=os.environ.copy()
            # Headless macOS fontconfig needs system fonts explicitly registered.
            if not env.get('FONTCONFIG_FILE') and Path('/System/Library/Fonts').is_dir():
                config=root/'fonts.conf'
                config.write_text('''<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig><dir>/System/Library/Fonts</dir><dir>/Library/Fonts</dir><cachedir>'''+str(root/'fontcache')+'''</cachedir><alias><family>仿宋_GB2312</family><prefer><family>STFangsong</family></prefer></alias><alias><family>宋体</family><prefer><family>Songti SC</family></prefer></alias></fontconfig>''')
                env['FONTCONFIG_FILE']=str(config)
            try:
                result=subprocess.run([executable,'-env:UserInstallation='+(root/'profile').as_uri(),'--headless','--nologo','--nodefault','--norestore','--convert-to','pdf:writer_pdf_Export','--outdir',str(root),str(source)],cwd=root,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=60,check=False)
            except subprocess.TimeoutExpired:raise PDFError('PDF 转换超时，请重试或先下载 Word。') from None
            except OSError:raise PDFError('无法启动 PDF 转换程序，请检查服务器 LibreOffice 配置。') from None
            output=root/'record.pdf'
            if result.returncode or not output.is_file() or not 0<output.stat().st_size<=50*1024*1024:raise PDFError('PDF 转换未完成，原 Word 内容未改变。')
            content=output.read_bytes()
            try:
                if not content.startswith(b'%PDF-') or not 1<=len(PdfReader(BytesIO(content)).pages)<=500:raise ValueError()
            except Exception:raise PDFError('PDF 文件校验失败，请先下载 Word。') from None
            return content
    finally:_slots.release()

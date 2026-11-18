"""Bounded, text-only local document ingestion; uploaded bytes are never executed."""
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile, BadZipFile

MAX_BYTES = 10 * 1024 * 1024
MAX_TEXT = 12000


def extract(name: str, data: bytes) -> str:
    suffix = Path(name).suffix.lower()
    if not data or len(data) > MAX_BYTES:
        raise ValueError('文件不能为空，且单个文件不得超过 10 MB。')
    if suffix in {'.md', '.txt'}:
        try:
            text = data.decode('utf-8-sig')
        except UnicodeDecodeError:
            raise ValueError('文本文件请使用 UTF-8 编码。')
    elif suffix == '.pdf':
        from pypdf import PdfReader
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            raise ValueError('请先移除 PDF 密码。')
        if len(reader.pages) > 150:
            raise ValueError('PDF 请控制在 150 页以内，较长文档请拆分。')
        chunks = []
        for n, page in enumerate(reader.pages, 1):
            chunks.append(f'[第 {n} 页]\n' + (page.extract_text() or ''))
            if sum(map(len, chunks)) > MAX_TEXT:
                raise ValueError('提取文字超过 12000 字符，请拆分文档后上传，避免静默截断。')
        text = '\n\n'.join(chunks)
        if not any((p.extract_text() or '').strip() for p in reader.pages):
            raise ValueError('未提取到文字；扫描版 PDF 请先进行 OCR。')
    elif suffix == '.docx':
        from docx import Document
        try:
            with ZipFile(BytesIO(data)) as archive:
                if sum(i.file_size for i in archive.infolist()) > 30 * 1024 * 1024:
                    raise ValueError('Word 文件解压后过大，请精简后上传。')
        except BadZipFile:
            raise ValueError('不是有效的 DOCX 文件。')
        doc = Document(BytesIO(data))
        text = '\n'.join([p.text for p in doc.paragraphs] + [' | '.join(c.text for c in row.cells) for t in doc.tables for row in t.rows])
    else:
        raise ValueError('支持 PDF、DOCX、Markdown 和 TXT 文件。')
    text = text.replace('\x00', '').strip()
    if len(text) < 2:
        raise ValueError('未提取到有效文字；扫描版 PDF 请先进行 OCR。')
    if len(text) > MAX_TEXT:
        raise ValueError('提取文字超过 12000 字符，请拆分文档后上传，避免静默截断。')
    return text

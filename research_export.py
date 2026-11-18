"""Local report exports with a real heading outline and a source appendix."""
from io import BytesIO
from pathlib import Path
from html import escape
import re

from markdown_it import MarkdownIt

MD = MarkdownIt('commonmark', {'html': False}).enable('table')


def blocks(text):
    tokens = MD.parse(text)
    result, heading, rows, cells, in_cell, listing = [], None, None, None, False, 0
    for t in tokens:
        if t.type == 'heading_open':
            heading = min(3, int(t.tag[1:]))
        elif t.type in {'bullet_list_open', 'ordered_list_open'}:
            listing += 1
        elif t.type in {'bullet_list_close', 'ordered_list_close'}:
            listing -= 1
        elif t.type == 'table_open':
            rows = []
        elif t.type == 'tr_open':
            cells = []
        elif t.type in {'td_open', 'th_open'}:
            in_cell = True
        elif t.type in {'td_close', 'th_close'}:
            in_cell = False
        elif t.type == 'tr_close':
            rows.append(cells)
        elif t.type == 'table_close':
            result.append(('table', rows))
            rows = None
        elif t.type == 'inline':
            text = ''.join(c.content if c.type != 'softbreak' else '\n' for c in t.children or [] if c.type in {'text', 'code_inline', 'softbreak'})
            # Keep link destinations in exports even when labels are descriptive.
            links = [c.attrs.get('href', '') for c in t.children or [] if c.type == 'link_open']
            text += ''.join(f' ({u})' for u in links if u and u not in text)
            if in_cell:
                cells.append(text)
            else:
                result.append((f'h{heading}' if heading else 'list' if listing else 'p', text))
                heading = None
        elif t.type in {'fence', 'code_block'}:
            result.append(('code', t.content))
    return result


def content(run):
    body = blocks(run['report'])
    body += [('h1', '参考来源')]
    for source in run['sources']:
        url = source['url']
        if url.startswith('/api/documents/'):
            url = '本地上传资料 ' + source['title']
        body.append(('p', f"{source['title']}\n{url}\n" + (f"获取时间：{source['retrieved_at']}" if source.get('retrieved_at') else '未记录历史获取时间')))
    return body


def export(run: dict, format: str) -> bytes:
    body = content(run)
    title = run.get('title') or run['question']
    return word(title, body) if format == 'docx' else pdf(title, body)


def word(title, body):
    from docx import Document
    from docx.shared import Pt, Cm, RGBColor
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    doc = Document()
    section = doc.sections[0]
    section.page_width, section.page_height = Cm(21), Cm(29.7)
    section.top_margin = section.bottom_margin = Cm(2.2)
    section.left_margin = section.right_margin = Cm(2.2)
    for name in ['Normal', 'Title', 'Subtitle', 'Heading 1', 'Heading 2', 'Heading 3']:
        style = doc.styles[name]
        style.font.name = 'Microsoft YaHei'
        style.element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'), 'Microsoft YaHei')
        style.font.color.rgb = RGBColor.from_string('243C31')
        for border in list(style.element.iter(qn('w:pBdr'))):
            border.getparent().remove(border)
    doc.styles['Title'].font.color.rgb = RGBColor(0,0,0)
    doc.styles['Title'].font.underline = False
    doc.styles['Subtitle'].font.italic = False
    normal = doc.styles['Normal']
    normal.font.size = Pt(10.5)
    normal.paragraph_format.line_spacing = 1.4
    normal.paragraph_format.space_after = Pt(7)
    doc.add_paragraph(title, 'Title')
    doc.add_paragraph('研究报告', 'Subtitle')
    doc.add_heading('目录', 1)
    for kind, text in body:
        if kind in {'h1', 'h2', 'h3'}:
            p = doc.add_paragraph('    ' * (int(kind[1]) - 1) + text)
            p.paragraph_format.space_after = Pt(3)
    doc.add_page_break()
    for kind, text in body:
        if kind == 'table':
            width = max((len(r) for r in text), default=0)
            if not width:
                continue
            table = doc.add_table(rows=0, cols=width)
            table.style = 'Light Shading Accent 1'
            for n, row in enumerate(text):
                cells = table.add_row().cells
                for i, cell in enumerate(row):
                    cells[i].text = cell
                if n == 0:
                    repeat = OxmlElement('w:tblHeader')
                    table.rows[0]._tr.get_or_add_trPr().append(repeat)
            doc.add_paragraph()
        elif kind.startswith('h'):
            doc.add_heading(text, int(kind[1]))
        else:
            p = doc.add_paragraph(text, 'List Bullet' if kind == 'list' else None)
            if kind == 'code':
                for r in p.runs:
                    r.font.name = 'Consolas'
                    r.font.size = Pt(9)
    output = BytesIO()
    doc.save(output)
    return output.getvalue()


def pdf(title, body):
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib import colors
    from reportlab.platypus import BaseDocTemplate, PageTemplate, Frame, Paragraph, Spacer, PageBreak, Table, TableStyle
    from reportlab.platypus.tableofcontents import TableOfContents
    from reportlab.lib.enums import TA_LEFT
    font = 'ResearchChinese'
    if font not in pdfmetrics.getRegisteredFontNames():
        candidate = Path('C:/Windows/Fonts/simsun.ttc')
        if candidate.exists():
            pdfmetrics.registerFont(TTFont(font, str(candidate), subfontIndex=0))
        else:
            font = 'STSong-Light'
            if font not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(UnicodeCIDFont(font))
    styles = {k: ParagraphStyle(k, fontName=font, fontSize=size, leading=leading,
              textColor=colors.HexColor('#243C31'), spaceAfter=after, wordWrap='CJK',
              keepWithNext=k.startswith('h'), splitLongWords=True, alignment=TA_LEFT)
              for k, size, leading, after in [('title',22,32,20), ('h1',16,24,12), ('h2',13,21,10), ('h3',11.5,19,8), ('p',10.5,17,9), ('cell',8,13,0)]}
    def para(text, style='p'):
        return Paragraph(escape(str(text)).replace('\n', '<br/>'), styles[style])
    output = BytesIO()
    class ReportDoc(BaseDocTemplate):
        def afterFlowable(self, flowable):
            if isinstance(flowable, Paragraph) and flowable.style.name in {'h1','h2','h3'}:
                level = int(flowable.style.name[1]) - 1
                self.notify('TOCEntry', (level, flowable.getPlainText(), self.page))
    doc = ReportDoc(output, pagesize=(595.28,841.89), leftMargin=55,rightMargin=55,topMargin=52,bottomMargin=52, title=title)
    def page_number(canvas, doc):
        canvas.setFont(font, 9)
        canvas.setFillColor(colors.HexColor('#7B8982'))
        canvas.drawCentredString(297.6, 28, str(doc.page))
    doc.addPageTemplates(PageTemplate(id='report', frames=[Frame(doc.leftMargin,doc.bottomMargin,doc.width,doc.height,id='normal',leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0)], onPage=page_number))
    toc = TableOfContents()
    toc.levelStyles = [ParagraphStyle(f'toc{i}',fontName=font,fontSize=10,leading=17,leftIndent=i*14,spaceBefore=5,wordWrap='CJK') for i in range(3)]
    story = [para(title, 'title'), para('研究报告'), Spacer(1,20), para('目录','title'), toc, PageBreak()]
    for kind, text in body:
        if kind == 'table':
            width = max((len(r) for r in text), default=0)
            if not width:
                continue
            data = [[para(c, 'cell') for c in row] + [''] * (width-len(row)) for row in text]
            table = Table(data, colWidths=[doc.width/width]*width, repeatRows=1, hAlign='LEFT', splitInRow=1)
            table.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('BACKGROUND',(0,0),(-1,0),colors.HexColor('#E9F1EC')),('LINEBELOW',(0,0),(-1,0),.5,colors.HexColor('#CBDCD2')),('BOTTOMPADDING',(0,0),(-1,-1),7),('TOPPADDING',(0,0),(-1,-1),7)]))
            story += [table,Spacer(1,12)]
        elif kind == 'code':
            # Paragraph wrapping prevents long code lines escaping the page.
            story.append(para(text))
        else:
            story.append(para(('• ' if kind == 'list' else '') + text, kind if kind.startswith('h') else 'p'))
    doc.multiBuild(story)
    return output.getvalue()

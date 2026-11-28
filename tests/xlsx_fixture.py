"""在内存里拼一个 .xlsx：共享字符串 + 日期样式 + 两个工作表。

为什么不用 openpyxl：项目为了少一个依赖，解析器就是手写的（见 importer._read_xlsx），
测试样本也就用手写——顺便证明「标准库能造出 Excel 认的包」。
"""
import io
import zipfile
from datetime import datetime

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
RNS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"

STRINGS = ["时间", "金额", "收支", "备注", "支出", "收入", "午饭", "打车", "工资",
           "2026-11-28", "空表"]
S = {text: i for i, text in enumerate(STRINGS)}


def _cell(ref: str, value, kind: str = "n", style: int | None = None) -> str:
    attrs = f' r="{ref}"' + (f' t="{kind}"' if kind != "n" else "")
    if style is not None:
        attrs += f' s="{style}"'
    body = f"<v>{S[value]}</v>" if kind == "s" else f"<v>{value}</v>"
    return f"<c{attrs}>{body}</c>"


def _serial(moment: datetime) -> float:
    return (moment - datetime(1899, 12, 30)).total_seconds() / 86400


def build_xlsx() -> bytes:
    """返回一个 xlsx 的字节：3 条账，日期分别是内置格式、自定义格式、文本。"""
    rows = [
        '<row r="1">' + "".join(
            _cell(f"{col}1", text, "s") for col, text in
            zip("ABCD", ["时间", "金额", "收支", "备注"])) + "</row>",
        f'<row r="2">{_cell("A2", _serial(datetime(2026, 11, 25)), style=1)}'
        f'{_cell("B2", 25)}{_cell("C2", "支出", "s")}{_cell("D2", "午饭", "s")}</row>',
        f'<row r="3">{_cell("A3", _serial(datetime(2026, 11, 26, 12, 30)), style=2)}'
        f'{_cell("B3", 32)}{_cell("C3", "支出", "s")}{_cell("D3", "打车", "s")}</row>',
        f'<row r="4">{_cell("A4", "2026-11-28", "s")}'
        f'{_cell("B4", 1234.56)}{_cell("C4", "收入", "s")}{_cell("D4", "工资", "s")}</row>',
    ]
    sheet1 = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              f'<worksheet xmlns="{NS}"><sheetData>{"".join(rows)}</sheetData></worksheet>')
    sheet2 = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              f'<worksheet xmlns="{NS}"><sheetData>'
              f'<row r="1">{_cell("A1", "空表", "s")}</row></sheetData></worksheet>')
    shared = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              f'<sst xmlns="{NS}" count="{len(STRINGS)}" uniqueCount="{len(STRINGS)}">'
              + "".join(f"<si><t>{text}</t></si>" for text in STRINGS) + "</sst>")
    styles = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="{NS}">'
        f'<numFmts count="1"><numFmt numFmtId="164" formatCode="yyyy-mm-dd hh:mm"/></numFmts>'
        f'<cellXfs count="3"><xf numFmtId="0"/><xf numFmtId="14" applyNumberFormat="1"/>'
        f'<xf numFmtId="164" applyNumberFormat="1"/></cellXfs></styleSheet>')
    workbook = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<workbook xmlns="{NS}" xmlns:r="{RNS}"><workbookPr/><sheets>'
        f'<sheet name="账单" sheetId="1" r:id="rId1"/>'
        f'<sheet name="空表" sheetId="2" r:id="rId2"/>'
        f'</sheets></workbook>')
    workbook_rels = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Relationships xmlns="{PKG}">'
        f'<Relationship Id="rId1" Type="{RNS}/worksheet" Target="worksheets/sheet1.xml"/>'
        f'<Relationship Id="rId2" Type="{RNS}/worksheet" Target="worksheets/sheet2.xml"/>'
        f'<Relationship Id="rId3" Type="{RNS}/sharedStrings" Target="sharedStrings.xml"/>'
        f'<Relationship Id="rId4" Type="{RNS}/styles" Target="styles.xml"/>'
        f'</Relationships>')
    content_types = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Types xmlns="{CT}">'
        f'<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        f'<Default Extension="xml" ContentType="application/xml"/>'
        f'<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        f'<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        f'<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        f'<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
        f'<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        f'</Types>')
    rels = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<Relationships xmlns="{PKG}">'
            f'<Relationship Id="rId1" Type="{RNS}/officeDocument" Target="xl/workbook.xml"/>'
            f'</Relationships>')

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as book:
        book.writestr("[Content_Types].xml", content_types)
        book.writestr("_rels/.rels", rels)
        book.writestr("xl/workbook.xml", workbook)
        book.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        book.writestr("xl/sharedStrings.xml", shared)
        book.writestr("xl/styles.xml", styles)
        book.writestr("xl/worksheets/sheet1.xml", sheet1)
        book.writestr("xl/worksheets/sheet2.xml", sheet2)
    return buffer.getvalue()

"""Read PDF, Word, Excel, txt, and JPEG into pages.

Page text comes from the file itself, or from Tesseract for images. Model
output is never copied into the page text it will later be checked against.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import zipfile
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from pypdf import PdfReader


class DocumentError(ValueError):
    pass


MIME = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    ".txt": "text/plain",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}

_ACCEPT = "PDF、Word、Excel、txt 或 JPEG"
_SSML = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG = "{http://schemas.openxmlformats.org/package/2006/relationships}"


@dataclass
class Page:
    doc_id: str
    page: int
    text: str


@dataclass
class LoadedDocument:
    doc_id: str
    path: Path
    filename: str
    sha256: str
    mime: str
    pages: list[Page] = field(default_factory=list)

    @property
    def text_available(self) -> bool:
        return any(page.text.strip() for page in self.pages)

    def as_dict(self) -> dict[str, object]:
        return {
            "docId": self.doc_id,
            "filename": self.filename,
            "sha256": self.sha256,
            "mime": self.mime,
            "pageCount": max(1, len(self.pages)),
            "textAvailable": self.text_available,
            "path": str(self.path),
        }

    def text_map(self) -> dict[int, str]:
        return {page.page: page.text for page in self.pages}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _paragraph_has_page_break(paragraph) -> bool:
    breaks = paragraph._p.findall(".//" + qn("w:br"))
    return any(item.get(qn("w:type")) == "page" for item in breaks)


def read_docx(path: Path, doc_id: str) -> list[Page]:
    document = Document(str(path))
    pages: list[list[str]] = [[]]
    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if text:
            pages[-1].append(text)
        if _paragraph_has_page_break(paragraph):
            pages.append([])
    for table in document.tables:
        for row in table.rows:
            cells: list[str] = []
            for cell in row.cells:
                cell_text = " ".join(cell.text.split())
                if cell_text and cell_text not in cells:
                    cells.append(cell_text)
            if len(cells) >= 2:
                pages[0].append(f"{cells[0]}：{cells[1]}")
            elif cells:
                pages[0].append(cells[0])
    return [
        Page(doc_id, index, "\n".join(lines))
        for index, lines in enumerate(pages, start=1)
        if lines or index == 1
    ]


def read_pdf(path: Path, doc_id: str) -> list[Page]:
    reader = PdfReader(str(path))
    if not reader.pages:
        raise DocumentError(f"{path.name} 没有页面")
    pages = []
    for index, page in enumerate(reader.pages, start=1):
        pages.append(Page(doc_id, index, page.extract_text() or ""))
    return pages


def ocr_image(path: Path) -> str:
    executable = shutil.which("tesseract")
    if not executable:
        return ""
    completed = subprocess.run(
        [executable, str(path), "stdout", "-l", "chi_sim+eng", "--psm", "6"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        return ""
    return completed.stdout or ""


def read_jpeg(path: Path, doc_id: str) -> list[Page]:
    return [Page(doc_id, 1, ocr_image(path))]


def _reject(path: Path) -> DocumentError:
    suffix = path.suffix.lower()
    shown = suffix or "无后缀"
    return DocumentError(f"读不了 {shown}：{path.name}。请先另存为 {_ACCEPT}，再放进来。")


def _decode_text(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("gb18030", errors="replace")


def _pair_cells(cells: list[str]) -> list[str]:
    lines: list[str] = []
    index = 0
    while index < len(cells):
        current = cells[index].strip()
        if not current:
            index += 1
            continue
        nxt = cells[index + 1].strip() if index + 1 < len(cells) else ""
        if nxt and "：" not in current and ":" not in current:
            lines.append(f"{current}：{nxt}")
            index += 2
            continue
        lines.append(current)
        index += 1
    return lines


def read_txt(path: Path, doc_id: str) -> list[Page]:
    lines: list[str] = []
    for line in _decode_text(path.read_bytes()).splitlines():
        if "\t" in line:
            cells = [cell.strip() for cell in line.split("\t") if cell.strip()]
            lines.extend(_pair_cells(cells))
            continue
        text = line.strip()
        if text:
            lines.append(text)
    return [Page(doc_id, 1, "\n".join(lines))]


def _col_index(ref: str) -> int:
    number = 0
    for char in ref:
        if not char.isalpha():
            break
        number = number * 26 + (ord(char.upper()) - 64)
    return number


def _xlsx_value(cell: ET.Element, shared: list[str]) -> str:
    kind = cell.attrib.get("t", "")
    if kind == "inlineStr":
        return "".join(node.text or "" for node in cell.iter(_SSML + "t")).strip()
    node = cell.find(_SSML + "v")
    raw = "" if node is None or node.text is None else node.text
    if kind == "s":
        try:
            return shared[int(raw)].strip()
        except (ValueError, IndexError):
            return ""
    if kind == "b":
        return "TRUE" if raw == "1" else "FALSE"
    return raw.strip()


def _xlsx_sheet(root: ET.Element, shared: list[str]) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in root.findall(f".//{_SSML}sheetData/{_SSML}row"):
        cells: list[tuple[int, str]] = []
        for index, cell in enumerate(row.findall(_SSML + "c"), start=1):
            text = _xlsx_value(cell, shared)
            if text:
                cells.append((_col_index(cell.attrib.get("r", "")) or index, text))
        cells.sort(key=lambda item: item[0])
        values = [text for _, text in cells]
        if values:
            rows.append(values)
    return rows


def _xlsx_sheets(path: Path) -> list[list[list[str]]]:
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise DocumentError(f"读不了这个 Excel：{path.name}") from exc
    with archive:
        names = set(archive.namelist())
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.findall(_SSML + "si"):
                shared.append("".join(node.text or "" for node in item.iter(_SSML + "t")))
        if "xl/_rels/workbook.xml.rels" not in names or "xl/workbook.xml" not in names:
            raise DocumentError(f"读不了这个 Excel：{path.name}")
        targets: dict[str, str] = {}
        for rel in ET.fromstring(archive.read("xl/_rels/workbook.xml.rels")):
            rid = rel.attrib.get("Id")
            target = rel.attrib.get("Target", "")
            if rid:
                targets[rid] = target
        sheets: list[list[list[str]]] = []
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        for sheet in workbook.findall(f".//{_SSML}sheet"):
            target = targets.get(sheet.attrib.get(_REL + "id", ""), "")
            if not target:
                continue
            sheet_path = target.lstrip("/")
            if not sheet_path.startswith("xl/"):
                sheet_path = "xl/" + sheet_path
            if sheet_path not in names:
                continue
            sheets.append(_xlsx_sheet(ET.fromstring(archive.read(sheet_path)), shared))
        return sheets


def _sheet_text(rows: list[list[str]]) -> str:
    lines: list[str] = []
    for row in rows:
        lines.extend(_pair_cells(row))
    return "\n".join(lines)


def read_xlsx(path: Path, doc_id: str) -> list[Page]:
    pages = [
        Page(doc_id, index, _sheet_text(rows))
        for index, rows in enumerate(_xlsx_sheets(path), start=1)
    ]
    return pages or [Page(doc_id, 1, "")]


@contextmanager
def _office(prog_id: str, missing: str):
    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:
        raise DocumentError(missing) from exc
    pythoncom.CoInitialize()
    app = None
    try:
        try:
            app = win32com.client.DispatchEx(prog_id)
            app.Visible = False
        except Exception as exc:
            raise DocumentError(missing) from exc
        yield app
    finally:
        if app is not None:
            try:
                app.DisplayAlerts = False
                app.Quit()
            except Exception:
                pass
        pythoncom.CoUninitialize()


def _clean_word(text: str) -> str:
    return " ".join(text.replace("\x07", " ").replace("\r", " ").replace("\x0b", " ").split())


def _word_lines(document) -> list[str]:
    lines: list[str] = []
    for paragraph in document.Paragraphs:
        if paragraph.Range.Tables.Count:
            continue
        text = _clean_word(paragraph.Range.Text)
        if text:
            lines.append(text)
    for table in document.Tables:
        for row_index in range(1, table.Rows.Count + 1):
            cells: list[str] = []
            for col_index in range(1, table.Columns.Count + 1):
                try:
                    text = _clean_word(table.Cell(row_index, col_index).Range.Text)
                except Exception:
                    continue
                if text and text not in cells:
                    cells.append(text)
            lines.extend(_pair_cells(cells))
    return lines


def read_doc(path: Path, doc_id: str) -> list[Page]:
    missing = f"这台电脑没有 Word，读不了 {path.name}"
    lines: list[str] = []
    try:
        with _office("Word.Application", missing) as word:
            word.DisplayAlerts = 0
            document = word.Documents.Open(str(path.resolve()), False, True, False)
            try:
                lines = _word_lines(document)
            finally:
                document.Close(False)
    except DocumentError:
        raise
    except Exception as exc:
        raise DocumentError(f"读不了这个 Word：{path.name}") from exc
    return [Page(doc_id, 1, "\n".join(lines))]


def _cell_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _excel_grid(value: object) -> list[list[str]]:
    if value is None:
        return []
    if not isinstance(value, tuple):
        text = _cell_text(value)
        return [[text]] if text else []
    rows_in = value
    if rows_in and not isinstance(rows_in[0], tuple):
        rows_in = (rows_in,)
    rows: list[list[str]] = []
    for row in rows_in:
        cells = [_cell_text(item) for item in row]
        cells = [cell for cell in cells if cell]
        if cells:
            rows.append(cells)
    return rows


def read_xls(path: Path, doc_id: str) -> list[Page]:
    missing = f"这台电脑没有 Excel，读不了 {path.name}"
    pages: list[Page] = []
    try:
        with _office("Excel.Application", missing) as excel:
            excel.DisplayAlerts = False
            workbook = excel.Workbooks.Open(str(path.resolve()), 0, True)
            try:
                for index, sheet in enumerate(workbook.Worksheets, start=1):
                    lines: list[str] = []
                    for row in _excel_grid(sheet.UsedRange.Value):
                        lines.extend(_pair_cells(row))
                    pages.append(Page(doc_id, index, "\n".join(lines)))
            finally:
                workbook.Close(False)
    except DocumentError:
        raise
    except Exception as exc:
        raise DocumentError(f"读不了这个 Excel：{path.name}") from exc
    return pages or [Page(doc_id, 1, "")]


def read_pages(path: Path, doc_id: str) -> list[Page]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return read_pdf(path, doc_id)
    if suffix == ".docx":
        return read_docx(path, doc_id)
    if suffix == ".doc":
        return read_doc(path, doc_id)
    if suffix in {".xlsx", ".xlsm"}:
        return read_xlsx(path, doc_id)
    if suffix == ".xls":
        return read_xls(path, doc_id)
    if suffix == ".txt":
        return read_txt(path, doc_id)
    if suffix in {".jpg", ".jpeg"}:
        return read_jpeg(path, doc_id)
    raise _reject(path)


def load_documents(paths: list[Path]) -> list[LoadedDocument]:
    loaded: list[LoadedDocument] = []
    for index, path in enumerate(paths, start=1):
        if not path.is_file():
            raise DocumentError(f"找不到文件：{path}")
        suffix = path.suffix.lower()
        mime = MIME.get(suffix)
        if mime is None:
            raise _reject(path)
        doc_id = f"d{index}"
        document = LoadedDocument(
            doc_id=doc_id,
            path=path.resolve(),
            filename=path.name,
            sha256=file_sha256(path),
            mime=mime,
        )
        document.pages = read_pages(path, doc_id)
        if not document.pages:
            document.pages = [Page(doc_id, 1, "")]
        loaded.append(document)
    if not loaded:
        raise DocumentError("至少要有一份资料")
    return loaded

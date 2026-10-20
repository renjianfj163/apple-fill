import zipfile
from pathlib import Path

import pytest

from apple_fill.documents import DocumentError, load_documents
from apple_fill.extract import extract_product


def test_txt_reads_labels_and_tab_pairs(tmp_path: Path):
    path = tmp_path / "spec.txt"
    path.write_text("产品名称：青苹空气净化器\n品牌\t青苹\n", encoding="utf-8")
    product = extract_product([path], "qp-200", backend="labels")
    assert product["fields"]["productName"]["value"] == "青苹空气净化器"
    assert product["fields"]["brand"]["value"] == "青苹"


def test_txt_reads_device_labels(tmp_path: Path):
    path = tmp_path / "brief.txt"
    path.write_text("设备名称：TD-LTE数字移动电话机\n设备型号：A2108\n", encoding="utf-8")
    product = extract_product([path], "phone", backend="labels")
    assert product["fields"]["productName"]["value"] == "TD-LTE数字移动电话机"
    assert product["fields"]["modelNo"]["value"] == "A2108"


def test_txt_reads_gb18030(tmp_path: Path):
    path = tmp_path / "spec.txt"
    path.write_bytes("型号：QP-200\n".encode("gb18030"))
    product = extract_product([path], "qp-200", backend="labels")
    assert product["fields"]["modelNo"]["value"] == "QP-200"


def _write_xlsx(path: Path) -> None:
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
</Types>
"""
    root_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>
"""
    workbook = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>
</workbook>
"""
    workbook_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>
</Relationships>
"""
    shared = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="4" uniqueCount="4">
  <si><t>产品名称</t></si>
  <si><t>青苹空气净化器</t></si>
  <si><t>额定电压：220V</t></si>
  <si><t>型号</t></si>
  <si><t>QP-200</t></si>
</sst>
"""
    sheet = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    <row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>
    <row r="2"><c r="A2" t="s"><v>2</v></c></row>
    <row r="3"><c r="A3" t="s"><v>3</v></c><c r="C3" t="s"><v>4</v></c></row>
  </sheetData>
</worksheet>
"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/sharedStrings.xml", shared)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)


def test_xlsx_pairs_cells_and_keeps_labeled_cells(tmp_path: Path):
    path = tmp_path / "spec.xlsx"
    _write_xlsx(path)
    product = extract_product([path], "qp-200", backend="labels")
    assert product["fields"]["productName"]["value"] == "青苹空气净化器"
    assert product["fields"]["modelNo"]["value"] == "QP-200"
    voltage = next(item for item in product["fields"]["specs"] if item["key"] == "ratedVoltage")
    assert voltage["value"] == "220V"


def test_unknown_suffix_names_the_accepted_kinds(tmp_path: Path):
    path = tmp_path / "notes.rtf"
    path.write_text("产品名称：青苹空气净化器", encoding="utf-8")
    with pytest.raises(DocumentError, match="请先另存为 PDF、Word、Excel、txt 或 JPEG，再放进来"):
        load_documents([path])

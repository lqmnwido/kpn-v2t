import json
import sys
from pathlib import Path
from zipfile import ZipFile

from docx import Document
from docx.oxml import OxmlElement
from docx.shared import Pt


def load_document(template_path):
    with ZipFile(template_path) as package:
        parts = package.infolist()
        if len(parts) > 1024 or sum(part.file_size for part in parts) > 50 * 1024 * 1024:
            raise ValueError("Word template package is too large")
    return Document(template_path)


def answer_tables(document, headings):
    tables = {heading: [] for heading in headings}
    for table in document.tables:
        if len(table.rows) == 2 and len(table.rows[0].cells) == 1 and len(table.rows[1].cells) == 1:
            heading = " ".join(table.cell(0, 0).text.split())
            if heading in tables:
                tables[heading].append(table)
    for number, heading in enumerate(headings, 1):
        if len(tables[heading]) != 1:
            raise ValueError(f"Expected one report answer table for section {number}: {heading}")
    return [tables[heading][0] for heading in headings]


def fill_report(template_path, json_path, output_path):
    data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    document = load_document(template_path)
    sections = data["sections"]
    if [section["number"] for section in sections] != list(range(1, 9)):
        raise ValueError("Expected eight report sections in numbered order")

    for section, table in zip(sections, answer_tables(document, [section["heading"] for section in sections])):
        # Long answer rows can continue on the next page with their section heading.
        heading_properties = table.rows[0]._tr.get_or_add_trPr()
        if not heading_properties.xpath("./w:tblHeader"):
            heading_properties.append(OxmlElement("w:tblHeader"))
        for flag in table.rows[1]._tr.xpath("./w:trPr/w:cantSplit"):
            flag.getparent().remove(flag)
        cell = table.cell(1, 0)
        for extra in cell.paragraphs[1:]:
            extra._p.getparent().remove(extra._p)
        entries = section["paragraphs"]
        if not entries or [entry["number"] for entry in entries] != list(range(1, len(entries) + 1)):
            raise ValueError(f"Invalid paragraph numbering for section {section['number']}")
        for index, entry in enumerate(entries):
            paragraph = cell.paragraphs[0] if index == 0 else cell.add_paragraph()
            paragraph.clear()
            paragraph.paragraph_format.space_before = Pt(0)
            paragraph.paragraph_format.space_after = Pt(6 if index + 1 < len(entries) else 0)
            run = paragraph.add_run(f"{entry['number']}. {entry['text']}")
            run.font.name = "Arial"
            run.font.size = Pt(12)

    document.save(output_path)


if __name__ == "__main__":
    if sys.argv[1] == "--validate":
        headings = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
        answer_tables(load_document(sys.argv[2]), headings)
    else:
        fill_report(*sys.argv[1:])

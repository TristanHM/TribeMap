"""Read the GM's .docx turn report as plain text lines (no python-docx needed).

Each paragraph becomes one line; tabs become '\t'; a line break inside a paragraph starts a new line.
"""
import zipfile
import xml.etree.ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def lines(path):
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    out = []
    for p in root.iter(W + "p"):
        buf = []
        for el in p.iter():
            if el.tag == W + "t":
                buf.append(el.text or "")
            elif el.tag == W + "tab":
                buf.append("\t")
            elif el.tag in (W + "br", W + "cr"):
                buf.append("\n")
        out.extend("".join(buf).split("\n"))
    return out


def read_report(path):
    """Lines of a report from .docx or a plain-text file."""
    path = str(path)
    if path.lower().endswith(".docx"):
        return lines(path)
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        return f.read().splitlines()

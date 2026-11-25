"""Render data/docs/pdf_src/*.md into real PDF files in data/docs/pdf/.

The RAG pipeline then reads the PDFs (not the Markdown), exactly as it would with a company's real policy PDFs.
Run:  python -m scripts.render_pdfs
"""
import re
from pathlib import Path

from fpdf import FPDF

from app.config import get_settings

FONT_CANDIDATES = [Path("C:/Windows/Fonts/arial.ttf"), Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")]


class DocPDF(FPDF):
    def __init__(self, title: str):
        super().__init__(format="A4")
        self.doc_title = title
        self.set_auto_page_break(True, margin=18)
        self.set_margins(20, 18, 20)
        font = next((f for f in FONT_CANDIDATES if f.exists()), None)
        if font:
            bold = font.with_name("arialbd.ttf") if font.name == "arial.ttf" else font.with_name("DejaVuSans-Bold.ttf")
            self.add_font("Body", "", str(font))
            self.add_font("Body", "B", str(bold if bold.exists() else font))
            self.family = "Body"
        else:
            self.family = "Helvetica"

    def header(self):
        self.set_font(self.family, "B", 8)
        self.set_text_color(31, 92, 74)
        self.cell(0, 6, "THISTLE & PINE", new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(31, 92, 74)
        self.line(20, self.get_y(), 190, self.get_y())
        self.ln(4)
        self.set_text_color(30, 30, 30)

    def footer(self):
        self.set_y(-14)
        self.set_font(self.family, "", 7)
        self.set_text_color(120, 120, 120)
        self.cell(0, 6, f"{self.doc_title}  ·  Page {self.page_no()}", align="C")


def safe(text: str, unicode_font: bool) -> str:
    if unicode_font:
        return text
    return (text.replace("–", "-").replace("—", "-").replace("’", "'").replace("‘", "'")
            .replace("“", '"').replace("”", '"').replace("…", "...").encode("latin-1", "replace").decode("latin-1"))


def render(md_path: Path, out_dir: Path) -> Path:
    lines = md_path.read_text(encoding="utf-8").splitlines()
    title = next((l[2:].strip() for l in lines if l.startswith("# ")), md_path.stem)
    pdf = DocPDF(title)
    uni = pdf.family == "Body"
    pdf.set_title(title)
    pdf.set_author("Thistle & Pine Ltd")
    pdf.add_page()

    table: list[list[str]] = []

    def flush_table():
        if not table:
            return
        pdf.set_font(pdf.family, "", 9)
        with pdf.table(first_row_as_headings=True, line_height=5.5) as t:
            for row in table:
                r = t.row()
                for cell in row:
                    r.cell(safe(cell, uni))
        pdf.ln(3)
        table.clear()

    for raw in lines:
        line = raw.rstrip()
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                table.append(cells)
            continue
        flush_table()
        if not line.strip():
            pdf.ln(2)
        elif line.startswith("# "):
            pdf.set_font(pdf.family, "B", 18)
            pdf.multi_cell(0, 9, safe(line[2:], uni), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)
        elif line.startswith("## "):
            pdf.ln(2)
            pdf.set_font(pdf.family, "B", 13)
            pdf.multi_cell(0, 7, safe(line[3:], uni), new_x="LMARGIN", new_y="NEXT")
        elif line.startswith("### "):
            pdf.set_font(pdf.family, "B", 11)
            pdf.multi_cell(0, 6, safe(line[4:], uni), new_x="LMARGIN", new_y="NEXT")
        elif re.match(r"^\s*([-*]|\d+\.)\s+", line):
            m = re.match(r"^\s*([-*]|\d+\.)\s+(.*)", line)
            bullet = "•" if m.group(1) in "-*" and uni else ("-" if m.group(1) in "-*" else m.group(1))
            pdf.set_font(pdf.family, "", 10)
            pdf.set_x(24)
            pdf.multi_cell(0, 5.5, safe(f"{bullet}  {m.group(2)}", uni), markdown=True,
                           new_x="LMARGIN", new_y="NEXT")
        else:
            pdf.set_font(pdf.family, "", 10)
            pdf.multi_cell(0, 5.5, safe(line.strip(), uni), markdown=True, new_x="LMARGIN", new_y="NEXT")
    flush_table()

    out = out_dir / f"{md_path.stem}.pdf"
    pdf.output(str(out))
    return out


def main():
    docs = get_settings().docs_dir
    src, out = docs / "pdf_src", docs / "pdf"
    out.mkdir(parents=True, exist_ok=True)
    for md in sorted(src.glob("*.md")):
        print("rendered", render(md, out).name)


if __name__ == "__main__":
    main()

"""Turn raw documents (web pages, PDFs, product CSV) into citable chunks.

Every chunk carries a human-readable `location` (section heading, page number or SKU)
so answers can cite exactly where a fact came from.
"""
from __future__ import annotations

import csv
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from bs4 import BeautifulSoup
from pypdf import PdfReader

CHUNK_WORDS = 170
OVERLAP_WORDS = 40


@dataclass
class Chunk:
    id: str
    doc: str          # file name, e.g. returns-and-refunds-policy.pdf
    title: str        # document title
    source_type: str  # web | pdf | csv
    location: str     # "§ Refund timescales" | "p.2" | "SKU TP-CKW-001"
    text: str

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def citation(self) -> str:
        return f"{self.title} — {self.location}"


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _window(words: list[str], size: int = CHUNK_WORDS, overlap: int = OVERLAP_WORDS):
    """Split long text into overlapping windows so facts at a boundary aren't cut in half."""
    if len(words) <= size:
        yield words
        return
    step = size - overlap
    for start in range(0, len(words), step):
        part = words[start:start + size]
        if len(part) < 25 and start > 0:
            break
        yield part


# ---------------------------------------------------------------- web pages
def load_html(path: Path) -> list[Chunk]:
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    title = _clean(soup.title.get_text()) if soup.title else path.stem
    title = re.split(r"\s[|\-–]\s", title)[0]  # "Gift cards | Thistle & Pine" -> "Gift cards"
    for tag in soup(["script", "style", "nav", "header", "footer", "noscript", "form"]):
        tag.decompose()
    main = soup.find("main") or soup.body or soup

    # Walk the page in order, starting a new section at every h1/h2/h3.
    sections: list[tuple[str, list[str]]] = [("Overview", [])]
    for el in main.find_all(["h1", "h2", "h3", "p", "li", "tr", "dt", "dd"]):
        if el.name in ("h1", "h2", "h3"):
            sections.append((_clean(el.get_text()), []))
        elif el.name == "tr":
            cells = [_clean(c.get_text()) for c in el.find_all(["th", "td"])]
            sections[-1][1].append(" | ".join(cells))
        elif not el.find_parent(["li", "tr"]) or el.name == "li":
            sections[-1][1].append(_clean(el.get_text(" ")))

    chunks: list[Chunk] = []
    for heading, parts in sections:
        words = " ".join(p for p in parts if p).split()
        if len(words) < 8:
            continue
        for i, w in enumerate(_window(words)):
            chunks.append(Chunk(
                id=f"{path.name}#{len(chunks)}", doc=path.name, title=title, source_type="web",
                location=f"§ {heading}" + (f" (part {i + 1})" if i else ""),
                text=f"{title} — {heading}: {' '.join(w)}",
            ))
    return chunks


# ---------------------------------------------------------------- PDFs
def load_pdf(path: Path) -> list[Chunk]:
    reader = PdfReader(str(path))
    title = (reader.metadata.title if reader.metadata and reader.metadata.title else path.stem).strip()
    chunks: list[Chunk] = []
    for page_no, page in enumerate(reader.pages, start=1):
        words = _clean(page.extract_text() or "").split()
        for w in _window(words):
            chunks.append(Chunk(
                id=f"{path.name}#{len(chunks)}", doc=path.name, title=title, source_type="pdf",
                location=f"p.{page_no}", text=f"{title}: {' '.join(w)}",
            ))
    return chunks


# ---------------------------------------------------------------- products CSV
def load_products_csv(path: Path) -> list[Chunk]:
    """One chunk per product, written as a sentence so both BM25 and embeddings match it."""
    chunks: list[Chunk] = []
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            bits = [f"Product: {row['name']} (SKU {row['sku']}), category {row['category']}.",
                    f"Price £{float(row['price_gbp']):.2f}.", row.get("description", "")]
            if row.get("materials"):
                bits.append(f"Materials: {row['materials']}.")
            if row.get("dimensions"):
                bits.append(f"Dimensions: {row['dimensions']}.")
            if row.get("colour"):
                bits.append(f"Colour: {row['colour']}.")
            dw = (row.get("dishwasher_safe") or "").lower()
            if dw in ("yes", "no"):
                bits.append("Dishwasher safe." if dw == "yes" else "Not dishwasher safe.")
            if row.get("oven_safe_max_c"):
                bits.append(f"Oven safe up to {row['oven_safe_max_c']}°C.")
            if row.get("warranty_months") and row["warranty_months"] != "0":
                m = int(row["warranty_months"])
                bits.append(f"Warranty: {m // 12} years." if m % 12 == 0 else f"Warranty: {m} months.")
            if row.get("delivery_type") == "2-person":
                bits.append("2-person delivery item.")
            stock = int(row.get("stock_qty") or 0)
            bits.append("In stock." if stock > 0 else "Currently out of stock.")
            if (row.get("final_sale") or "").lower() == "yes":
                bits.append("Final Sale item: not returnable.")
            chunks.append(Chunk(
                id=f"{path.name}#{row['sku']}", doc=path.name, title="Product catalogue",
                source_type="csv", location=f"SKU {row['sku']}", text=_clean(" ".join(bits)),
            ))
    return chunks


def load_all(docs_dir: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    for p in sorted((docs_dir / "web").glob("*.html")):
        chunks += load_html(p)
    for p in sorted((docs_dir / "pdf").glob("*.pdf")):
        chunks += load_pdf(p)
    for p in sorted(docs_dir.glob("*.csv")):
        chunks += load_products_csv(p)
    return chunks

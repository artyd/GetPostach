"""Turn uploaded files into LLM-readable text.

The chat model can natively read only images and PDFs. Everything else — Excel
import databases (ТДБ по ввозах), CSV price lists, DBF customs dumps, Word docs,
JSON/XML — arrives from the frontend base64-encoded (attachment kind="file") and
is decoded + parsed here into compact plain text so Пігулькін can actually
analyse it instead of choking on raw bytes.

Every parser degrades gracefully: a missing optional library or a corrupt file
yields a short, honest note rather than an exception.
"""
from __future__ import annotations

import base64
import csv
import html
import io
import json
import re
import tempfile
from pathlib import Path

# Keep the text we feed the model bounded so a huge import DB can't blow the
# context window. We still tell the model exactly how much was truncated.
MAX_CHARS = 120_000
MAX_ROWS = 3_000

# Extensions we treat as already-plain-text (decoded, not byte-parsed).
_TEXT_EXTS = {
    "txt", "csv", "tsv", "tab", "md", "markdown", "json", "xml", "html", "htm",
    "yml", "yaml", "log", "ini", "cfg", "conf", "sql", "py", "js", "ts", "css",
    "srt", "rtf",
}


def _cap(text: str, note_prefix: str = "") -> str:
    if len(text) <= MAX_CHARS:
        return text
    cut = text[:MAX_CHARS]
    return cut + f"\n\n…(обрізано{note_prefix}, показано перші {MAX_CHARS} символів)"


def _decode(raw: bytes) -> str:
    """Decode bytes trying the encodings customs/pharma exports actually use."""
    for enc in ("utf-8-sig", "utf-8", "cp1251", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _grid(rows: list[list], total_rows: int | None = None) -> str:
    """Render a list of rows as a tab-separated grid with a truncation note."""
    lines = ["\t".join("" if c is None else str(c) for c in r) for r in rows]
    body = "\n".join(lines)
    if total_rows is not None and total_rows > len(rows):
        body += f"\n…(показано {len(rows)} з {total_rows} рядків)"
    return body


# ---------- per-format parsers ----------
def _from_csv(raw: bytes) -> str:
    text = _decode(raw)
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delim = dialect.delimiter
    except Exception:
        delim = ";" if sample.count(";") > sample.count(",") else ","
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    rows, total = [], 0
    for i, row in enumerate(reader):
        total += 1
        if i < MAX_ROWS:
            rows.append(row)
    return f"[CSV, роздільник «{delim}»]\n" + _grid(rows, total)


def _from_xlsx(raw: bytes) -> str:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    parts: list[str] = []
    try:
        for ws in wb.worksheets:
            rows, total = [], 0
            for row in ws.iter_rows(values_only=True):
                total += 1
                if total <= MAX_ROWS:
                    rows.append(list(row))
            dims = f"{total}×{ws.max_column or (len(rows[0]) if rows else 0)}"
            parts.append(f"### Аркуш «{ws.title}» ({dims})\n" + _grid(rows, total))
    finally:
        wb.close()
    return "\n\n".join(parts) if parts else "(порожня книга Excel)"


def _from_xls(raw: bytes) -> str:
    import xlrd

    book = xlrd.open_workbook(file_contents=raw)
    parts: list[str] = []
    for sh in book.sheets():
        rows = [sh.row_values(r) for r in range(min(sh.nrows, MAX_ROWS))]
        parts.append(
            f"### Аркуш «{sh.name}» ({sh.nrows}×{sh.ncols})\n" + _grid(rows, sh.nrows)
        )
    return "\n\n".join(parts) if parts else "(порожня книга Excel)"


def _from_dbf(raw: bytes) -> str:
    from dbfread import DBF

    tmp = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".dbf", delete=False) as tf:
            tf.write(raw)
            tmp = tf.name
        table = DBF(
            tmp, encoding="cp1251", ignore_missing_memofile=True,
            char_decode_errors="replace", load=False,
        )
        fields = list(table.field_names)
        rows = [fields]
        total = 0
        for rec in table:
            total += 1
            if total <= MAX_ROWS:
                rows.append([rec.get(f) for f in fields])
        return f"[DBF, {len(fields)} полів]\n" + _grid(rows, total + 1)
    finally:
        if tmp:
            Path(tmp).unlink(missing_ok=True)


def _from_docx(raw: bytes) -> str:
    import zipfile

    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names = z.namelist()
        xml = ""
        for part in ("word/document.xml",):
            if part in names:
                xml = z.read(part).decode("utf-8", "replace")
                break
    if not xml:
        return "(не вдалося знайти текст у DOCX)"
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab[^>]*/>", "\t", xml)
    xml = re.sub(r"<[^>]+>", "", xml)
    return html.unescape(xml).strip()


def _from_json(raw: bytes) -> str:
    try:
        obj = json.loads(_decode(raw))
        return json.dumps(obj, ensure_ascii=False, indent=1)
    except Exception:
        return _decode(raw)


def _sniff_zip_kind(raw: bytes) -> str | None:
    """PK-zip container: distinguish xlsx vs docx vs pptx by inner parts."""
    try:
        import zipfile

        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            names = set(z.namelist())
        if any(n.startswith("xl/") for n in names):
            return "xlsx"
        if any(n.startswith("word/") for n in names):
            return "docx"
    except Exception:
        pass
    return None


# ---------- public entry point ----------
def extract_text(name: str | None, media_type: str | None, data_b64: str) -> str:
    """Decode a base64 file and return compact readable text (never raises)."""
    name = name or "файл"
    ext = (name.rsplit(".", 1)[-1].lower() if "." in name else "")
    mt = (media_type or "").lower()

    try:
        raw = base64.b64decode(data_b64, validate=False)
    except Exception:
        # Not valid base64 — assume the caller already sent us plain text.
        return _cap(str(data_b64))

    if not raw:
        return "(порожній файл)"

    # Normalise ambiguous extensions / office MIME types via magic bytes.
    if ext in ("xlsx", "xlsm", "docx", "pptx") or "openxmlformats" in mt or raw[:4] == b"PK\x03\x04":
        sniffed = _sniff_zip_kind(raw)
        if sniffed:
            ext = sniffed if ext not in ("xlsx", "xlsm", "docx") else ext
    if raw[:4] == b"\xd0\xcf\x11\xe0" and ext not in ("xls", "doc"):  # legacy OLE2
        ext = "xls" if "excel" in mt or "sheet" in mt else ext

    try:
        if ext in ("xlsx", "xlsm"):
            return _cap(_from_xlsx(raw), " аркуш")
        if ext == "xls":
            return _cap(_from_xls(raw))
        if ext == "dbf":
            return _cap(_from_dbf(raw))
        if ext == "docx":
            return _cap(_from_docx(raw))
        if ext in ("csv", "tsv", "tab"):
            return _cap(_from_csv(raw))
        if ext == "json":
            return _cap(_from_json(raw))
        if ext in _TEXT_EXTS:
            return _cap(_decode(raw))
        # Unknown: if it decodes cleanly as text, use it; else say so honestly.
        text = _decode(raw)
        printable = sum(c.isprintable() or c in "\r\n\t" for c in text[:2000])
        if text and printable / max(1, len(text[:2000])) > 0.85:
            return _cap(text)
        return (
            f"(Не вдалося розпізнати вміст файлу «{name}» — формат «{ext or mt or 'невідомий'}» "
            f"не підтримується для читання. Розмір: {len(raw)} байт.)"
        )
    except ModuleNotFoundError as exc:
        return f"(Файл «{name}»: для читання формату «{ext}» бракує бібліотеки {exc.name} на сервері.)"
    except Exception as exc:  # noqa: BLE001
        return f"(Не вдалося прочитати файл «{name}» ({ext or mt}): {exc})"

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
from collections import deque
from pathlib import Path

# Keep the text we feed the model bounded so a huge import DB can't blow the
# context window. We still tell the model exactly how much was truncated.
MAX_CHARS = 120_000

# Table rendering strategy scales with size:
# - <= FULL_DUMP_MAX rows  -> dump every row (small file, model sees all data).
# - larger                 -> aggregate the WHOLE file into a column profile
#   (totals / min / max / mean / top values) + a head & tail sample, so even a
#   500k-row import DB is analysed correctly instead of silently truncated.
FULL_DUMP_MAX = 400
SAMPLE_HEAD = 150
SAMPLE_TAIL = 30
AGG_ROW_CAP = 300_000      # cap rows scanned for aggregation (safety bound)
TOP_CATS = 12              # top distinct values listed per text column

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


def _cell(v) -> str:
    return "" if v is None else str(v)


def _num(v):
    """Parse a cell as a number, tolerating '12,5', spaces and NBSP; else None."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(" ", "").replace(" ", "")
    if not s:
        return None
    if s.count(",") == 1 and s.count(".") == 0:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _g(x: float) -> str:
    if x is None:
        return "—"
    if x == int(x) and abs(x) < 1e15:
        return str(int(x))
    return f"{x:.4f}".rstrip("0").rstrip(".")


def _grid(rows) -> str:
    return "\n".join("\t".join(_cell(c) for c in r) for r in rows)


def _render_table(title: str, header, row_iter, ncols_hint: int = 0) -> str:
    """Turn a header + streamed data rows into compact, analysis-ready text.

    Small tables are dumped in full; large ones are aggregated over the whole
    file (per-column profile) plus a head/tail sample — so nothing important is
    silently dropped and the model can answer aggregate questions correctly.
    """
    header = [_cell(c) for c in header]
    ncols = len(header) or ncols_hint
    head: list[list] = []
    tail: deque = deque(maxlen=SAMPLE_TAIL)
    num = [{"n": 0, "s": 0.0, "mn": None, "mx": None} for _ in range(ncols)]
    cat: list[dict] = [{} for _ in range(ncols)]
    total = 0

    for row in row_iter:
        row = list(row)
        total += 1
        if len(head) < max(FULL_DUMP_MAX, SAMPLE_HEAD):
            head.append(row)
        tail.append(row)
        if total <= AGG_ROW_CAP:
            for i in range(min(ncols, len(row))):
                fv = _num(row[i])
                if fv is not None:
                    d = num[i]
                    d["n"] += 1
                    d["s"] += fv
                    d["mn"] = fv if d["mn"] is None else min(d["mn"], fv)
                    d["mx"] = fv if d["mx"] is None else max(d["mx"], fv)
                else:
                    v = row[i]
                    if v not in (None, ""):
                        c = cat[i]
                        k = str(v)[:80]
                        if len(c) < 5000 or k in c:
                            c[k] = c.get(k, 0) + 1

    # Small table: show everything.
    if total <= FULL_DUMP_MAX:
        return f"{title} ({total}×{ncols})\n" + _grid([header] + head)

    # Large table: whole-file column profile + head/tail sample.
    scanned = min(total, AGG_ROW_CAP)
    lines = [f"{title} ({total}×{ncols}) — велика таблиця: профіль по всьому файлу + зразок рядків"]
    lines.append("Стовпці: " + " | ".join(header))
    scope = "по всіх рядках" if total <= AGG_ROW_CAP else f"по перших {AGG_ROW_CAP} рядках"
    lines.append(f"\nПрофіль стовпців ({scope}):")
    for i, h in enumerate(header):
        d = num[i]
        if d["n"] >= 0.6 * max(1, scanned):  # mostly-numeric column
            avg = d["s"] / d["n"] if d["n"] else 0.0
            lines.append(
                f"- «{h}» [число]: значень={d['n']}, мін={_g(d['mn'])}, "
                f"макс={_g(d['mx'])}, середнє={_g(avg)}, сума={_g(d['s'])}"
            )
        else:
            c = cat[i]
            top = sorted(c.items(), key=lambda kv: -kv[1])[:TOP_CATS]
            tops = ", ".join(f"{k} ({n})" for k, n in top) or "—"
            more = f" +ще {len(c) - TOP_CATS}" if len(c) > TOP_CATS else ""
            lines.append(f"- «{h}» [текст]: унікальних={len(c)}, топ: {tops}{more}")

    lines.append(f"\nПерші {min(SAMPLE_HEAD, total)} рядків:")
    lines.append(_grid([header] + head[:SAMPLE_HEAD]))
    skipped = total - SAMPLE_HEAD - SAMPLE_TAIL
    if skipped > 0:
        lines.append(f"…(пропущено {skipped} рядків)…")
        lines.append(f"Останні {len(tail)} рядків:")
        lines.append(_grid(list(tail)))
    return "\n".join(lines)


# ---------- per-format parsers ----------
def _from_csv(raw: bytes) -> str:
    text = _decode(raw)
    sample = text[:4096]
    try:
        delim = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except Exception:
        delim = ";" if sample.count(";") > sample.count(",") else ","
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    try:
        header = next(reader)
    except StopIteration:
        return "(порожній CSV)"
    return _render_table(f"[CSV, роздільник «{delim}»]", header, reader)


def _from_xlsx(raw: bytes) -> str:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    parts: list[str] = []
    try:
        for ws in wb.worksheets:
            it = ws.iter_rows(values_only=True)
            header = next(it, None)
            if header is None:
                parts.append(f"### Аркуш «{ws.title}» — порожній")
                continue
            parts.append(_render_table(f"### Аркуш «{ws.title}»", header, it,
                                       ncols_hint=ws.max_column or 0))
    finally:
        wb.close()
    return "\n\n".join(parts) if parts else "(порожня книга Excel)"


def _from_xls(raw: bytes) -> str:
    import xlrd

    book = xlrd.open_workbook(file_contents=raw)
    parts: list[str] = []
    for sh in book.sheets():
        if sh.nrows == 0:
            parts.append(f"### Аркуш «{sh.name}» — порожній")
            continue
        header = sh.row_values(0)
        rows = (sh.row_values(r) for r in range(1, sh.nrows))
        parts.append(_render_table(f"### Аркуш «{sh.name}»", header, rows,
                                   ncols_hint=sh.ncols))
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
        rows = ([rec.get(f) for f in fields] for rec in table)
        return _render_table(f"[DBF, {len(fields)} полів]", fields, rows)
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

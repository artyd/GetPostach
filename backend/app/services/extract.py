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
import itertools
import json
import logging
import re
import tempfile
from collections import deque
from pathlib import Path

logger = logging.getLogger("pigulkin.extract")

# Safety bounds for zip-based formats (xlsx/docx/pptx/ods/zip) — guard against
# decompression bombs: a tiny upload must not expand into gigabytes.
ZIP_MAX_TOTAL = 400 * 1024 * 1024
ZIP_MAX_FILES = 300

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
    """Parse a cell as a number across locales.

    Handles '12,5' (comma decimal), '1.234,56' (euro) and '1,234.56' (anglo)
    thousands, NBSP/space/apostrophe separators and a trailing %.
    """
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(" ", "").replace(" ", "")
    if not s:
        return None
    s = s.replace("'", "").rstrip("%")
    has_c, has_d = "," in s, "." in s
    if has_c and has_d:
        if s.rfind(",") > s.rfind("."):      # euro: 1.234,56 -> 1234.56
            s = s.replace(".", "").replace(",", ".")
        else:                                 # anglo: 1,234.56 -> 1234.56
            s = s.replace(",", "")
    elif has_c:
        if s.count(",") == 1 and len(s.split(",")[1]) != 3:
            s = s.replace(",", ".")     # 11,4 -> 11.4
        else:
            s = s.replace(",", "")       # 1,234 -> 1234
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


def _detect_header(rows: list[list]) -> int:
    """Pick the most header-like row among the first few (customs/pharma exports
    often carry a title/period line above the real column headers)."""
    best_i, best_score = 0, -1.0
    for i in range(min(len(rows), 6)):
        cells = [c for c in rows[i] if _cell(c).strip()]
        if not cells:
            continue
        nonnum = sum(1 for c in cells if _num(c) is None)
        nxt = rows[i + 1] if i + 1 < len(rows) else []
        nxt_num = sum(1 for c in nxt if _num(c) is not None)
        score = len(cells) + nonnum + (2 if nxt_num else 0) - i * 0.5
        if score > best_score:
            best_score, best_i = score, i
    return best_i


def _table_from_iter(title: str, row_iter, ncols_hint: int = 0) -> str:
    """Detect the header row from a peek, then render the rest as a table."""
    it = iter(row_iter)
    preview = [list(r) for r in itertools.islice(it, 15)]
    if not preview:
        return f"{title} (порожньо)"
    hi = _detect_header(preview)
    header = preview[hi]
    rest = itertools.chain(preview[hi + 1:], it)
    note = "" if hi == 0 else f"\n(шапку знайдено у рядку {hi + 1}; рядки над нею пропущено)"
    return _render_table(title, header, rest, ncols_hint) + note


# ---------- per-format parsers ----------
def _from_csv(raw: bytes) -> str:
    text = _decode(raw)
    sample = text[:4096]
    try:
        delim = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except Exception:
        delim = ";" if sample.count(";") > sample.count(",") else ","
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    return _table_from_iter(f"[CSV, роздільник «{delim}»]", reader)


def _from_xlsx(raw: bytes) -> str:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    parts: list[str] = []
    try:
        for ws in wb.worksheets:
            it = ws.iter_rows(values_only=True)
            parts.append(_table_from_iter(f"### Аркуш «{ws.title}»", it,
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
        rows = (sh.row_values(r) for r in range(sh.nrows))
        parts.append(_table_from_iter(f"### Аркуш «{sh.name}»", rows,
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


def _zip_guard(raw: bytes) -> None:
    """Reject decompression bombs before we expand a zip-based document."""
    import zipfile

    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        infos = z.infolist()
        if len(infos) > ZIP_MAX_FILES:
            raise ValueError(f"забагато файлів в архіві ({len(infos)})")
        if sum(i.file_size for i in infos) > ZIP_MAX_TOTAL:
            raise ValueError("розпакований розмір архіву завеликий")


def _from_docx(raw: bytes) -> str:
    import zipfile

    _zip_guard(raw)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        if "word/document.xml" not in z.namelist():
            return "(не вдалося знайти текст у DOCX)"
        xml = z.read("word/document.xml").decode("utf-8", "replace")
    # Flatten tables to tab-separated rows; keep paragraph breaks elsewhere.
    xml = re.sub(r"</w:p>\s*</w:tc>", "</w:tc>", xml)   # 1 paragraph per cell
    xml = re.sub(r"<w:tab[^>]*/>", "\t", xml)
    xml = xml.replace("</w:tc>", "\t").replace("</w:tr>", "\n").replace("</w:p>", "\n")
    xml = re.sub(r"<[^>]+>", "", xml)
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", html.unescape(xml))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _from_pptx(raw: bytes) -> str:
    import zipfile

    _zip_guard(raw)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        slides = sorted(
            (n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)),
            key=lambda n: int(re.search(r"(\d+)", n).group(1)),
        )
        out = []
        for i, n in enumerate(slides, 1):
            xml = z.read(n).decode("utf-8", "replace")
            xml = re.sub(r"</a:p>", "\n", xml)
            txt = html.unescape(re.sub(r"<[^>]+>", "", xml)).strip()
            if txt:
                out.append(f"### Слайд {i}\n{txt}")
    return "\n\n".join(out) if out else "(порожня презентація)"


def _from_ods(raw: bytes) -> str:
    import zipfile

    _zip_guard(raw)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        xml = z.read("content.xml").decode("utf-8", "replace")
    parts = []
    for tm in re.finditer(r"<table:table\b[^>]*?table:name=\"([^\"]*)\"[^>]*>(.*?)</table:table>", xml, re.S):
        name, body = tm.group(1), tm.group(2)
        rows = []
        for rm in re.finditer(r"<table:table-row\b[^>]*>(.*?)</table:table-row>", body, re.S):
            cells = []
            for cm in re.finditer(r"<table:table-cell\b([^>]*?)(?:/>|>(.*?)</table:table-cell>)", rm.group(1), re.S):
                attrs, inner = cm.group(1) or "", cm.group(2) or ""
                txt = html.unescape(re.sub(r"<[^>]+>", "", inner)).strip()
                rep = re.search(r'number-columns-repeated="(\d+)"', attrs)
                cells.extend([txt] * min(int(rep.group(1)) if rep else 1, 512))
            while cells and cells[-1] == "":
                cells.pop()
            if cells:
                rows.append(cells)
        if rows:
            parts.append(_table_from_iter(f"### Таблиця «{name}»", iter(rows)))
    return "\n\n".join(parts) if parts else "(порожній ODS)"


def _from_eml(raw: bytes) -> str:
    import email
    from email import policy

    msg = email.message_from_bytes(raw, policy=policy.default)
    hdr = [f"Від: {msg.get('from','')}", f"Кому: {msg.get('to','')}",
           f"Дата: {msg.get('date','')}", f"Тема: {msg.get('subject','')}"]
    body = ""
    try:
        b = msg.get_body(preferencelist=("plain", "html"))
        if b:
            body = b.get_content()
            if b.get_content_type() == "text/html":
                body = html.unescape(re.sub(r"<[^>]+>", " ", body))
    except Exception:  # noqa: BLE001
        body = ""
    out = ["\n".join(hdr), "", body.strip()]
    for part in msg.iter_attachments():
        fn = part.get_filename() or "attachment"
        try:
            payload = part.get_content()
            payload = payload.encode("utf-8", "replace") if isinstance(payload, str) else payload
        except Exception:  # noqa: BLE001
            payload = part.get_payload(decode=True) or b""
        if isinstance(payload, (bytes, bytearray)):
            out.append(f"\n--- Вкладення: {fn} ---\n" + _extract_bytes(fn, part.get_content_type(), bytes(payload))[:20000])
    return "\n".join(out)


def _from_msg(raw: bytes) -> str:
    import extract_msg

    tmp = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".msg", delete=False) as tf:
            tf.write(raw)
            tmp = tf.name
        m = extract_msg.Message(tmp)
        hdr = [f"Від: {m.sender or ''}", f"Кому: {m.to or ''}",
               f"Дата: {m.date or ''}", f"Тема: {m.subject or ''}"]
        out = "\n".join(hdr) + "\n\n" + (m.body or "")
        for att in (m.attachments or []):
            fn = getattr(att, "longFilename", None) or getattr(att, "shortFilename", None) or "attachment"
            data = getattr(att, "data", None)
            if isinstance(data, (bytes, bytearray)):
                out += f"\n\n--- Вкладення: {fn} ---\n" + _extract_bytes(fn, None, bytes(data))[:20000]
        m.close()
        return out
    finally:
        if tmp:
            Path(tmp).unlink(missing_ok=True)


def _from_doc(raw: bytes) -> str:
    """Legacy binary .doc — best-effort text (recommend .docx for fidelity)."""
    import olefile

    if not olefile.isOleFile(io.BytesIO(raw)):
        return _decode(raw)
    ole = olefile.OleFileIO(io.BytesIO(raw))
    try:
        data = ole.openstream("WordDocument").read() if ole.exists("WordDocument") else b""
    finally:
        ole.close()
    best = ""
    for enc in ("cp1251", "utf-16-le", "latin-1"):
        t = data.decode(enc, "ignore")
        runs = re.findall(r"[ \t\w.,;:!?\-()«»№%/@°+…Ѐ-ӿ]{4,}", t)
        cand = "\n".join(r.strip() for r in runs if len(r.strip()) >= 4)
        if len(cand) > len(best):
            best = cand
    best = re.sub(r"\n{3,}", "\n\n", best).strip()
    return best or "(Не вдалося витягти текст із .doc — збережіть як .docx для точного розбору.)"


def _from_zip(raw: bytes) -> str:
    import zipfile

    _zip_guard(raw)
    out, n = [], 0
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            n += 1
            if n > 50:
                out.append("…(в архіві ще файли — показано перші 50)")
                break
            try:
                data = z.read(info)
            except Exception as exc:  # noqa: BLE001
                out.append(f"### {info.filename}: не вдалося прочитати ({exc})")
                continue
            out.append(f"### Файл з архіву: {info.filename}\n" + _extract_bytes(info.filename, None, data)[:30000])
    return "\n\n".join(out) if out else "(порожній архів)"


def _from_json(raw: bytes) -> str:
    try:
        obj = json.loads(_decode(raw))
        return json.dumps(obj, ensure_ascii=False, indent=1)
    except Exception:
        return _decode(raw)


def _sniff_zip_kind(raw: bytes) -> str | None:
    """PK-zip container: tell xlsx / docx / pptx / ods apart by inner parts."""
    try:
        import zipfile

        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            names = set(z.namelist())
            mimetype = z.read("mimetype").decode("ascii", "ignore") if "mimetype" in names else ""
    except Exception:
        return None
    if "spreadsheet" in mimetype:
        return "ods"
    if any(n.startswith("xl/") for n in names):
        return "xlsx"
    if any(n.startswith("word/") for n in names):
        return "docx"
    if any(n.startswith("ppt/") for n in names):
        return "pptx"
    if "content.xml" in names:
        return "ods"
    return None


# ---------- public entry point ----------
def extract_text(name: str | None, media_type: str | None, data_b64: str) -> str:
    """Decode a base64 file and return compact readable text (never raises)."""
    try:
        raw = base64.b64decode(data_b64, validate=False)
    except Exception:
        # Not valid base64 — assume the caller already sent us plain text.
        return _cap(str(data_b64))
    return _extract_bytes(name or "файл", media_type, raw)


def _extract_bytes(name: str | None, media_type: str | None, raw: bytes) -> str:
    """Dispatch raw bytes to the right parser. Used directly for nested files
    (email attachments, zip entries) so recursion needs no re-encoding."""
    name = name or "файл"
    ext = (name.rsplit(".", 1)[-1].lower() if "." in name else "")
    mt = (media_type or "").lower()

    if not raw:
        return "(порожній файл)"

    # Normalise ambiguous extensions / office MIME types via magic bytes.
    if ext in ("xlsx", "xlsm", "docx", "pptx", "ods") or "openxmlformats" in mt or raw[:4] == b"PK\x03\x04":
        sniffed = _sniff_zip_kind(raw)
        if sniffed and ext not in ("xlsx", "xlsm", "docx", "pptx", "ods"):
            ext = sniffed
    if raw[:4] == b"\xd0\xcf\x11\xe0" and ext not in ("xls", "doc", "msg", "ppt"):  # legacy OLE2
        ext = "xls" if ("excel" in mt or "sheet" in mt) else ext

    try:
        if ext in ("xlsx", "xlsm"):
            return _cap(_from_xlsx(raw), " аркуш")
        if ext == "xls":
            return _cap(_from_xls(raw))
        if ext == "ods":
            return _cap(_from_ods(raw))
        if ext == "dbf":
            return _cap(_from_dbf(raw))
        if ext == "docx":
            return _cap(_from_docx(raw))
        if ext == "doc":
            return _cap(_from_doc(raw))
        if ext in ("pptx", "ppt"):
            return _cap(_from_pptx(raw))
        if ext == "eml":
            return _cap(_from_eml(raw))
        if ext == "msg":
            return _cap(_from_msg(raw))
        if ext == "zip":
            return _cap(_from_zip(raw))
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
        # A recognised binary container (zip/OOXML or OLE2) that reaches this
        # branch means we handed the model unreadable bytes instead of parsed
        # text — the exact silent degradation we want visible in the logs.
        looks_container = raw[:4] in (b"PK\x03\x04", b"\xd0\xcf\x11\xe0")
        logger.warning(
            "extract: unrecognised format for %r (ext=%r, mt=%r, %d bytes, container=%s)",
            name, ext, mt, len(raw), looks_container,
        )
        return (
            f"(Не вдалося розпізнати вміст файлу «{name}» — формат «{ext or mt or 'невідомий'}» "
            f"не підтримується для читання. Розмір: {len(raw)} байт.)"
        )
    except ModuleNotFoundError as exc:
        logger.warning(
            "extract: missing library %r to read %r (ext=%r, %d bytes)",
            exc.name, name, ext, len(raw),
        )
        return f"(Файл «{name}»: для читання формату «{ext}» бракує бібліотеки {exc.name} на сервері.)"
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "extract: failed to parse %r (ext=%r, mt=%r, %d bytes): %s",
            name, ext, mt, len(raw), exc, exc_info=True,
        )
        return f"(Не вдалося прочитати файл «{name}» ({ext or mt}): {exc})"

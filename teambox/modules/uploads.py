"""SAFE ATTACHMENTS for the home feed.
- An allow-list of file types by extension; the first bytes must match the type (a .png has to BE a PNG), otherwise refused.
- Stored outside the web root (data/feed/files/<random 32 hex>, no extension, never the member's file name).
- Served with the right Content-Type, Content-Disposition, X-Content-Type-Options: nosniff and a CSP sandbox.
  Only pictures and videos are ever shown inline. HTML, SVG and everything else is DOWNLOAD ONLY as application/octet-stream,
  so nothing uploaded can run as a page on the team box's address.
- Notes (txt/md) and spreadsheets (csv/xlsx/ods) get a PREVIEW made once at upload time: plain text / a table of strings,
  which the UI shows with textContent (never as HTML)."""
import csv
import io
import os
import re
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile

# ext -> (kind, content type when served, inline?, magic check)
TYPES = {
    "png": ("picture", "image/png", True, "png"), "jpg": ("picture", "image/jpeg", True, "jpg"), "jpeg": ("picture", "image/jpeg", True, "jpg"),
    "gif": ("picture", "image/gif", True, "gif"), "webp": ("picture", "image/webp", True, "webp"),
    "mp4": ("video", "video/mp4", True, "ftyp"), "m4v": ("video", "video/mp4", True, "ftyp"), "mov": ("video", "video/quicktime", True, "ftyp"),
    "webm": ("video", "video/webm", True, "ebml"),
    "txt": ("note", "text/plain; charset=utf-8", False, "text"), "md": ("note", "text/markdown; charset=utf-8", False, "text"),
    "log": ("note", "text/plain; charset=utf-8", False, "text"), "json": ("note", "application/json", False, "text"),
    "csv": ("spreadsheet", "text/csv; charset=utf-8", False, "text"), "tsv": ("spreadsheet", "text/tab-separated-values; charset=utf-8", False, "text"),
    "xlsx": ("spreadsheet", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", False, "zip"),
    "ods": ("spreadsheet", "application/vnd.oasis.opendocument.spreadsheet", False, "zip"),
    "xls": ("spreadsheet", "application/vnd.ms-excel", False, "ole"),
    "pdf": ("pdf", "application/pdf", False, "pdf"),
    "docx": ("document", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", False, "zip"),
    "odt": ("document", "application/vnd.oasis.opendocument.text", False, "zip"),
    "pptx": ("document", "application/vnd.openxmlformats-officedocument.presentationml.presentation", False, "zip"),
    "odp": ("document", "application/vnd.oasis.opendocument.presentation", False, "zip"),
    "doc": ("document", "application/msword", False, "ole"), "rtf": ("document", "application/rtf", False, "text"),
    # accepted, but ONLY ever downloaded as opaque bytes - never rendered by the browser on this site
    "html": ("download-only", "application/octet-stream", False, None), "htm": ("download-only", "application/octet-stream", False, None),
    "svg": ("download-only", "application/octet-stream", False, None), "xml": ("download-only", "application/octet-stream", False, None),
}
FILE_CSP = "default-src 'none'; img-src 'self'; media-src 'self'; style-src 'unsafe-inline'; sandbox"
PREVIEW_ROWS, PREVIEW_COLS, PREVIEW_TEXT = 20, 20, 20000
XML_MAX = 30 * 2 ** 20                         # never decompress more than this from one member of an xlsx/ods zip


class UploadError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code, self.msg = code, msg


def clean_name(name):
    """The member's file name, for display and Content-Disposition only (never used as a path)."""
    name = urllib.parse.unquote(str(name or "")).replace("\\", "/").split("/")[-1]
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C").strip().strip(".")
    name = re.sub(r"\s+", " ", name)[:120]
    return name or "file"


def ext_of(name):
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def classify(name):
    e = ext_of(name)
    if e not in TYPES:
        raise UploadError(415, "'.%s' files can not be attached. Allowed: pictures (png jpg gif webp), videos (mp4 mov webm), notes (txt md), "
                               "spreadsheets (csv xlsx ods xls), PDFs and documents (docx odt pptx doc rtf)." % (e or "?"))
    return e, TYPES[e]


def check_magic(e, magic, head):
    ok = {
        "png": head.startswith(b"\x89PNG\r\n\x1a\n"), "jpg": head.startswith(b"\xff\xd8\xff"),
        "gif": head[:6] in (b"GIF87a", b"GIF89a"), "webp": head[:4] == b"RIFF" and head[8:12] == b"WEBP",
        "ftyp": head[4:8] == b"ftyp", "ebml": head.startswith(b"\x1a\x45\xdf\xa3"), "pdf": head.startswith(b"%PDF-"),
        "zip": head.startswith(b"PK\x03\x04"), "ole": head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"),
        "text": b"\x00" not in head,
        None: True,
    }[magic]
    if not ok:
        raise UploadError(415, "the file's content does not match its '.%s' name - refused" % e)


# ---------------------------------------------------------------- previews (made once, at upload time)
def _decode(raw):
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _cell(v):
    v = "" if v is None else str(v)
    return v[:200]


def preview_text(path):
    with open(path, "rb") as fh:
        raw = fh.read(PREVIEW_TEXT + 4)
    t = _decode(raw)
    return {"type": "text", "text": t[:PREVIEW_TEXT], "truncated": len(raw) > PREVIEW_TEXT}


def preview_csv(path, delim=None):
    with open(path, "rb") as fh:
        raw = fh.read(256 * 1024)
    t = _decode(raw)
    if delim is None:
        try:
            delim = csv.Sniffer().sniff(t[:4096], delimiters=",;\t|").delimiter
        except csv.Error:
            delim = ","
    rows = []
    for row in csv.reader(io.StringIO(t), delimiter=delim):
        rows.append([_cell(c) for c in row[:PREVIEW_COLS]])
        if len(rows) >= PREVIEW_ROWS:
            break
    return {"type": "table", "rows": rows}


def _xml_open(zf, member):
    info = zf.getinfo(member)
    if info.file_size > XML_MAX:
        raise UploadError(415, "the spreadsheet is too big inside to preview")
    fh = zf.open(member)
    head = fh.peek(4096)[:4096] if hasattr(fh, "peek") else b""
    if b"<!DOCTYPE" in head or b"<!ENTITY" in head:      # spreadsheets never need a DTD: refuse entity tricks outright
        raise UploadError(415, "the spreadsheet contains a DTD - no preview")
    return fh


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _col_index(ref):
    letters = re.match(r"[A-Z]+", ref or "")
    n = 0
    for ch in (letters.group(0) if letters else ""):
        n = n * 26 + (ord(ch) - 64)
    return max(n - 1, 0)


def preview_xlsx(path):
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        strings = []
        if "xl/sharedStrings.xml" in names:
            with _xml_open(zf, "xl/sharedStrings.xml") as fh:
                strings = _shared_strings(fh)
        sheets = sorted((n for n in names if re.match(r"^xl/worksheets/sheet\d+\.xml$", n)), key=lambda n: int(re.findall(r"\d+", n)[-1]))
        if not sheets:
            raise UploadError(415, "no worksheet found")
        with _xml_open(zf, sheets[0]) as fh:
            return _sheet_rows(fh, strings)


def _shared_strings(fh):
    strings, cur = [], None
    for ev, el in ET.iterparse(fh, events=("start", "end")):
        t = _local(el.tag)
        if ev == "start" and t == "si":
            cur = []
        elif ev == "end" and t == "t" and cur is not None:
            cur.append(el.text or "")
        elif ev == "end" and t == "si":
            strings.append("".join(cur or [])[:200])
            cur = None
            el.clear()
            if len(strings) >= 50000:
                break
    return strings


def _sheet_rows(fh, strings):
    rows, row, vtype, val = [], None, None, None
    for ev, el in ET.iterparse(fh, events=("start", "end")):
        t = _local(el.tag)
        if ev == "start" and t == "row":
            row = []
        elif ev == "start" and t == "c":
            vtype, val, col = el.get("t"), None, _col_index(el.get("r"))
            if row is not None and col < PREVIEW_COLS:
                while len(row) < col:
                    row.append("")
        elif ev == "end" and t in ("v", "t") and row is not None:
            val = (val or "") + (el.text or "")
        elif ev == "end" and t == "c" and row is not None:
            if vtype == "s":
                try:
                    val = strings[int(val)]
                except (ValueError, IndexError, TypeError):
                    val = ""
            if len(row) < PREVIEW_COLS:
                row.append(_cell(val))
            el.clear()
        elif ev == "end" and t == "row":
            rows.append(row or [])
            row = None
            el.clear()
            if len(rows) >= PREVIEW_ROWS:
                break
    return {"type": "table", "rows": rows}


def preview_ods(path):
    with zipfile.ZipFile(path) as zf:
        with _xml_open(zf, "content.xml") as fh:
            return _ods_rows(fh)


def _ods_rows(fh):
    rows, row, depth_table, txt = [], None, 0, None
    for ev, el in ET.iterparse(fh, events=("start", "end")):
        t = _local(el.tag)
        if t == "table":
            if ev == "start":
                depth_table += 1
            elif rows or depth_table:
                break                                  # first sheet only
        elif t == "table-row" and ev == "start":
            row = []
        elif t == "table-cell" and ev == "start":
            txt = []
        elif t == "p" and ev == "end" and txt is not None:
            txt.append("".join(el.itertext()))
        elif t == "table-cell" and ev == "end" and row is not None:
            rep = 1
            for k, v in el.attrib.items():
                if _local(k) == "number-columns-repeated":
                    try:
                        rep = max(1, min(int(v), PREVIEW_COLS))
                    except ValueError:
                        rep = 1
            for _ in range(rep):
                if len(row) < PREVIEW_COLS:
                    row.append(_cell("\n".join(txt or [])))
            txt = None
            el.clear()
        elif t == "table-row" and ev == "end" and row is not None:
            while row and row[-1] == "":
                row.pop()
            rows.append(row)
            row = None
            el.clear()
            if len(rows) >= PREVIEW_ROWS:
                break
    while rows and not rows[-1]:
        rows.pop()
    return {"type": "table", "rows": rows}


def make_preview(path, e):
    try:
        if e in ("txt", "md", "log", "json"):
            return preview_text(path)
        if e == "csv":
            return preview_csv(path)
        if e == "tsv":
            return preview_csv(path, "\t")
        if e == "xlsx":
            return preview_xlsx(path)
        if e == "ods":
            return preview_ods(path)
    except (UploadError, zipfile.BadZipFile, ET.ParseError, KeyError, OSError, ValueError, RuntimeError, EOFError) as exc:
        return {"type": "none", "why": "no preview: %s" % (getattr(exc, "msg", None) or type(exc).__name__)}
    return None


# ---------------------------------------------------------------- serving
def disposition(kind, name, inline):
    ascii_name = re.sub(r'[^A-Za-z0-9._ -]', "_", name)[:120] or "file"
    return '%s; filename="%s"; filename*=UTF-8\'\'%s' % ("inline" if inline else "attachment", ascii_name, urllib.parse.quote(name, safe=""))


def serve_file(handler, path, meta, want_download, sec_headers):
    """Streams the file (with single-range support, for video seeking). Only pictures/videos may be inline; everything else is
    an attachment, and download-only types are always application/octet-stream."""
    e = meta["ext"]
    kind, ctype, inline_ok, _m = TYPES.get(e, ("download-only", "application/octet-stream", False, None))
    inline = inline_ok and not want_download
    if not inline and kind == "download-only":
        ctype = "application/octet-stream"
    size = os.path.getsize(path)
    start, end, code = 0, size - 1, 200
    rng = handler.headers.get("Range") or ""
    m = re.match(r"^bytes=(\d*)-(\d*)$", rng.strip())
    if m and size and (m.group(1) or m.group(2)):
        if m.group(1):
            start = int(m.group(1))
            end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
        else:
            start = max(size - int(m.group(2)), 0)
        if start > end or start >= size:
            handler.send_response(416)
            handler.send_header("Content-Range", "bytes */%d" % size)
            handler.send_header("Content-Length", "0")
            handler.end_headers()
            return None
        code = 206
    handler.send_response(code)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(end - start + 1))
    handler.send_header("Content-Disposition", disposition(kind, meta["name"], inline))
    handler.send_header("Accept-Ranges", "bytes")
    if code == 206:
        handler.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
    for k, v in sec_headers:
        if k != "Content-Security-Policy":
            handler.send_header(k, v)
    handler.send_header("Content-Security-Policy", FILE_CSP)
    handler.send_header("Cross-Origin-Resource-Policy", "same-origin")
    handler.end_headers()
    if handler.command == "HEAD":
        return None
    with open(path, "rb") as fh:
        fh.seek(start)
        left = end - start + 1
        while left > 0:
            chunk = fh.read(min(256 * 1024, left))
            if not chunk:
                break
            handler.wfile.write(chunk)
            left -= len(chunk)
    return None

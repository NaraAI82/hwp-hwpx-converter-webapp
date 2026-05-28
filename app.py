from __future__ import annotations

import io
import os
import re
import zlib
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Tuple

import olefile
from flask import Flask, Response, render_template, request

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024

HWP_FALLBACK_NOTICE = (
    "HWP 바이너리 파일은 구조상 정확한 텍스트 추출이 어려울 수 있습니다.\n"
    "현재 파일은 깨진 바이너리 문자열이 감지되어 변환 결과를 제공하지 않았습니다.\n"
    "가능하면 한글 프로그램에서 ‘다른 이름으로 저장 → HWPX’로 변환한 뒤 다시 업로드해 주세요."
)

SUSPICIOUS_MARKERS = {
    "root entry",
    "bodytext",
    "docinfo",
    "fileheader",
    "scripts",
    "defaultjscript",
    "hwpsummaryinformation",
    "linkdoc",
    "ole",
    "ole10native",
    "prvtext",
    "prvimage",
    "section",
}


def normalize_text(text: str) -> str:
    text = text.replace("\r", "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def extract_xml_text(xml_bytes: bytes) -> str:
    try:
        root = ET.fromstring(xml_bytes)
    except Exception:
        return ""

    lines: List[str] = []
    try:
        for elem in root.iter():
            tag = elem.tag.split("}")[-1].lower()
            txt = (elem.text or "").strip()
            if not txt:
                continue
            if tag in {"title", "heading", "h1", "h2", "h3"}:
                lines.append(f"# {txt}")
            else:
                lines.append(txt)
    except Exception:
        return ""

    return normalize_text("\n".join(lines))


def hwpx_to_text(data: bytes) -> Tuple[str, str]:
    txt_parts: List[str] = []
    md_parts: List[str] = []

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
            section_like = [
                n
                for n in names
                if n.lower().endswith(".xml")
                and (
                    "section" in n.lower()
                    or "body" in n.lower()
                    or "contents" in n.lower()
                )
            ]
            xml_files = section_like or [n for n in names if n.lower().endswith(".xml")]

            for name in sorted(xml_files):
                try:
                    xml_bytes = zf.read(name)
                    chunk = extract_xml_text(xml_bytes)
                    if chunk:
                        txt_parts.append(chunk)
                        md_parts.append(f"## {name}\n\n{chunk}")
                except Exception:
                    continue
    except Exception:
        return "", ""

    txt = normalize_text("\n\n".join(txt_parts))
    md = normalize_text("\n\n".join(md_parts))
    return txt, md


def _is_hwp_compressed(file_header: bytes) -> bool:
    if len(file_header) < 40:
        return False
    try:
        # HWP v5 file property flags (little-endian) around offset 36
        flags = int.from_bytes(file_header[36:40], "little", signed=False)
        return bool(flags & 0x01)
    except Exception:
        return False


def _iter_body_section_streams(ole: olefile.OleFileIO) -> Iterable[List[str]]:
    for entry in ole.listdir(streams=True, storages=False):
        if len(entry) >= 2 and entry[0].lower() == "bodytext" and entry[1].lower().startswith("section"):
            yield entry


def _read_stream_safe(ole: olefile.OleFileIO, entry: List[str]) -> bytes:
    try:
        with ole.openstream(entry) as fp:
            return fp.read()
    except Exception:
        return b""


def _decompress_if_needed(data: bytes, compressed: bool) -> bytes:
    if not data:
        return b""
    if not compressed:
        return data
    try:
        # raw deflate in HWP BodyText sections
        return zlib.decompress(data, -15)
    except Exception:
        try:
            return zlib.decompress(data)
        except Exception:
            return b""


def _extract_para_text_from_section(section: bytes) -> str:
    # HWP record stream parser: extract HWPTAG_PARA_TEXT(67) payloads as UTF-16LE
    out: List[str] = []
    pos = 0
    n = len(section)

    while pos + 4 <= n:
        header = int.from_bytes(section[pos : pos + 4], "little", signed=False)
        pos += 4

        tag_id = header & 0x3FF
        size = (header >> 20) & 0xFFF

        if size == 0xFFF:
            if pos + 4 > n:
                break
            size = int.from_bytes(section[pos : pos + 4], "little", signed=False)
            pos += 4

        if size < 0 or pos + size > n:
            break

        payload = section[pos : pos + size]
        pos += size

        if tag_id == 67 and payload:
            try:
                t = payload.decode("utf-16le", errors="ignore")
                if t.strip():
                    out.append(t)
            except Exception:
                continue

    return normalize_text("\n".join(out))


def quality_check(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 20:
        return False

    lowered = t.lower()
    hard_fail_markers = {"root entry", "docinfo", "fileheader", "bodytext"}
    if any(m in lowered for m in hard_fail_markers):
        return False

    if "\x00" in t:
        return False

    marker_hits = sum(1 for m in SUSPICIOUS_MARKERS if m in lowered)
    if marker_hits >= 2:
        return False

    allowed_chars = re.findall(r"[가-힣A-Za-z0-9\s\.,;:!?\-\(\)\[\]\{\}\"'“”‘’·…/]", t)
    allowed_ratio = len(allowed_chars) / max(len(t), 1)
    disallowed_ratio = 1 - allowed_ratio

    control_chars = sum(1 for c in t if ord(c) < 32 and c not in "\n\t\r")
    control_ratio = control_chars / max(len(t), 1)

    replacement_ratio = t.count("�") / max(len(t), 1)
    garbled_symbol_ratio = len(re.findall(r"[�◻◼◆◇□■※¤�]", t)) / max(len(t), 1)

    meaningful = re.findall(r"[가-힣A-Za-z0-9]", t)
    meaningful_ratio = len(meaningful) / max(len(t), 1)
    hangul_ratio = len(re.findall(r"[가-힣]", t)) / max(len(t), 1)

    punct_count = len(re.findall(r"[.!?;:,]", t))
    if len(t) >= 200 and punct_count <= 1:
        return False

    random_runs = re.findall(r"[A-Za-z0-9_\-+=/\\|]{18,}", t)
    if len(random_runs) >= 2:
        return False

    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    normal_lines = 0
    for ln in lines:
        if len(ln) < 8:
            continue
        if re.search(r"[가-힣]{2,}", ln) and re.search(r"[A-Za-z가-힣0-9]", ln):
            normal_lines += 1

    if normal_lines < 3:
        return False
    if allowed_ratio < 0.70:
        return False
    if disallowed_ratio >= 0.30:
        return False
    if meaningful_ratio < 0.25:
        return False
    if hangul_ratio < 0.10:
        return False
    if control_ratio > 0.01:
        return False
    if replacement_ratio > 0.01:
        return False
    if garbled_symbol_ratio > 0.02:
        return False

    return True


def failure_notice_files(stem: str) -> List[Tuple[str, bytes]]:
    txt = HWP_FALLBACK_NOTICE
    md = HWP_FALLBACK_NOTICE
    return [
        (f"{stem}.txt", txt.encode("utf-8")),
        (f"{stem}.md", md.encode("utf-8")),
    ]


def hwp_to_text(data: bytes) -> Tuple[str, str]:
    text_chunks: List[str] = []

    try:
        with olefile.OleFileIO(io.BytesIO(data)) as ole:
            header = _read_stream_safe(ole, ["FileHeader"])
            compressed = _is_hwp_compressed(header)

            for entry in _iter_body_section_streams(ole):
                raw = _read_stream_safe(ole, entry)
                if not raw:
                    continue
                stream_data = _decompress_if_needed(raw, compressed)
                if not stream_data:
                    continue
                chunk = _extract_para_text_from_section(stream_data)
                if chunk:
                    text_chunks.append(chunk)
    except Exception:
        return "", ""

    merged = normalize_text("\n\n".join(text_chunks))
    if not quality_check(merged):
        return "", ""

    md = f"# HWP 변환 결과\n\n{merged}"
    return merged, md


def convert_file(filename: str, content: bytes) -> List[Tuple[str, bytes]]:
    stem = Path(filename).stem
    ext = Path(filename).suffix.lower()

    try:
        if ext == ".hwpx":
            txt, md = hwpx_to_text(content)
            if not txt and not md:
                return failure_notice_files(stem)
            return [
                (f"{stem}.txt", txt.encode("utf-8")),
                (f"{stem}.md", md.encode("utf-8")),
            ]

        if ext == ".hwp":
            txt, md = hwp_to_text(content)
            if not txt or not md:
                return failure_notice_files(stem)
            return [
                (f"{stem}.txt", txt.encode("utf-8")),
                (f"{stem}.md", md.encode("utf-8")),
            ]

        return [(f"{stem}_error.txt", f"지원하지 않는 확장자: {ext}".encode("utf-8"))]
    except Exception:
        return failure_notice_files(stem)


@app.get("/")
def index() -> str:
    return render_template("index.html")


@app.post("/convert")
def convert() -> Response:
    files = request.files.getlist("files")
    if not files:
        return render_template("index.html", error="파일을 1개 이상 업로드해 주세요."), 400

    out = io.BytesIO()
    manifest: List[str] = [f"converted_at={datetime.utcnow().isoformat()}Z"]

    try:
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in files:
                try:
                    original_name = Path(f.filename or "").name
                    if not original_name:
                        continue

                    content = f.read() or b""
                    results = convert_file(original_name, content)
                    for out_name, out_bytes in results:
                        zf.writestr(out_name, out_bytes)
                        manifest.append(f"{original_name} -> {out_name}")
                except Exception:
                    fallback_name = Path(getattr(f, "filename", "unknown") or "unknown").stem
                    for out_name, out_bytes in failure_notice_files(fallback_name):
                        zf.writestr(out_name, out_bytes)
                        manifest.append(f"{fallback_name} -> {out_name}(fallback)")
                finally:
                    content = b""

            zf.writestr("manifest.txt", "\n".join(manifest).encode("utf-8"))
    except Exception:
        return render_template("index.html", error="ZIP 생성 중 오류가 발생했습니다. 다시 시도해 주세요."), 500

    payload = out.getvalue()
    out.close()

    download_name = f"converted_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.zip"
    headers = {
        "Content-Type": "application/zip",
        "Content-Disposition": f'attachment; filename="{download_name}"; filename*=UTF-8\'\'{download_name}',
        "Cache-Control": "no-store",
    }
    response = Response(payload, headers=headers)
    payload = b""
    return response


@app.errorhandler(413)
def too_large(_e):
    return render_template("index.html", error="업로드 용량이 너무 큽니다. 100MB 이하로 올려주세요."), 413


@app.errorhandler(Exception)
def handle_exception(_e):
    return render_template("index.html", error="처리 중 오류가 발생했습니다. 파일을 다시 확인해 주세요."), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)

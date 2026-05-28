from __future__ import annotations

import io
import os
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

from flask import Flask, Response, render_template, request

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024


def normalize_text(text: str) -> str:
    text = text.replace("\r", "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def extract_xml_text(xml_bytes: bytes) -> str:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return ""

    lines: List[str] = []
    for elem in root.iter():
        tag = elem.tag.split("}")[-1].lower()
        txt = (elem.text or "").strip()
        if not txt:
            continue

        if tag in {"title", "heading", "h1", "h2", "h3"}:
            lines.append(f"# {txt}")
        else:
            lines.append(txt)

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
                except KeyError:
                    continue
                chunk = extract_xml_text(xml_bytes)
                if chunk:
                    txt_parts.append(chunk)
                    md_parts.append(f"## {name}\n\n{chunk}")
    except zipfile.BadZipFile:
        return "", ""

    txt = normalize_text("\n\n".join(txt_parts))
    md = normalize_text("\n\n".join(md_parts))
    return txt, md


def hwp_to_text_fallback(data: bytes) -> Tuple[str, str]:
    decoded_candidates: List[str] = []

    for enc in ("utf-8", "cp949", "euc-kr"):
        try:
            s = data.decode(enc, errors="ignore")
        except Exception:
            continue
        lines = []
        for line in s.splitlines():
            line = line.strip()
            if not line:
                continue
            if re.search(r"[가-힣A-Za-z0-9]", line) and len(line) >= 2:
                lines.append(line)
        if lines:
            decoded_candidates.append("\n".join(lines))

    if not decoded_candidates:
        notice = (
            "[변환 안내]\n"
            "이 HWP 파일은 내장 변환기로 본문 추출에 실패했습니다.\n"
            "권장: HWPX/PDF로 변환 후 다시 업로드하거나, hwp5txt 연동 사용"
        )
        return notice, f"# 변환 안내\n\n{notice}"

    best = normalize_text(max(decoded_candidates, key=len))
    notice = "[주의] HWP 바이너리 추출(휴리스틱) 결과라 문단/표가 깨질 수 있습니다.\n\n"
    txt = notice + best
    md = "# HWP 변환 결과(휴리스틱)\n\n" + notice + best
    return txt, md


def convert_file(filename: str, content: bytes) -> List[Tuple[str, bytes]]:
    stem = Path(filename).stem
    ext = Path(filename).suffix.lower()

    if ext == ".hwpx":
        txt, md = hwpx_to_text(content)
        if not txt and not md:
            msg = "HWPX 파싱 실패: 파일 손상 또는 비표준 구조"
            return [
                (f"{stem}.txt", msg.encode("utf-8")),
                (f"{stem}.md", f"# 변환 실패\n\n{msg}".encode("utf-8")),
            ]
        return [
            (f"{stem}.txt", txt.encode("utf-8")),
            (f"{stem}.md", md.encode("utf-8")),
        ]

    if ext == ".hwp":
        txt, md = hwp_to_text_fallback(content)
        return [
            (f"{stem}.txt", txt.encode("utf-8")),
            (f"{stem}.md", md.encode("utf-8")),
        ]

    msg = f"지원하지 않는 확장자: {ext}"
    return [(f"{stem}_error.txt", msg.encode("utf-8"))]


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

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            original_name = Path(f.filename or "").name
            if not original_name:
                continue

            ext = Path(original_name).suffix.lower()
            if ext not in {".hwp", ".hwpx"}:
                zf.writestr(
                    f"{Path(original_name).stem}_error.txt",
                    f"지원하지 않는 확장자: {ext}".encode("utf-8"),
                )
                manifest.append(f"{original_name} -> unsupported")
                continue

            content = f.read()
            results = convert_file(original_name, content)

            for out_name, out_bytes in results:
                zf.writestr(out_name, out_bytes)
                manifest.append(f"{original_name} -> {out_name}")

            content = b""

        zf.writestr("manifest.txt", "\n".join(manifest).encode("utf-8"))

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

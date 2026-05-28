import io
import zipfile
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import HWP_FALLBACK_NOTICE, app, quality_check


def run() -> None:
    broken = Path("tests/fixtures/broken_hwp_text_sample.txt").read_text(encoding="utf-8")

    assert quality_check(broken) is False, "broken marker text must fail"
    assert quality_check("Root Entry\n정상처럼 보여도 실패") is False
    assert quality_check("DocInfo\n정상처럼 보여도 실패") is False
    assert quality_check("FileHeader\n정상처럼 보여도 실패") is False
    assert quality_check("BodyText\n정상처럼 보여도 실패") is False

    client = app.test_client()
    payload = (
        b"\x00\x01Root Entry\nBodyText\nDocInfo\nFileHeader\n"
        b"DefaultJScript\nHwpSummaryInformation\nSection1\n"
        b"AAAAABBBBBCCCCCDDDDDEEEEE1111122222"
    )
    resp = client.post(
        "/convert",
        data={"files": (io.BytesIO(payload), "broken.hwp")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200

    zf = zipfile.ZipFile(io.BytesIO(resp.data))
    txt = zf.read("broken.txt").decode("utf-8")
    md = zf.read("broken.md").decode("utf-8")

    assert txt == HWP_FALLBACK_NOTICE
    assert md.startswith("# HWP 지원 제한 안내")
    assert HWP_FALLBACK_NOTICE in md

    forbidden = ["Root Entry", "BodyText", "DocInfo", "FileHeader"]
    for token in forbidden:
        assert token not in txt
        assert token not in md

    print("ok")


if __name__ == "__main__":
    run()

from __future__ import annotations

import sys
import types
from pathlib import Path

from elan_pretty.render import pdf as pdf_render


def test_auto_pdf_backend_falls_back_when_weasyprint_native_libs_fail(
    monkeypatch,
    tmp_path: Path,
) -> None:
    html_path = tmp_path / "example.html"
    pdf_path = tmp_path / "example.pdf"
    fake_browser = tmp_path / "chromium"
    html_path.write_text("<!doctype html><p>test</p>", encoding="utf-8")
    fake_browser.write_text("#!/bin/sh\n", encoding="utf-8")

    class BrokenHTML:
        def __init__(self, filename: str) -> None:
            self.filename = filename

        def write_pdf(self, target: str) -> None:
            raise OSError("cannot load library 'libpango-1.0-0'")

    fake_weasyprint = types.SimpleNamespace(HTML=BrokenHTML)
    monkeypatch.setitem(sys.modules, "weasyprint", fake_weasyprint)
    monkeypatch.setattr(pdf_render, "_chromium_candidates", lambda: [str(fake_browser)])

    def fake_run(command: list[str], check: bool) -> None:
        pdf_path.write_bytes(b"%PDF-1.4\n")

    monkeypatch.setattr(pdf_render.subprocess, "run", fake_run)

    assert pdf_render.render_pdf(html_path, pdf_path, backend="auto") == pdf_path
    assert pdf_path.read_bytes().startswith(b"%PDF")

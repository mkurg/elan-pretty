from __future__ import annotations

import sys
import types
from pathlib import Path

from elan_pretty.render import pdf as pdf_render


def test_auto_pdf_backend_prefers_chromium(
    monkeypatch,
    tmp_path: Path,
) -> None:
    html_path = tmp_path / "example.html"
    pdf_path = tmp_path / "example.pdf"
    fake_browser = tmp_path / "chromium"
    html_path.write_text("<!doctype html><p>test</p>", encoding="utf-8")
    fake_browser.write_text("#!/bin/sh\n", encoding="utf-8")

    class WeasyHTML:
        def __init__(self, filename: str) -> None:
            self.filename = filename

        def write_pdf(self, target: str) -> None:
            Path(target).write_bytes(b"WEASY\n")

    fake_weasyprint = types.SimpleNamespace(HTML=WeasyHTML)
    monkeypatch.setitem(sys.modules, "weasyprint", fake_weasyprint)
    monkeypatch.setattr(pdf_render, "_chromium_candidates", lambda: [str(fake_browser)])

    def fake_run(command: list[str], check: bool, **kwargs: object) -> None:
        pdf_path.write_bytes(b"%PDF-1.4\n")

    monkeypatch.setattr(pdf_render.subprocess, "run", fake_run)

    assert pdf_render.render_pdf(html_path, pdf_path, backend="auto") == pdf_path
    assert pdf_path.read_bytes().startswith(b"%PDF")


def test_chromium_backend_tries_next_browser_candidate(
    monkeypatch,
    tmp_path: Path,
) -> None:
    html_path = tmp_path / "example.html"
    pdf_path = tmp_path / "example.pdf"
    html_path.write_text("<!doctype html><p>test</p>", encoding="utf-8")
    monkeypatch.setattr(pdf_render, "_chromium_candidates", lambda: ["bad", "good"])

    def fake_print(browser: str, html: Path, pdf: Path) -> None:
        if browser == "bad":
            raise OSError("bad browser")
        pdf.write_bytes(b"%PDF-1.4\n")

    monkeypatch.setattr(pdf_render, "_print_with_chromium", fake_print)

    assert pdf_render.render_pdf(html_path, pdf_path, backend="chromium") == pdf_path
    assert pdf_path.read_bytes().startswith(b"%PDF")


def test_auto_pdf_backend_falls_back_to_weasyprint_without_chromium(
    monkeypatch,
    tmp_path: Path,
) -> None:
    html_path = tmp_path / "example.html"
    pdf_path = tmp_path / "example.pdf"
    html_path.write_text("<!doctype html><p>test</p>", encoding="utf-8")

    class WeasyHTML:
        def __init__(self, filename: str) -> None:
            self.filename = filename

        def write_pdf(self, target: str) -> None:
            Path(target).write_bytes(b"WEASY\n")

    fake_weasyprint = types.SimpleNamespace(HTML=WeasyHTML)
    monkeypatch.setitem(sys.modules, "weasyprint", fake_weasyprint)
    monkeypatch.setattr(pdf_render, "_chromium_candidates", lambda: [])

    assert pdf_render.render_pdf(html_path, pdf_path, backend="auto") == pdf_path
    assert pdf_path.read_bytes() == b"WEASY\n"

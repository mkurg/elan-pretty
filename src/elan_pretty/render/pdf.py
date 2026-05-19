from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def render_pdf(html_path: Path, pdf_path: Path, backend: str = "auto") -> Path:
    """Print HTML to PDF with WeasyPrint or a headless Chromium-compatible browser."""

    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    if backend in {"auto", "weasyprint"}:
        try:
            from weasyprint import HTML
        except ImportError:
            if backend == "weasyprint":
                msg = "WeasyPrint is not installed. Install with: pip install 'elan-pretty[pdf]'"
                raise RuntimeError(msg) from None
        else:
            HTML(filename=str(html_path)).write_pdf(str(pdf_path))
            return pdf_path

    if backend in {"auto", "chromium"}:
        last_error: Exception | None = None
        for browser in _chromium_candidates():
            try:
                subprocess.run(
                    [
                        browser,
                        "--headless",
                        "--disable-gpu",
                        "--no-pdf-header-footer",
                        f"--print-to-pdf={pdf_path}",
                        html_path.resolve().as_uri(),
                    ],
                    check=True,
                )
            except (OSError, subprocess.CalledProcessError) as exc:
                last_error = exc
                if backend == "chromium":
                    raise
                continue
            return pdf_path
        if backend == "chromium":
            msg = "No Chromium-compatible browser was found on PATH."
            if last_error is not None:
                msg = f"{msg} Last attempted browser failed: {last_error}."
            raise RuntimeError(msg)

    msg = "Could not render PDF: install WeasyPrint or make Chromium/Chrome available."
    if "last_error" in locals() and last_error is not None:
        msg = f"{msg} Last attempted browser failed: {last_error}."
    raise RuntimeError(msg)


def _chromium_candidates() -> list[str]:
    paths = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    candidates = [
        "chromium",
        "chromium-browser",
        "google-chrome",
        "google-chrome-stable",
        "chrome",
        "msedge",
    ]
    for executable in candidates:
        path = shutil.which(executable)
        if path and path not in paths:
            paths.append(path)
    return paths

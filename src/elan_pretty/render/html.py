from __future__ import annotations

import re
import shutil
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

from elan_pretty.config import ProjectConfig
from elan_pretty.models import InterlinearDocument
from elan_pretty.utils import format_ms, safe_slug


ABBREVIATION_TOKEN = re.compile(r"(\S+)")
BOUNDARY_SPLIT = re.compile(r"([-=])")
GLOSS_PUNCTUATION = ".,;:()[]{}<>"


class HTMLRenderer:
    def __init__(self) -> None:
        self.package_dir = Path(__file__).resolve().parent
        self.template_dir = self.package_dir / "templates"
        self.static_dir = self.package_dir / "static"
        self.environment = Environment(
            loader=FileSystemLoader(str(self.template_dir)),
            autoescape=select_autoescape(["html", "xml", "j2"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self.environment.filters["format_ms"] = format_ms
        self.environment.filters["format_gloss"] = self._format_gloss

    def write(
        self,
        document: InterlinearDocument,
        output_dir: Path,
        config: ProjectConfig,
        *,
        stem: str | None = None,
    ) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        assets_dir = output_dir / "assets"
        assets_dir.mkdir(exist_ok=True)

        css_source = self.static_dir / "elan-pretty.css"
        css_target = assets_dir / "elan-pretty.css"
        shutil.copyfile(css_source, css_target)

        filename = f"{safe_slug(stem or document.id)}.html"
        output_path = output_dir / filename
        template = self.environment.get_template("document.html.j2")
        html = template.render(
            document=document,
            config=config,
            css_href="assets/elan-pretty.css",
            primary_media=document.media[0] if document.media else None,
        )
        output_path.write_text(html, encoding="utf-8")
        return output_path

    def _format_gloss(self, value: str | None, abbreviations: dict[str, str]) -> Markup:
        if not value:
            return Markup("")

        rendered: list[str] = []
        for part in ABBREVIATION_TOKEN.split(value):
            if not part:
                continue
            rendered.append(self._format_gloss_token(part, abbreviations))
        return Markup("".join(rendered))

    def _format_gloss_token(self, token: str, abbreviations: dict[str, str]) -> str:
        rendered: list[str] = []
        for piece in BOUNDARY_SPLIT.split(token):
            if not piece:
                continue
            if piece in {"-", "="}:
                rendered.append(str(escape(piece)))
                continue

            lookup_key = piece.strip(GLOSS_PUNCTUATION)
            if self._is_abbreviation(lookup_key):
                title = abbreviations.get(lookup_key)
                title_attr = f' title="{escape(title)}"' if title else ""
                rendered.append(
                    f'<span class="gloss-abbr"{title_attr}>{escape(piece)}</span>'
                )
            else:
                rendered.append(str(escape(piece)))
        return "".join(rendered)

    def _is_abbreviation(self, token: str) -> bool:
        letters = [char for char in token if char.isalpha()]
        if not letters:
            return False
        return all(not char.islower() for char in letters)

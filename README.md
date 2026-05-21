# ELAN Pretty

`elan-pretty` renders ELAN `.eaf` files directly into clean JSON, semantic HTML,
and optional PDF-ready output for interlinear glossed text.

It does not use ELAN export formats, FLEx, or hardcoded tier names. Tier roles
are configured in YAML, then the parser resolves ELAN parent-child annotation
links and timestamps from the original XML.

## Install

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
```

For WeasyPrint PDF support:

```bash
pip install -e '.[pdf]'
```

Chromium/Chrome print-to-PDF also works when a compatible browser is available.

## Quick Start

```bash
python main.py pear_harikumarigurung_05082025_post.eaf output/ \
  --config config/sample.tiers.yaml \
  --inspect-tiers
```

Outputs:

- `output/pear_harikumarigurung_05082025_post.json`
- `output/pear_harikumarigurung_05082025_post.html`
- `output/assets/elan-pretty.css`

Add `--pdf` to write a PDF:

```bash
python main.py pear_harikumarigurung_05082025_post.eaf output/ \
  --config config/sample.tiers.yaml \
  --pdf
```

## Tier Mapping

Tier mapping is always configurable:

```yaml
tiers:
  reference: ref@w4r
  phrase: po@w4r
  words: wd@w4r
  morphemes: mb@w4r
  gloss: ge@w4r
  translation: ft@w4r
```

For multi-speaker files, provide parallel tier lists. ELAN Pretty will merge the
speaker bundles by timestamp and color-code speakers in HTML/PDF:

```yaml
tiers:
  reference: [ref@A, ref@B]
  phrase: [tx@A, tx@B]
  words: [wd@A, wd@B]
  morphemes: [mb@A, mb@B]
  gloss: [ge@A, ge@B]
  translation: [ft@A, ft@B]
```

The `reference` tier is optional but useful when timestamps live on a parent
tier. The normalizer groups annotations by their nearest time-aligned ancestor,
so sibling tiers such as source text and free translation still align correctly.

Extra metadata tiers can be included:

```yaml
tiers:
  metadata:
    speaker: speaker@A
    comment: cmt@A
```

## Pipeline

```text
.eaf
  -> lxml ELAN parser
  -> raw annotation graph
  -> pydantic normalized model
  -> JSON export
  -> Jinja2 HTML renderer
  -> WeasyPrint or Chromium PDF
```

The normalized segment shape is:

```json
{
  "id": "segment_0001",
  "start_ms": 1200,
  "end_ms": 4600,
  "phrase": "...",
  "words": [
    {
      "surface": "...",
      "morphemes": [
        {
          "form": "...",
          "gloss": "..."
        }
      ]
    }
  ],
  "translation": "..."
}
```

## Rendering

The HTML uses:

- word-level alignment with Leipzig-style word-internal morpheme/gloss strings
- merged multi-speaker timelines with speaker color-coding
- flexible wrapping for long examples and long glosses
- `dir="auto"` and Unicode bidi-aware direction inference for RTL scripts
- small caps for gloss abbreviations
- print CSS with page margins and break control
- light, dark, and system themes
- optional audio cue buttons
- browser-side search/filtering

## CLI

```bash
python main.py INPUT OUTPUT_DIR [options]
```

Useful options:

- `--config config/sample.tiers.yaml`
- `--auto-detect-tiers`
- `--suggest-tiers`
- `--mapping-profile gurung-w4r`
- `--save-mapping "Project Name"`
- `--pdf`
- `--pdf-backend auto|weasyprint|chromium`
- `--audio-links`
- `--theme light|dark|system`
- `--title "My Text"`
- `--inspect-tiers`

`INPUT` may be one `.eaf` file or a directory. Directories are searched
recursively for `.eaf` files.

## GitHub Pages Publishing

Render a public GitHub Pages publication folder:

```bash
python main.py pear_harikumarigurung_05082025_post.eaf published \
  --config config/sample.tiers.yaml \
  --github-pages \
  --pdf
```

This writes:

- `published/index.html`
- `published/<document-slug>/index.html`
- `published/<document-slug>/<document-slug>.json`
- `published/<document-slug>/<document-slug>.pdf`

If the repository remote is on GitHub, the CLI prints the inferred public Pages
URL. To publish and push in one command:

```bash
python main.py input.eaf published \
  --config tiers.yaml \
  --github-pages \
  --pdf \
  --commit-and-push
```

GitHub Pages should be configured to deploy from branch `main`, folder `/root`.

## Tier Detection and Saved Mappings

For a quick inventory plus a suggested role mapping:

```bash
python main.py input.eaf output --suggest-tiers --auto-detect-tiers
```

Saved mapping profiles live in `mappings/*.yaml` and can be reused:

```bash
python main.py input.eaf published \
  --mapping-profile gurung-w4r \
  --github-pages \
  --pdf
```

The detector is heuristic. It looks at tier names, ELAN hierarchy, annotation
counts, linguistic type constraints, and value shapes. A saved profile wins when
its configured tier IDs match the uploaded file well; otherwise the system falls
back to detection.

## Telegram Bot Backend

Install the optional bot dependency:

```bash
pip install -e '.[bot,pdf]'
```

Run locally with long polling:

```bash
export TELEGRAM_BOT_TOKEN=123456:replace-me
export ELAN_PRETTY_AUTO_GIT_PUSH=false
python -m elan_pretty.bot.telegram_bot
```

The bot flow is:

1. user sends an `.eaf` file
2. bot suggests or reuses a tier mapping
3. user taps buttons to render, save the mapping, edit roles, or choose a saved mapping
4. bot renders HTML/JSON/PDF and can push the GitHub Pages output
5. `/publications` lets the bot remove items from the public web page

See [docs/aws_ec2_telegram.md](docs/aws_ec2_telegram.md) for EC2 deployment.

## Robustness Notes

The parser handles:

- `TIME_ORDER`
- `ALIGNABLE_ANNOTATION`
- `REF_ANNOTATION`
- `ANNOTATION_REF`
- `PREVIOUS_ANNOTATION`
- tier hierarchy via `PARENT_REF`
- linguistic type metadata
- missing time slots, missing parents, orphan refs, empty annotations, and
  incomplete configured tiers

Warnings are preserved in JSON and shown in the rendered HTML.

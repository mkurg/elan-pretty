# Architecture

`elan-pretty` treats ELAN XML as a source format, not as a rendering model. The
core boundary is a normalized pydantic representation that can be serialized to
JSON and then rendered to HTML or PDF.

```text
.eaf
  -> EAF parser
  -> raw tier and annotation graph
  -> normalizer
  -> pydantic JSON document
  -> HTML renderer
  -> print/PDF backend
```

## Modules

- `elan_pretty.parser` reads `.eaf` XML with `lxml`, preserving ELAN annotation
  ids, tier hierarchy, parent references, time slots, media descriptors, and
  document order.
- `elan_pretty.raw` contains lightweight parsed ELAN structures. These are close
  to the XML and intentionally not exposed as the public output format.
- `elan_pretty.normalize` resolves annotation references into linguistic
  examples, groups annotations by their nearest time-aligned ancestor, and
  converts arbitrary configured tiers into `Segment`, `Word`, and `Morpheme`
  pydantic models.
- `elan_pretty.models` defines the stable JSON model used by renderers and
  downstream corpus tooling.
- `elan_pretty.render.html` renders semantic HTML with Jinja2 and static CSS.
- `elan_pretty.render.pdf` optionally prints HTML through WeasyPrint or
  Chromium.
- `elan_pretty.cli` wires the pipeline into a Typer command.

## Internal Model

The normalized model is deliberately small:

```json
{
  "id": "segment_0001",
  "start_ms": 1200,
  "end_ms": 4600,
  "phrase": "tsa mi kri",
  "words": [
    {
      "surface": "tsa",
      "morphemes": [
        { "form": "tsa", "gloss": "DIST" }
      ]
    }
  ],
  "translation": "that man"
}
```

The full model also keeps source annotation ids, media descriptors, tier
metadata, inferred text direction, per-segment warnings, and document-level
warnings. This makes the JSON suitable both for rendering and for diagnosing
annotation quality problems.

## ELAN Alignment Strategy

ELAN tiers often place timestamps on a reference tier while text, words,
morphemes, glosses, and translations live on symbolic child tiers. The
normalizer therefore resolves each annotation to its nearest alignable ancestor
and uses that annotation as the segment anchor. Sibling tiers such as phrase and
free translation can then be joined correctly even when neither directly points
to the other.

Within tiers that use `PREVIOUS_ANNOTATION`, document order alone is not enough.
The normalizer reconstructs chains from previous links and falls back to XML
order for malformed or partial chains.

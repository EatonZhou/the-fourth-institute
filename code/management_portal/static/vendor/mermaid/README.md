# Mermaid browser bundle

Vendored Mermaid `11.17.2` ESM browser bundle for the Shared Preview Markdown renderer.

- Upstream: https://github.com/mermaid-js/mermaid
- Package: https://www.npmjs.com/package/mermaid/v/11.17.2
- License: MIT; see `LICENSE`.

`mermaid.esm.min.mjs` and `chunks/mermaid.esm.min/*.mjs` are copied from the
published package. The ESM build is required because Portal's Monaco editor owns
the page-level AMD loader; loading Mermaid's classic bundle through that loader
causes anonymous `define()` collisions.

The bundle is loaded lazily only when a Markdown document contains a fenced
`mermaid` block. Update the ESM entry, all of its chunks, and the license together
when changing versions.

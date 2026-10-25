# Third-party notices

TFI Portal includes the browser libraries listed below. Their original
copyright notices and license terms remain applicable. The repository's Apache-2.0
license does not replace these third-party licenses.

Paths in the inventory are relative to `management_portal/static/`. Source links
identify the exact official npm releases used to verify the distributed files;
each release's metadata includes its source repository and archive integrity.
Original license files are retained at the linked paths. Upstream notices and
licenses embedded by a bundler are also reproduced below.

## Optional Article runtime downloads

The installer can download [TinyTeX 2026.10](https://github.com/rstudio/tinytex-releases/releases/tag/v2026.10)
and [TexLab 5.26.0](https://github.com/latex-lsp/texlab/releases/tag/v5.26.0)
into the installation's private tool directory. Their binaries are not bundled in this source tree.
TexLab is distributed under [GPL-3.0](https://github.com/latex-lsp/texlab/blob/v5.26.0/LICENSE).
TeX Live and its packages retain their individual licenses; see the
[TeX Live licensing conditions](https://tug.org/texlive/copying.html) and the installed package metadata.
The installer verifies pinned distribution archives and records the subsequently resolved TeX packages.

## Browser library inventory

| Component | Version | Location | Official release | License | Original terms |
| --- | --- | --- | --- | --- | --- |
| D3 | 7.9.0 | `vendor/d3; projection_libraries/d3/7.9.0` | [d3@7.9.0](https://registry.npmjs.org/d3/7.9.0) | ISC | [LICENSE](management_portal/static/vendor/d3/LICENSE) / [LICENSE](management_portal/static/projection_libraries/d3/7.9.0/LICENSE) |
| KaTeX code | 0.17.0 | `vendor/katex` | [katex@0.17.0](https://registry.npmjs.org/katex/0.17.0) | MIT | [LICENSE](management_portal/static/vendor/katex/LICENSE) |
| KaTeX fonts | supplied with 0.17.0 | `vendor/katex/fonts` | [katex@0.17.0](https://registry.npmjs.org/katex/0.17.0) | SIL OFL-1.1 | [copyright and reserved names](management_portal/static/vendor/katex/fonts/NOTICE.md); [OFL.txt](management_portal/static/vendor/katex/fonts/OFL.txt) |
| Mermaid | 11.17.2 | `vendor/mermaid` | [mermaid@11.17.2](https://registry.npmjs.org/mermaid/11.17.2) | MIT | [LICENSE](management_portal/static/vendor/mermaid/LICENSE) |
| Monaco Editor | 0.56.0 | `vendor/monaco` | [monaco-editor@0.56.0](https://registry.npmjs.org/monaco-editor/0.56.0) | MIT | [LICENSE](management_portal/static/vendor/monaco/LICENSE); [upstream notice file](management_portal/static/vendor/monaco/ThirdPartyNotices.txt); [embedded notices](#monaco-upstream-notices) |
| Cytoscape.js | 3.33.1 | `projection_libraries/cytoscape/3.33.1` | [cytoscape@3.33.1](https://registry.npmjs.org/cytoscape/3.33.1) | MIT | [LICENSE](management_portal/static/projection_libraries/cytoscape/3.33.1/LICENSE) |
| dagre | 1.1.5 | `projection_libraries/dagre/1.1.5` | [@dagrejs/dagre@1.1.5](https://registry.npmjs.org/@dagrejs%2Fdagre/1.1.5) | MIT | [LICENSE](management_portal/static/projection_libraries/dagre/1.1.5/LICENSE) |
| graphlib (bundled with dagre) | 2.2.4 | `projection_libraries/dagre/1.1.5` | [@dagrejs/graphlib@2.2.4](https://registry.npmjs.org/@dagrejs%2Fgraphlib/2.2.4) | MIT | [LICENSE](management_portal/static/projection_libraries/dagre/1.1.5/graphlib-LICENSE) |
| DOMPurify | 3.4.17 | `projection_libraries/dompurify/3.4.17` | [dompurify@3.4.17](https://registry.npmjs.org/dompurify/3.4.17) | Apache-2.0 (selected from MPL-2.0 OR Apache-2.0) | [LICENSE](management_portal/static/projection_libraries/dompurify/3.4.17/LICENSE) |
| marked | 18.1.1 | `projection_libraries/marked/18.1.1` | [marked@18.1.1](https://registry.npmjs.org/marked/18.1.1) | MIT | [LICENSE](management_portal/static/projection_libraries/marked/18.1.1/LICENSE) |
| pdfjs-dist | 3.11.174 | `vendor/pdfjs` | [pdfjs-dist@3.11.174](https://registry.npmjs.org/pdfjs-dist/3.11.174) | Apache-2.0 | [LICENSE](management_portal/static/vendor/pdfjs/LICENSE) |
| @xterm/xterm | 6.0.0 | `vendor/xterm` | [@xterm/xterm@6.0.0](https://registry.npmjs.org/@xterm/xterm/6.0.0) | MIT | [LICENSE](management_portal/static/vendor/xterm/LICENSE) |
| @xterm/addon-fit | 0.11.0 | `vendor/xterm` | [@xterm/addon-fit@0.11.0](https://registry.npmjs.org/@xterm/addon-fit/0.11.0) | MIT | [LICENSE](management_portal/static/vendor/xterm/LICENSE-addon-fit) |
| Adobe CMaps | supplied with pdfjs-dist 3.11.174 | `vendor/pdfjs/cmaps` | [pdfjs-dist@3.11.174](https://registry.npmjs.org/pdfjs-dist/3.11.174) | BSD-3-Clause | [LICENSE](management_portal/static/vendor/pdfjs/cmaps/LICENSE) |

The CMap license is separate from PDF.js's Apache-2.0 license. The PDF.js
`standard_fonts` directory is not distributed here. The xterm CSS retains its
original inline license and historical attribution.

Monaco's `vs/loader.js` retains an older version in its header; its bytes match
the loader shipped in the official **0.56.0** archive. The local
`vs/assets/ts.worker-BWKtMYOk.js` and `vs/language/typescript/ts.worker.js` differ
from that archive; they are local adaptations of its TypeScript workers. The
original Monaco license and upstream third-party notices below apply to the
upstream portions. Other Monaco files match the official archive. The existing
worker adaptations are recorded in [MODIFICATIONS.md](management_portal/static/vendor/monaco/MODIFICATIONS.md).

`projection_libraries/portal-graph/1.0.0` is first-party code and uses the
repository's [Apache-2.0 license](LICENSE); it is not a separate third-party
package. The projection library registry also retains its upstream package
metadata, original licenses and archive integrity values.

## Interface assets

The images in `management_portal/static/assets` have a separate
[provenance record](management_portal/static/assets/ATTRIBUTIONS.md), including
file hashes and their introduction in Git. The available repository history and
file metadata do not establish their original authors, external sources or
separate permission records. Those provenance details remain unverified; the
repository's Apache-2.0 license does not establish rights to unknown-source
material. No new license has been assigned to those images in this audit.

## Components bundled with Mermaid

These exact package versions are identified by the official Mermaid 11.17.2
source maps and retained bundle source annotations. This is an inventory of
bundled code, not a substitution of the version ranges in `package.json`.
Mermaid's original inline license comments remain in its files. For DOMPurify,
Apache-2.0 is the selected option; upstream dual-license material is preserved.

| Package | Version | Official release | License | Original notice |
| --- | --- | --- | --- | --- |
| @braintree/sanitize-url | 7.1.2 | [release](https://registry.npmjs.org/@braintree%2Fsanitize-url/7.1.2) | MIT | [LICENSE](#license-0984740e0c3d725c) |
| @iconify/utils | 3.0.2 | [release](https://registry.npmjs.org/@iconify%2Futils/3.0.2) | MIT | [license.txt](#license-a86793ac023162ce) |
| @upsetjs/venn.js | 2.0.0 | [release](https://registry.npmjs.org/@upsetjs%2Fvenn.js/2.0.0) | MIT | [LICENSE](#license-6a3508febf2cfccf) |
| cose-base | 1.0.3 | [release](https://registry.npmjs.org/cose-base/1.0.3) | MIT | [LICENSE](#license-5fb3cf4a14c3c5af) |
| cose-base | 2.2.0 | [release](https://registry.npmjs.org/cose-base/2.2.0) | MIT | [LICENSE](#license-5fb3cf4a14c3c5af) |
| cytoscape | 3.34.0 | [release](https://registry.npmjs.org/cytoscape/3.34.0) | MIT | [LICENSE](#license-eb319c6e6f233607) |
| cytoscape-cose-bilkent | 4.1.0 | [release](https://registry.npmjs.org/cytoscape-cose-bilkent/4.1.0) | MIT | [LICENSE](#license-440fc58a56a12814) |
| cytoscape-fcose | 2.2.0 | [release](https://registry.npmjs.org/cytoscape-fcose/2.2.0) | MIT | [LICENSE](#license-2837634f40394921) |
| d3-array | 2.12.1 | [release](https://registry.npmjs.org/d3-array/2.12.1) | BSD-3-Clause | [LICENSE](#license-0747bebeb06b1095) |
| d3-array | 3.2.4 | [release](https://registry.npmjs.org/d3-array/3.2.4) | ISC | [LICENSE](#license-3e6849627f74ff73) |
| d3-axis | 3.0.0 | [release](https://registry.npmjs.org/d3-axis/3.0.0) | ISC | [LICENSE](#license-e008c5e25a6be382) |
| d3-brush | 3.0.0 | [release](https://registry.npmjs.org/d3-brush/3.0.0) | ISC | [LICENSE](#license-e008c5e25a6be382) |
| d3-color | 3.1.0 | [release](https://registry.npmjs.org/d3-color/3.1.0) | ISC | [LICENSE](#license-faa682e3e430941f) |
| d3-dispatch | 3.0.1 | [release](https://registry.npmjs.org/d3-dispatch/3.0.1) | ISC | [LICENSE](#license-e008c5e25a6be382) |
| d3-ease | 3.0.1 | [release](https://registry.npmjs.org/d3-ease/3.0.1) | BSD-3-Clause | [LICENSE](#license-b989d9085148d21f) |
| d3-format | 3.1.0 | [release](https://registry.npmjs.org/d3-format/3.1.0) | ISC | [LICENSE](#license-e008c5e25a6be382) |
| d3-hierarchy | 3.1.2 | [release](https://registry.npmjs.org/d3-hierarchy/3.1.2) | ISC | [LICENSE](#license-e008c5e25a6be382) |
| d3-interpolate | 3.0.1 | [release](https://registry.npmjs.org/d3-interpolate/3.0.1) | ISC | [LICENSE](#license-e008c5e25a6be382) |
| d3-path | 1.0.9 | [release](https://registry.npmjs.org/d3-path/1.0.9) | BSD-3-Clause | [LICENSE](#license-b8265327ab678f55) |
| d3-path | 3.1.0 | [release](https://registry.npmjs.org/d3-path/3.1.0) | ISC | [LICENSE](#license-a09b0d6d76e768fc) |
| d3-sankey | 0.12.3 | [release](https://registry.npmjs.org/d3-sankey/0.12.3) | BSD-3-Clause | [LICENSE](#license-2bf785e778d67a4f) |
| d3-scale | 4.0.2 | [release](https://registry.npmjs.org/d3-scale/4.0.2) | ISC | [LICENSE](#license-e008c5e25a6be382) |
| d3-scale-chromatic | 3.1.0 | [release](https://registry.npmjs.org/d3-scale-chromatic/3.1.0) | ISC | [LICENSE](#license-0986d93944902cca) |
| d3-selection | 3.0.0 | [release](https://registry.npmjs.org/d3-selection/3.0.0) | ISC | [LICENSE](#license-e008c5e25a6be382) |
| d3-shape | 1.3.7 | [release](https://registry.npmjs.org/d3-shape/1.3.7) | BSD-3-Clause | [LICENSE](#license-7eea8533ea92bd8c) |
| d3-shape | 3.2.0 | [release](https://registry.npmjs.org/d3-shape/3.2.0) | ISC | [LICENSE](#license-faa682e3e430941f) |
| d3-time | 3.1.0 | [release](https://registry.npmjs.org/d3-time/3.1.0) | ISC | [LICENSE](#license-faa682e3e430941f) |
| d3-time-format | 4.1.0 | [release](https://registry.npmjs.org/d3-time-format/4.1.0) | ISC | [LICENSE](#license-e008c5e25a6be382) |
| d3-timer | 3.0.1 | [release](https://registry.npmjs.org/d3-timer/3.0.1) | ISC | [LICENSE](#license-e008c5e25a6be382) |
| d3-transition | 3.0.1 | [release](https://registry.npmjs.org/d3-transition/3.0.1) | ISC | [LICENSE](#license-e008c5e25a6be382) |
| d3-zoom | 3.0.0 | [release](https://registry.npmjs.org/d3-zoom/3.0.0) | ISC | [LICENSE](#license-e008c5e25a6be382) |
| dagre-d3-es | 7.0.14 | [release](https://registry.npmjs.org/dagre-d3-es/7.0.14) | MIT | [LICENSE.md](#license-d01eb84ccce437db) |
| dayjs | 1.11.21 | [release](https://registry.npmjs.org/dayjs/1.11.21) | MIT | [LICENSE](#license-5faab7526d055651) |
| dompurify | 3.4.12 | [release](https://registry.npmjs.org/dompurify/3.4.12) | Apache-2.0 (dual licensed upstream) | [LICENSE](#license-cfc7749b96f63bd3); [license_header](#license-5803fa925ae1703b); [LICENSE-MPL](#license-fab3dd6bdab226f1) |
| es-toolkit | 1.45.1 | [release](https://registry.npmjs.org/es-toolkit/1.45.1) | MIT | [LICENSE](#license-ed68888a2a28f32a) |
| fastdom | 1.0.12 | [release](https://registry.npmjs.org/fastdom/1.0.12) | MIT | [README-LICENSE.txt](#license-efd1a97ac2074c06) |
| internmap | 2.0.3 | [release](https://registry.npmjs.org/internmap/2.0.3) | ISC | [LICENSE](#license-9ea8903938dab5f1) |
| js-yaml | 4.3.0 | [release](https://registry.npmjs.org/js-yaml/4.3.0) | MIT | [LICENSE](#license-a07bc24468b9654c) |
| katex | 0.16.47 | [release](https://registry.npmjs.org/katex/0.16.47) | MIT | [LICENSE](#license-766ccc1f306c885a) |
| khroma | 2.1.0 | [release](https://registry.npmjs.org/khroma/2.1.0) | MIT | [license](#license-66b333b0f66759a0) |
| layout-base | 1.0.2 | [release](https://registry.npmjs.org/layout-base/1.0.2) | MIT | [LICENSE](#license-eabb762d8a95109a) |
| layout-base | 2.0.1 | [release](https://registry.npmjs.org/layout-base/2.0.1) | MIT | [LICENSE](#license-eabb762d8a95109a) |
| lodash-es | 4.18.1 | [release](https://registry.npmjs.org/lodash-es/4.18.1) | MIT | [LICENSE](#license-f71e8ed126b46346) |
| marked | 16.3.0 | [release](https://registry.npmjs.org/marked/16.3.0) | MIT | [LICENSE.md](#license-8e3a3f82f59a6095) |
| roughjs | 4.6.6 | [release](https://registry.npmjs.org/roughjs/4.6.6) | MIT | [LICENSE](#license-dca9a392272606ac) |
| stylis | 4.3.6 | [release](https://registry.npmjs.org/stylis/4.3.6) | MIT | [LICENSE](#license-88c579c6ee44b411) |
| ts-dedent | 2.2.0 | [release](https://registry.npmjs.org/ts-dedent/2.2.0) | MIT | [LICENSE](#license-eff13095d9d762c3) |
| uuid | 14.0.0 | [release](https://registry.npmjs.org/uuid/14.0.0) | MIT | [LICENSE.md](#license-beaa6b04fb82e41d) |
| vscode-jsonrpc | 8.2.0 | [release](https://registry.npmjs.org/vscode-jsonrpc/8.2.0) | MIT | [License.txt](#license-ec9ee83580841e8e); [thirdpartynotices.txt](#license-a89123562fe364dc) |
| vscode-languageserver-protocol | 3.17.5 | [release](https://registry.npmjs.org/vscode-languageserver-protocol/3.17.5) | MIT | [License.txt](#license-ec9ee83580841e8e); [thirdpartynotices.txt](#license-9265d27cf75775aa) |
| vscode-languageserver-types | 3.17.5 | [release](https://registry.npmjs.org/vscode-languageserver-types/3.17.5) | MIT | [License.txt](#license-ec9ee83580841e8e); [thirdpartynotices.txt](#license-a89123562fe364dc) |

FastDom publishes its original MIT text in the release's `README.md` License
section rather than a standalone LICENSE file; that section is reproduced
verbatim below. Khroma's package metadata omits a license field, so its MIT
designation is taken from the original `package/license` text.

## Dependencies installed separately

The manifests below describe dependencies installed by Python/npm package
managers, separately from the browser files checked into this repository.
[requirements.txt](requirements.txt) includes the Portal runtime requirements.
The tables cover the direct dependencies declared in these manifests; they are
not a complete inventory of an arbitrary installed environment or a replacement
for the licenses and notices supplied with each package.

The reference versions were checked against official PyPI/npm release metadata
on **2026-10-19**, with original license text checked where metadata was ambiguous.
These versions are evidence for the license descriptions, **not dependency
pins**: the manifests currently leave versions open. The version actually
installed, including its transitive dependencies and bundled native libraries,
determines which upstream terms and notices apply.

### Python dependencies

| Package | Reference version | Upstream license / scope | Declared in |
| --- | --- | --- | --- |
| [nats-py](https://pypi.org/project/nats-py/2.15.0/) | 2.15.0 | Apache-2.0 | [Portal runtime](requirements-portal-runtime.txt) |
| [aiohttp](https://pypi.org/project/aiohttp/3.13.3/) | 3.13.3 | Apache-2.0 AND MIT | [Portal runtime](requirements-portal-runtime.txt) |
| [jsonschema](https://pypi.org/project/jsonschema/4.10.3/) | 4.10.3 | MIT | [Portal runtime](requirements-portal-runtime.txt) |
| [markdown-it-py](https://pypi.org/project/markdown-it-py/3.0.0/) | 3.0.0 | MIT (including the original markdown-it notice) | [Portal runtime](requirements-portal-runtime.txt) |
| [packaging](https://pypi.org/project/packaging/26.3/) | 26.3 | Apache-2.0 OR BSD-2-Clause | [Portal runtime](requirements-portal-runtime.txt) |
| [PyYAML](https://pypi.org/project/PyYAML/6.0.3/) | 6.0.3 | MIT | [Portal runtime](requirements-portal-runtime.txt) |
| [Pillow](https://pypi.org/project/Pillow/12.3.0/) | 12.3.0 | MIT-CMU | [Portal runtime](requirements-portal-runtime.txt) |
| [psycopg](https://pypi.org/project/psycopg/3.2.13/) | 3.2.13 | LGPL-3.0-only | [Portal runtime](requirements-portal-runtime.txt); [Claude backend](requirements-claude-code.txt) |
| [psycopg-binary](https://pypi.org/project/psycopg-binary/3.2.13/) | 3.2.13 | LGPL-3.0-only | [Portal runtime](requirements-portal-runtime.txt); [Claude backend](requirements-claude-code.txt) |
| [psycopg-pool](https://pypi.org/project/psycopg-pool/3.3.3/) | 3.3.3 | LGPL-3.0-only | [Portal runtime](requirements-portal-runtime.txt); [Claude backend](requirements-claude-code.txt) |
| [qrcode](https://pypi.org/project/qrcode/8.2/) | 8.2 | BSD-3-Clause; includes an MIT-origin notice | [Portal runtime](requirements-portal-runtime.txt) |
| [starlette](https://pypi.org/project/starlette/0.47.2/) | 0.47.2 | BSD-3-Clause | [Portal runtime](requirements-portal-runtime.txt) |
| [uvicorn](https://pypi.org/project/uvicorn/0.35.0/) | 0.35.0 | BSD-3-Clause | [Portal runtime](requirements-portal-runtime.txt) |
| [webauthn](https://pypi.org/project/webauthn/3.0.0/) | 3.0.0 | BSD-3-Clause | [Portal runtime](requirements-portal-runtime.txt) |
| [cryptography](https://pypi.org/project/cryptography/50.0.1/) | 50.0.1 | Apache-2.0 OR BSD-3-Clause | [Portal runtime](requirements-portal-runtime.txt) |
| [numpy](https://pypi.org/project/numpy/2.5.4/) | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | [optional embeddings](requirements-society-service.txt) |
| [onnxruntime](https://pypi.org/project/onnxruntime/1.29.0/) | 1.29.0 | MIT; bundled components have additional notices | [optional embeddings](requirements-society-service.txt) |
| [tokenizers](https://pypi.org/project/tokenizers/0.23.1/) | 0.23.1 | Apache-2.0 | [optional embeddings](requirements-society-service.txt) |
| [claude-agent-sdk](https://pypi.org/project/claude-agent-sdk/0.2.111/) | 0.2.111 | MIT for SDK source; see SDK/CLI terms below | [Claude backend](requirements-claude-code.txt) |
| [pytest](https://pypi.org/project/pytest/9.1.1/) | 9.1.1 | MIT | [Python tests](requirements-test.txt) |

`aiohttp` includes llhttp's MIT license alongside its Apache-2.0 license.
`qrcode` retains a separate notice for its MIT-origin QR implementation; see the
[original release license](https://raw.githubusercontent.com/lincolnloop/python-qrcode/v8.2/LICENSE).
Psycopg, its binary package and its connection pool are LGPL-3.0-only, not
Apache-2.0. The [binary installation](https://www.psycopg.org/psycopg3/docs/basic/install.html#binary-installation)
also supplies client libraries with their own terms. NumPy and ONNX Runtime
similarly include additional notices for bundled code. When redistributing an
installed runtime, retain these packages' complete license/notice material and
meet the source and other redistribution requirements of the actual artifacts;
the table's top-level package names do not replace those requirements.

### JavaScript tooling and runtimes

| Package | Reference version | Upstream license / scope | Declared in |
| --- | --- | --- | --- |
| [@babel/parser](https://registry.npmjs.org/@babel%2fparser/7.28.5) | 7.28.5 | MIT | [JavaScript tooling](package.json) |
| [@babel/traverse](https://registry.npmjs.org/@babel%2ftraverse/7.28.5) | 7.28.5 | MIT | [JavaScript tooling](package.json) |
| [jsdom](https://registry.npmjs.org/jsdom/26.1.0) | 26.1.0 | MIT | [JavaScript tooling](package.json) |
| [monaco-editor](https://registry.npmjs.org/monaco-editor/0.56.0) | 0.56.0 | MIT | [JavaScript tooling](package.json) |
| [@deepseek-ai/cordis](https://registry.npmjs.org/@deepseek-ai%2fcordis/4.0.4) | 4.0.4 | MIT | [DSH runtime](management_portal/dsh_runtime/package.json) |
| [@deepseek-ai/dsh](https://registry.npmjs.org/@deepseek-ai%2fdsh/0.2.0-rc.2) | 0.2.0-rc.2 | MIT | [DSH runtime](management_portal/dsh_runtime/package.json) |
| [@deepseek-ai/dsh-llm](https://registry.npmjs.org/@deepseek-ai%2fdsh-llm/0.2.0-rc.2) | 0.2.0-rc.2 | MIT | [DSH runtime](management_portal/dsh_runtime/package.json) |
| [playwright](https://registry.npmjs.org/playwright/1.61.1) | 1.61.1 | Apache-2.0 | [TAU runtime](management_portal/test_framework/tau_runtime/package.json) |
| [d3](https://registry.npmjs.org/d3/7.9.0) | 7.9.0 | ISC | [development projection](management_portal/workbench_projections/development_app/package.json) |
| [@dagrejs/dagre](https://registry.npmjs.org/@dagrejs%2fdagre/1.1.5) | 1.1.5 | MIT | [development projection](management_portal/workbench_projections/development_app/package.json) |
| [cytoscape](https://registry.npmjs.org/cytoscape/3.33.1) | 3.33.1 | MIT | [development projection](management_portal/workbench_projections/development_app/package.json) |
| [marked](https://registry.npmjs.org/marked/18.1.1) | 18.1.1 | MIT | [development projection](management_portal/workbench_projections/development_app/package.json) |
| [dompurify](https://registry.npmjs.org/dompurify/3.4.17) | 3.4.17 | (MPL-2.0 OR Apache-2.0) | [development projection](management_portal/workbench_projections/development_app/package.json) |
| [esbuild](https://registry.npmjs.org/esbuild/0.25.12) | 0.25.12 | MIT | [development projection](management_portal/workbench_projections/development_app/package.json); [research projection](management_portal/workbench_projections/research_apps/package.json) |

The first-party npm manifests use Apache-2.0 for Portal's own code. Their
`private` setting controls npm publication and does not change the license of
Portal or of their dependencies. The browser inventory above is authoritative
for the copies checked into `static/`, including their bundled dependencies.
Playwright's separately downloaded browser binaries and their bundled code
retain the notices supplied with those binaries; Playwright's Apache-2.0 license
does not replace browser licenses.

### External programs, SDKs and data

The [Claude backend requirements](requirements-claude-code.txt) install the
Claude Agent SDK. Its [source LICENSE](https://github.com/anthropics/claude-agent-sdk-python/blob/main/LICENSE)
is MIT, while its [README terms](https://github.com/anthropics/claude-agent-sdk-python#license-and-terms)
refer to Anthropic's commercial terms subject to component-specific licenses.
The SDK also bundles Claude Code: the CLI's [own terms](https://github.com/anthropics/claude-code/blob/main/LICENSE.md)
are distinct from the SDK source license. Portal's installer uses that supplied
CLI; this repository does not grant an Apache-2.0 license to it.

External AI backends, downloaded model weights, browser binaries, host tools
(such as Git, PostgreSQL, NATS, Poppler and TeX), and user-imported papers,
repositories, skills or other data keep their own licenses and usage terms.
Declaring, calling or importing them does not relicense their contents as Portal
code. The optional embedding requirements cover the Python runtime packages,
not a license grant for model weights.

The release installer can download private native tools from its pinned
[catalog](config/release-native-dependencies.json). PostgreSQL binaries are
third-party builds from [theseus-rs/postgresql-binaries](https://github.com/theseus-rs/postgresql-binaries),
under the PostgreSQL license; NATS Server comes from the
[NATS project](https://github.com/nats-io/nats-server), under Apache-2.0;
[Node.js](https://nodejs.org/) includes npm and its component-specific notices.
These binary archives are fetched at installation time, not bundled as Portal
source. Their available license, notice and build-metadata files are retained.
See [native dependency installation](docs/guide/release-native-dependencies.md)
for versions, supplier distinctions, provenance receipts and platform limits.

Windows packages also include the pinned Microsoft Visual C++ Redistributable
identified in [msvc-runtime.json](scripts/windows/msvc-runtime.json). Its selected
CRT DLLs are deployed beside PostgreSQL; Microsoft's original license is retained
as `Microsoft-Visual-C++-License.rtf` in that directory. These files remain subject
to Microsoft's license, not Portal's license. The redistributable installer is
not executed and no global runtime installation is required.

When updating dependencies or replacing distributed assets, update the relevant
inventory and preserve the original copyright, license and notice files from
the exact artifacts. Recheck bundled/transitive code when those bytes change;
package metadata alone is not an inventory of a compiled bundle.

## Original bundled notices

The following text is copied from the exact upstream release archives. Identical
license texts are shared between the package entries that reference them; their
copyright statements have not been rewritten.

<a id="license-0984740e0c3d725c"></a>

### @braintree/sanitize-url@7.1.2

Original: `package/LICENSE` in [@braintree/sanitize-url@7.1.2](https://registry.npmjs.org/@braintree/sanitize-url/-/sanitize-url-7.1.2.tgz).

````text
MIT License

Copyright (c) 2017 Braintree

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
````

<a id="license-a86793ac023162ce"></a>

### @iconify/utils@3.0.2

Original: `package/license.txt` in [@iconify/utils@3.0.2](https://registry.npmjs.org/@iconify/utils/-/utils-3.0.2.tgz).

````text
MIT License

Copyright (c) 2021-PRESENT Vjacheslav Trushkin

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
````

<a id="license-6a3508febf2cfccf"></a>

### @upsetjs/venn.js@2.0.0

Original: `package/LICENSE` in [@upsetjs/venn.js@2.0.0](https://registry.npmjs.org/@upsetjs/venn.js/-/venn.js-2.0.0.tgz).

````text
MIT License

Copyright (c) 2013 Ben Frederickson
Copyright (c) 2021 Samuel Gratzl

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
````

<a id="license-5fb3cf4a14c3c5af"></a>

### cose-base@1.0.3, cose-base@2.2.0

Original: `package/LICENSE` in [cose-base@1.0.3](https://registry.npmjs.org/cose-base/-/cose-base-1.0.3.tgz).

Original: `package/LICENSE` in [cose-base@2.2.0](https://registry.npmjs.org/cose-base/-/cose-base-2.2.0.tgz).

````text
MIT License

Copyright (c) 2019 - present, iVis@Bilkent.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
````

<a id="license-eb319c6e6f233607"></a>

### cytoscape@3.34.0

Original: `package/LICENSE` in [cytoscape@3.34.0](https://registry.npmjs.org/cytoscape/-/cytoscape-3.34.0.tgz).

````text
Copyright (c) 2016-2026, The Cytoscape Consortium.

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the “Software”), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies
of the Software, and to permit persons to whom the Software is furnished to do
so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
````

<a id="license-440fc58a56a12814"></a>

### cytoscape-cose-bilkent@4.1.0

Original: `package/LICENSE` in [cytoscape-cose-bilkent@4.1.0](https://registry.npmjs.org/cytoscape-cose-bilkent/-/cytoscape-cose-bilkent-4.1.0.tgz).

````text


Copyright (c) 2016-2018, The Cytoscape Consortium.

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the “Software”), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies
of the Software, and to permit persons to whom the Software is furnished to do
so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
````

<a id="license-2837634f40394921"></a>

### cytoscape-fcose@2.2.0

Original: `package/LICENSE` in [cytoscape-fcose@2.2.0](https://registry.npmjs.org/cytoscape-fcose/-/cytoscape-fcose-2.2.0.tgz).

````text
Copyright (c) 2018 - present, iVis-at-Bilkent.

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the “Software”), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies
of the Software, and to permit persons to whom the Software is furnished to do
so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
````

<a id="license-0747bebeb06b1095"></a>

### d3-array@2.12.1

Original: `package/LICENSE` in [d3-array@2.12.1](https://registry.npmjs.org/d3-array/-/d3-array-2.12.1.tgz).

````text
Copyright 2010-2020 Mike Bostock
All rights reserved.

Redistribution and use in source and binary forms, with or without modification,
are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the author nor the names of contributors may be used to
  endorse or promote products derived from this software without specific prior
  written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
````

<a id="license-3e6849627f74ff73"></a>

### d3-array@3.2.4

Original: `package/LICENSE` in [d3-array@3.2.4](https://registry.npmjs.org/d3-array/-/d3-array-3.2.4.tgz).

````text
Copyright 2010-2023 Mike Bostock

Permission to use, copy, modify, and/or distribute this software for any purpose
with or without fee is hereby granted, provided that the above copyright notice
and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND
FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS
OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER
TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF
THIS SOFTWARE.
````

<a id="license-e008c5e25a6be382"></a>

### d3-axis@3.0.0, d3-brush@3.0.0, d3-dispatch@3.0.1, d3-format@3.1.0, d3-hierarchy@3.1.2, d3-interpolate@3.0.1, d3-scale@4.0.2, d3-selection@3.0.0, d3-time-format@4.1.0, d3-timer@3.0.1, d3-transition@3.0.1, d3-zoom@3.0.0

Original: `package/LICENSE` in [d3-axis@3.0.0](https://registry.npmjs.org/d3-axis/-/d3-axis-3.0.0.tgz).

Original: `package/LICENSE` in [d3-brush@3.0.0](https://registry.npmjs.org/d3-brush/-/d3-brush-3.0.0.tgz).

Original: `package/LICENSE` in [d3-dispatch@3.0.1](https://registry.npmjs.org/d3-dispatch/-/d3-dispatch-3.0.1.tgz).

Original: `package/LICENSE` in [d3-format@3.1.0](https://registry.npmjs.org/d3-format/-/d3-format-3.1.0.tgz).

Original: `package/LICENSE` in [d3-hierarchy@3.1.2](https://registry.npmjs.org/d3-hierarchy/-/d3-hierarchy-3.1.2.tgz).

Original: `package/LICENSE` in [d3-interpolate@3.0.1](https://registry.npmjs.org/d3-interpolate/-/d3-interpolate-3.0.1.tgz).

Original: `package/LICENSE` in [d3-scale@4.0.2](https://registry.npmjs.org/d3-scale/-/d3-scale-4.0.2.tgz).

Original: `package/LICENSE` in [d3-selection@3.0.0](https://registry.npmjs.org/d3-selection/-/d3-selection-3.0.0.tgz).

Original: `package/LICENSE` in [d3-time-format@4.1.0](https://registry.npmjs.org/d3-time-format/-/d3-time-format-4.1.0.tgz).

Original: `package/LICENSE` in [d3-timer@3.0.1](https://registry.npmjs.org/d3-timer/-/d3-timer-3.0.1.tgz).

Original: `package/LICENSE` in [d3-transition@3.0.1](https://registry.npmjs.org/d3-transition/-/d3-transition-3.0.1.tgz).

Original: `package/LICENSE` in [d3-zoom@3.0.0](https://registry.npmjs.org/d3-zoom/-/d3-zoom-3.0.0.tgz).

````text
Copyright 2010-2021 Mike Bostock

Permission to use, copy, modify, and/or distribute this software for any purpose
with or without fee is hereby granted, provided that the above copyright notice
and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND
FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS
OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER
TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF
THIS SOFTWARE.
````

<a id="license-faa682e3e430941f"></a>

### d3-color@3.1.0, d3-shape@3.2.0, d3-time@3.1.0

Original: `package/LICENSE` in [d3-color@3.1.0](https://registry.npmjs.org/d3-color/-/d3-color-3.1.0.tgz).

Original: `package/LICENSE` in [d3-shape@3.2.0](https://registry.npmjs.org/d3-shape/-/d3-shape-3.2.0.tgz).

Original: `package/LICENSE` in [d3-time@3.1.0](https://registry.npmjs.org/d3-time/-/d3-time-3.1.0.tgz).

````text
Copyright 2010-2022 Mike Bostock

Permission to use, copy, modify, and/or distribute this software for any purpose
with or without fee is hereby granted, provided that the above copyright notice
and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND
FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS
OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER
TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF
THIS SOFTWARE.
````

<a id="license-b989d9085148d21f"></a>

### d3-ease@3.0.1

Original: `package/LICENSE` in [d3-ease@3.0.1](https://registry.npmjs.org/d3-ease/-/d3-ease-3.0.1.tgz).

````text
Copyright 2010-2021 Mike Bostock
Copyright 2001 Robert Penner
All rights reserved.

Redistribution and use in source and binary forms, with or without modification,
are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the author nor the names of contributors may be used to
  endorse or promote products derived from this software without specific prior
  written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
````

<a id="license-b8265327ab678f55"></a>

### d3-path@1.0.9

Original: `package/LICENSE` in [d3-path@1.0.9](https://registry.npmjs.org/d3-path/-/d3-path-1.0.9.tgz).

````text
Copyright 2015-2016 Mike Bostock
All rights reserved.

Redistribution and use in source and binary forms, with or without modification,
are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the author nor the names of contributors may be used to
  endorse or promote products derived from this software without specific prior
  written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
````

<a id="license-a09b0d6d76e768fc"></a>

### d3-path@3.1.0

Original: `package/LICENSE` in [d3-path@3.1.0](https://registry.npmjs.org/d3-path/-/d3-path-3.1.0.tgz).

````text
Copyright 2015-2022 Mike Bostock

Permission to use, copy, modify, and/or distribute this software for any purpose
with or without fee is hereby granted, provided that the above copyright notice
and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND
FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS
OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER
TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF
THIS SOFTWARE.
````

<a id="license-2bf785e778d67a4f"></a>

### d3-sankey@0.12.3

Original: `package/LICENSE` in [d3-sankey@0.12.3](https://registry.npmjs.org/d3-sankey/-/d3-sankey-0.12.3.tgz).

````text
Copyright 2015, Mike Bostock
All rights reserved.

Redistribution and use in source and binary forms, with or without modification,
are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the author nor the names of contributors may be used to
  endorse or promote products derived from this software without specific prior
  written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
````

<a id="license-0986d93944902cca"></a>

### d3-scale-chromatic@3.1.0

Original: `package/LICENSE` in [d3-scale-chromatic@3.1.0](https://registry.npmjs.org/d3-scale-chromatic/-/d3-scale-chromatic-3.1.0.tgz).

````text
Copyright 2010-2024 Mike Bostock

Permission to use, copy, modify, and/or distribute this software for any purpose
with or without fee is hereby granted, provided that the above copyright notice
and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND
FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS
OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER
TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF
THIS SOFTWARE.

Apache-Style Software License for ColorBrewer software and ColorBrewer Color Schemes

Copyright 2002 Cynthia Brewer, Mark Harrower, and The Pennsylvania State University

Licensed under the Apache License, Version 2.0 (the "License"); you may not use
this file except in compliance with the License. You may obtain a copy of the
License at

http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software distributed
under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
CONDITIONS OF ANY KIND, either express or implied. See the License for the
specific language governing permissions and limitations under the License.
````

<a id="license-7eea8533ea92bd8c"></a>

### d3-shape@1.3.7

Original: `package/LICENSE` in [d3-shape@1.3.7](https://registry.npmjs.org/d3-shape/-/d3-shape-1.3.7.tgz).

````text
Copyright 2010-2015 Mike Bostock
All rights reserved.

Redistribution and use in source and binary forms, with or without modification,
are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the author nor the names of contributors may be used to
  endorse or promote products derived from this software without specific prior
  written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
````

<a id="license-d01eb84ccce437db"></a>

### dagre-d3-es@7.0.14

Original: `package/LICENSE.md` in [dagre-d3-es@7.0.14](https://registry.npmjs.org/dagre-d3-es/-/dagre-d3-es-7.0.14.tgz).

````text
Original dagre-d3 copyright: Copyright (c) 2013 Chris Pettitt
Original dagre copyright: Copyright (c) 2012-2014 Chris Pettitt
Original graphlib copyright: Copyright (c) 2012-2014 Chris Pettitt

Copyright (c) 2022-2024 Thibaut Lassalle, David Newell, Alois Klink, Sidharth Vinod and dagre-es contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
````

<a id="license-5faab7526d055651"></a>

### dayjs@1.11.21

Original: `package/LICENSE` in [dayjs@1.11.21](https://registry.npmjs.org/dayjs/-/dayjs-1.11.21.tgz).

````text
MIT License

Copyright (c) 2018-present, iamkun

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
````

<a id="license-cfc7749b96f63bd3"></a>

### dompurify@3.4.12

Original: `package/LICENSE` in [dompurify@3.4.12](https://registry.npmjs.org/dompurify/-/dompurify-3.4.12.tgz).

````text

                                 Apache License
                           Version 2.0, January 2004
                        http://www.apache.org/licenses/

   TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION

   1. Definitions.

      "License" shall mean the terms and conditions for use, reproduction,
      and distribution as defined by Sections 1 through 9 of this document.

      "Licensor" shall mean the copyright owner or entity authorized by
      the copyright owner that is granting the License.

      "Legal Entity" shall mean the union of the acting entity and all
      other entities that control, are controlled by, or are under common
      control with that entity. For the purposes of this definition,
      "control" means (i) the power, direct or indirect, to cause the
      direction or management of such entity, whether by contract or
      otherwise, or (ii) ownership of fifty percent (50%) or more of the
      outstanding shares, or (iii) beneficial ownership of such entity.

      "You" (or "Your") shall mean an individual or Legal Entity
      exercising permissions granted by this License.

      "Source" form shall mean the preferred form for making modifications,
      including but not limited to software source code, documentation
      source, and configuration files.

      "Object" form shall mean any form resulting from mechanical
      transformation or translation of a Source form, including but
      not limited to compiled object code, generated documentation,
      and conversions to other media types.

      "Work" shall mean the work of authorship, whether in Source or
      Object form, made available under the License, as indicated by a
      copyright notice that is included in or attached to the work
      (an example is provided in the Appendix below).

      "Derivative Works" shall mean any work, whether in Source or Object
      form, that is based on (or derived from) the Work and for which the
      editorial revisions, annotations, elaborations, or other modifications
      represent, as a whole, an original work of authorship. For the purposes
      of this License, Derivative Works shall not include works that remain
      separable from, or merely link (or bind by name) to the interfaces of,
      the Work and Derivative Works thereof.

      "Contribution" shall mean any work of authorship, including
      the original version of the Work and any modifications or additions
      to that Work or Derivative Works thereof, that is intentionally
      submitted to Licensor for inclusion in the Work by the copyright owner
      or by an individual or Legal Entity authorized to submit on behalf of
      the copyright owner. For the purposes of this definition, "submitted"
      means any form of electronic, verbal, or written communication sent
      to the Licensor or its representatives, including but not limited to
      communication on electronic mailing lists, source code control systems,
      and issue tracking systems that are managed by, or on behalf of, the
      Licensor for the purpose of discussing and improving the Work, but
      excluding communication that is conspicuously marked or otherwise
      designated in writing by the copyright owner as "Not a Contribution."

      "Contributor" shall mean Licensor and any individual or Legal Entity
      on behalf of whom a Contribution has been received by Licensor and
      subsequently incorporated within the Work.

   2. Grant of Copyright License. Subject to the terms and conditions of
      this License, each Contributor hereby grants to You a perpetual,
      worldwide, non-exclusive, no-charge, royalty-free, irrevocable
      copyright license to reproduce, prepare Derivative Works of,
      publicly display, publicly perform, sublicense, and distribute the
      Work and such Derivative Works in Source or Object form.

   3. Grant of Patent License. Subject to the terms and conditions of
      this License, each Contributor hereby grants to You a perpetual,
      worldwide, non-exclusive, no-charge, royalty-free, irrevocable
      (except as stated in this section) patent license to make, have made,
      use, offer to sell, sell, import, and otherwise transfer the Work,
      where such license applies only to those patent claims licensable
      by such Contributor that are necessarily infringed by their
      Contribution(s) alone or by combination of their Contribution(s)
      with the Work to which such Contribution(s) was submitted. If You
      institute patent litigation against any entity (including a
      cross-claim or counterclaim in a lawsuit) alleging that the Work
      or a Contribution incorporated within the Work constitutes direct
      or contributory patent infringement, then any patent licenses
      granted to You under this License for that Work shall terminate
      as of the date such litigation is filed.

   4. Redistribution. You may reproduce and distribute copies of the
      Work or Derivative Works thereof in any medium, with or without
      modifications, and in Source or Object form, provided that You
      meet the following conditions:

      (a) You must give any other recipients of the Work or
          Derivative Works a copy of this License; and

      (b) You must cause any modified files to carry prominent notices
          stating that You changed the files; and

      (c) You must retain, in the Source form of any Derivative Works
          that You distribute, all copyright, patent, trademark, and
          attribution notices from the Source form of the Work,
          excluding those notices that do not pertain to any part of
          the Derivative Works; and

      (d) If the Work includes a "NOTICE" text file as part of its
          distribution, then any Derivative Works that You distribute must
          include a readable copy of the attribution notices contained
          within such NOTICE file, excluding those notices that do not
          pertain to any part of the Derivative Works, in at least one
          of the following places: within a NOTICE text file distributed
          as part of the Derivative Works; within the Source form or
          documentation, if provided along with the Derivative Works; or,
          within a display generated by the Derivative Works, if and
          wherever such third-party notices normally appear. The contents
          of the NOTICE file are for informational purposes only and
          do not modify the License. You may add Your own attribution
          notices within Derivative Works that You distribute, alongside
          or as an addendum to the NOTICE text from the Work, provided
          that such additional attribution notices cannot be construed
          as modifying the License.

      You may add Your own copyright statement to Your modifications and
      may provide additional or different license terms and conditions
      for use, reproduction, or distribution of Your modifications, or
      for any such Derivative Works as a whole, provided Your use,
      reproduction, and distribution of the Work otherwise complies with
      the conditions stated in this License.

   5. Submission of Contributions. Unless You explicitly state otherwise,
      any Contribution intentionally submitted for inclusion in the Work
      by You to the Licensor shall be under the terms and conditions of
      this License, without any additional terms or conditions.
      Notwithstanding the above, nothing herein shall supersede or modify
      the terms of any separate license agreement you may have executed
      with Licensor regarding such Contributions.

   6. Trademarks. This License does not grant permission to use the trade
      names, trademarks, service marks, or product names of the Licensor,
      except as required for reasonable and customary use in describing the
      origin of the Work and reproducing the content of the NOTICE file.

   7. Disclaimer of Warranty. Unless required by applicable law or
      agreed to in writing, Licensor provides the Work (and each
      Contributor provides its Contributions) on an "AS IS" BASIS,
      WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
      implied, including, without limitation, any warranties or conditions
      of TITLE, NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A
      PARTICULAR PURPOSE. You are solely responsible for determining the
      appropriateness of using or redistributing the Work and assume any
      risks associated with Your exercise of permissions under this License.

   8. Limitation of Liability. In no event and under no legal theory,
      whether in tort (including negligence), contract, or otherwise,
      unless required by applicable law (such as deliberate and grossly
      negligent acts) or agreed to in writing, shall any Contributor be
      liable to You for damages, including any direct, indirect, special,
      incidental, or consequential damages of any character arising as a
      result of this License or out of the use or inability to use the
      Work (including but not limited to damages for loss of goodwill,
      work stoppage, computer failure or malfunction, or any and all
      other commercial damages or losses), even if such Contributor
      has been advised of the possibility of such damages.

   9. Accepting Warranty or Additional Liability. While redistributing
      the Work or Derivative Works thereof, You may choose to offer,
      and charge a fee for, acceptance of support, warranty, indemnity,
      or other liability obligations and/or rights consistent with this
      License. However, in accepting such obligations, You may act only
      on Your own behalf and on Your sole responsibility, not on behalf
      of any other Contributor, and only if You agree to indemnify,
      defend, and hold each Contributor harmless for any liability
      incurred by, or claims asserted against, such Contributor by reason
      of your accepting any such warranty or additional liability.

   END OF TERMS AND CONDITIONS

   APPENDIX: How to apply the Apache License to your work.

      To apply the Apache License to your work, attach the following
      boilerplate notice, with the fields enclosed by brackets "[]"
      replaced with your own identifying information. (Don't include
      the brackets!)  The text should be enclosed in the appropriate
      comment syntax for the file format. We also recommend that a
      file or class name and description of purpose be included on the
      same "printed page" as the copyright notice for easier
      identification within third-party archives.

   Copyright [yyyy] [name of copyright owner]

   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at

       http://www.apache.org/licenses/LICENSE-2.0

   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.
````

<a id="license-5803fa925ae1703b"></a>

### dompurify@3.4.12

Original: `package/src/license_header` in [dompurify@3.4.12](https://registry.npmjs.org/dompurify/-/dompurify-3.4.12.tgz).

````text
/*! @license DOMPurify VERSION | (c) Cure53 and other contributors | Released under the Apache license 2.0 and Mozilla Public License 2.0 | github.com/cure53/DOMPurify/blob/VERSION/LICENSE */
````

<a id="license-fab3dd6bdab226f1"></a>

### dompurify@3.4.12

Original: `package/LICENSE-MPL` in [dompurify@3.4.12](https://registry.npmjs.org/dompurify/-/dompurify-3.4.12.tgz).

````text
Mozilla Public License Version 2.0
==================================

1. Definitions
--------------

1.1. "Contributor"
    means each individual or legal entity that creates, contributes to
    the creation of, or owns Covered Software.

1.2. "Contributor Version"
    means the combination of the Contributions of others (if any) used
    by a Contributor and that particular Contributor's Contribution.

1.3. "Contribution"
    means Covered Software of a particular Contributor.

1.4. "Covered Software"
    means Source Code Form to which the initial Contributor has attached
    the notice in Exhibit A, the Executable Form of such Source Code
    Form, and Modifications of such Source Code Form, in each case
    including portions thereof.

1.5. "Incompatible With Secondary Licenses"
    means

    (a) that the initial Contributor has attached the notice described
        in Exhibit B to the Covered Software; or

    (b) that the Covered Software was made available under the terms of
        version 1.1 or earlier of the License, but not also under the
        terms of a Secondary License.

1.6. "Executable Form"
    means any form of the work other than Source Code Form.

1.7. "Larger Work"
    means a work that combines Covered Software with other material, in 
    a separate file or files, that is not Covered Software.

1.8. "License"
    means this document.

1.9. "Licensable"
    means having the right to grant, to the maximum extent possible,
    whether at the time of the initial grant or subsequently, any and
    all of the rights conveyed by this License.

1.10. "Modifications"
    means any of the following:

    (a) any file in Source Code Form that results from an addition to,
        deletion from, or modification of the contents of Covered
        Software; or

    (b) any new file in Source Code Form that contains any Covered
        Software.

1.11. "Patent Claims" of a Contributor
    means any patent claim(s), including without limitation, method,
    process, and apparatus claims, in any patent Licensable by such
    Contributor that would be infringed, but for the grant of the
    License, by the making, using, selling, offering for sale, having
    made, import, or transfer of either its Contributions or its
    Contributor Version.

1.12. "Secondary License"
    means either the GNU General Public License, Version 2.0, the GNU
    Lesser General Public License, Version 2.1, the GNU Affero General
    Public License, Version 3.0, or any later versions of those
    licenses.

1.13. "Source Code Form"
    means the form of the work preferred for making modifications.

1.14. "You" (or "Your")
    means an individual or a legal entity exercising rights under this
    License. For legal entities, "You" includes any entity that
    controls, is controlled by, or is under common control with You. For
    purposes of this definition, "control" means (a) the power, direct
    or indirect, to cause the direction or management of such entity,
    whether by contract or otherwise, or (b) ownership of more than
    fifty percent (50%) of the outstanding shares or beneficial
    ownership of such entity.

2. License Grants and Conditions
--------------------------------

2.1. Grants

Each Contributor hereby grants You a world-wide, royalty-free,
non-exclusive license:

(a) under intellectual property rights (other than patent or trademark)
    Licensable by such Contributor to use, reproduce, make available,
    modify, display, perform, distribute, and otherwise exploit its
    Contributions, either on an unmodified basis, with Modifications, or
    as part of a Larger Work; and

(b) under Patent Claims of such Contributor to make, use, sell, offer
    for sale, have made, import, and otherwise transfer either its
    Contributions or its Contributor Version.

2.2. Effective Date

The licenses granted in Section 2.1 with respect to any Contribution
become effective for each Contribution on the date the Contributor first
distributes such Contribution.

2.3. Limitations on Grant Scope

The licenses granted in this Section 2 are the only rights granted under
this License. No additional rights or licenses will be implied from the
distribution or licensing of Covered Software under this License.
Notwithstanding Section 2.1(b) above, no patent license is granted by a
Contributor:

(a) for any code that a Contributor has removed from Covered Software;
    or

(b) for infringements caused by: (i) Your and any other third party's
    modifications of Covered Software, or (ii) the combination of its
    Contributions with other software (except as part of its Contributor
    Version); or

(c) under Patent Claims infringed by Covered Software in the absence of
    its Contributions.

This License does not grant any rights in the trademarks, service marks,
or logos of any Contributor (except as may be necessary to comply with
the notice requirements in Section 3.4).

2.4. Subsequent Licenses

No Contributor makes additional grants as a result of Your choice to
distribute the Covered Software under a subsequent version of this
License (see Section 10.2) or under the terms of a Secondary License (if
permitted under the terms of Section 3.3).

2.5. Representation

Each Contributor represents that the Contributor believes its
Contributions are its original creation(s) or it has sufficient rights
to grant the rights to its Contributions conveyed by this License.

2.6. Fair Use

This License is not intended to limit any rights You have under
applicable copyright doctrines of fair use, fair dealing, or other
equivalents.

2.7. Conditions

Sections 3.1, 3.2, 3.3, and 3.4 are conditions of the licenses granted
in Section 2.1.

3. Responsibilities
-------------------

3.1. Distribution of Source Form

All distribution of Covered Software in Source Code Form, including any
Modifications that You create or to which You contribute, must be under
the terms of this License. You must inform recipients that the Source
Code Form of the Covered Software is governed by the terms of this
License, and how they can obtain a copy of this License. You may not
attempt to alter or restrict the recipients' rights in the Source Code
Form.

3.2. Distribution of Executable Form

If You distribute Covered Software in Executable Form then:

(a) such Covered Software must also be made available in Source Code
    Form, as described in Section 3.1, and You must inform recipients of
    the Executable Form how they can obtain a copy of such Source Code
    Form by reasonable means in a timely manner, at a charge no more
    than the cost of distribution to the recipient; and

(b) You may distribute such Executable Form under the terms of this
    License, or sublicense it under different terms, provided that the
    license for the Executable Form does not attempt to limit or alter
    the recipients' rights in the Source Code Form under this License.

3.3. Distribution of a Larger Work

You may create and distribute a Larger Work under terms of Your choice,
provided that You also comply with the requirements of this License for
the Covered Software. If the Larger Work is a combination of Covered
Software with a work governed by one or more Secondary Licenses, and the
Covered Software is not Incompatible With Secondary Licenses, this
License permits You to additionally distribute such Covered Software
under the terms of such Secondary License(s), so that the recipient of
the Larger Work may, at their option, further distribute the Covered
Software under the terms of either this License or such Secondary
License(s).

3.4. Notices

You may not remove or alter the substance of any license notices
(including copyright notices, patent notices, disclaimers of warranty,
or limitations of liability) contained within the Source Code Form of
the Covered Software, except that You may alter any license notices to
the extent required to remedy known factual inaccuracies.

3.5. Application of Additional Terms

You may choose to offer, and to charge a fee for, warranty, support,
indemnity or liability obligations to one or more recipients of Covered
Software. However, You may do so only on Your own behalf, and not on
behalf of any Contributor. You must make it absolutely clear that any
such warranty, support, indemnity, or liability obligation is offered by
You alone, and You hereby agree to indemnify every Contributor for any
liability incurred by such Contributor as a result of warranty, support,
indemnity or liability terms You offer. You may include additional
disclaimers of warranty and limitations of liability specific to any
jurisdiction.

4. Inability to Comply Due to Statute or Regulation
---------------------------------------------------

If it is impossible for You to comply with any of the terms of this
License with respect to some or all of the Covered Software due to
statute, judicial order, or regulation then You must: (a) comply with
the terms of this License to the maximum extent possible; and (b)
describe the limitations and the code they affect. Such description must
be placed in a text file included with all distributions of the Covered
Software under this License. Except to the extent prohibited by statute
or regulation, such description must be sufficiently detailed for a
recipient of ordinary skill to be able to understand it.

5. Termination
--------------

5.1. The rights granted under this License will terminate automatically
if You fail to comply with any of its terms. However, if You become
compliant, then the rights granted under this License from a particular
Contributor are reinstated (a) provisionally, unless and until such
Contributor explicitly and finally terminates Your grants, and (b) on an
ongoing basis, if such Contributor fails to notify You of the
non-compliance by some reasonable means prior to 60 days after You have
come back into compliance. Moreover, Your grants from a particular
Contributor are reinstated on an ongoing basis if such Contributor
notifies You of the non-compliance by some reasonable means, this is the
first time You have received notice of non-compliance with this License
from such Contributor, and You become compliant prior to 30 days after
Your receipt of the notice.

5.2. If You initiate litigation against any entity by asserting a patent
infringement claim (excluding declaratory judgment actions,
counter-claims, and cross-claims) alleging that a Contributor Version
directly or indirectly infringes any patent, then the rights granted to
You by any and all Contributors for the Covered Software under Section
2.1 of this License shall terminate.

5.3. In the event of termination under Sections 5.1 or 5.2 above, all
end user license agreements (excluding distributors and resellers) which
have been validly granted by You or Your distributors under this License
prior to termination shall survive termination.

************************************************************************
*                                                                      *
*  6. Disclaimer of Warranty                                           *
*  -------------------------                                           *
*                                                                      *
*  Covered Software is provided under this License on an "as is"       *
*  basis, without warranty of any kind, either expressed, implied, or  *
*  statutory, including, without limitation, warranties that the       *
*  Covered Software is free of defects, merchantable, fit for a        *
*  particular purpose or non-infringing. The entire risk as to the     *
*  quality and performance of the Covered Software is with You.        *
*  Should any Covered Software prove defective in any respect, You     *
*  (not any Contributor) assume the cost of any necessary servicing,   *
*  repair, or correction. This disclaimer of warranty constitutes an   *
*  essential part of this License. No use of any Covered Software is   *
*  authorized under this License except under this disclaimer.         *
*                                                                      *
************************************************************************

************************************************************************
*                                                                      *
*  7. Limitation of Liability                                          *
*  --------------------------                                          *
*                                                                      *
*  Under no circumstances and under no legal theory, whether tort      *
*  (including negligence), contract, or otherwise, shall any           *
*  Contributor, or anyone who distributes Covered Software as          *
*  permitted above, be liable to You for any direct, indirect,         *
*  special, incidental, or consequential damages of any character      *
*  including, without limitation, damages for lost profits, loss of    *
*  goodwill, work stoppage, computer failure or malfunction, or any    *
*  and all other commercial damages or losses, even if such party      *
*  shall have been informed of the possibility of such damages. This   *
*  limitation of liability shall not apply to liability for death or   *
*  personal injury resulting from such party's negligence to the       *
*  extent applicable law prohibits such limitation. Some               *
*  jurisdictions do not allow the exclusion or limitation of           *
*  incidental or consequential damages, so this exclusion and          *
*  limitation may not apply to You.                                    *
*                                                                      *
************************************************************************

8. Litigation
-------------

Any litigation relating to this License may be brought only in the
courts of a jurisdiction where the defendant maintains its principal
place of business and such litigation shall be governed by laws of that
jurisdiction, without reference to its conflict-of-law provisions.
Nothing in this Section shall prevent a party's ability to bring
cross-claims or counter-claims.

9. Miscellaneous
----------------

This License represents the complete agreement concerning the subject
matter hereof. If any provision of this License is held to be
unenforceable, such provision shall be reformed only to the extent
necessary to make it enforceable. Any law or regulation which provides
that the language of a contract shall be construed against the drafter
shall not be used to construe this License against a Contributor.

10. Versions of the License
---------------------------

10.1. New Versions

Mozilla Foundation is the license steward. Except as provided in Section
10.3, no one other than the license steward has the right to modify or
publish new versions of this License. Each version will be given a
distinguishing version number.

10.2. Effect of New Versions

You may distribute the Covered Software under the terms of the version
of the License under which You originally received the Covered Software,
or under the terms of any subsequent version published by the license
steward.

10.3. Modified Versions

If you create software not governed by this License, and you want to
create a new license for such software, you may create and use a
modified version of this License if you rename the license and remove
any references to the name of the license steward (except to note that
such modified license differs from this License).

10.4. Distributing Source Code Form that is Incompatible With Secondary
Licenses

If You choose to distribute Source Code Form that is Incompatible With
Secondary Licenses under the terms of this version of the License, the
notice described in Exhibit B of this License must be attached.

Exhibit A - Source Code Form License Notice
-------------------------------------------

  This Source Code Form is subject to the terms of the Mozilla Public
  License, v. 2.0. If a copy of the MPL was not distributed with this
  file, You can obtain one at http://mozilla.org/MPL/2.0/.

If it is not possible or desirable to put the notice in a particular
file, then You may include the notice in a location (such as a LICENSE
file in a relevant directory) where a recipient would be likely to look
for such a notice.

You may add additional accurate notices of copyright ownership.

Exhibit B - "Incompatible With Secondary Licenses" Notice
---------------------------------------------------------

  This Source Code Form is "Incompatible With Secondary Licenses", as
  defined by the Mozilla Public License, v. 2.0.
````

<a id="license-ed68888a2a28f32a"></a>

### es-toolkit@1.45.1

Original: `package/LICENSE` in [es-toolkit@1.45.1](https://registry.npmjs.org/es-toolkit/-/es-toolkit-1.45.1.tgz).

````text
MIT License

Copyright (c) 2024 Viva Republica, Inc

Copyright OpenJS Foundation and other contributors

Parts of the test suite and compatibility layer in `es-toolkit/compat` are derived from Lodash (https://github.com/lodash/lodash) by the OpenJS Foundation (https://openjsf.org/) and Underscore.js by Jeremy Ashkenas, DocumentCloud and Investigative Reporters & Editors (http://underscorejs.org/)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
````

<a id="license-efd1a97ac2074c06"></a>

### fastdom@1.0.12

Original: `package/README.md#License` in [fastdom@1.0.12](https://registry.npmjs.org/fastdom/-/fastdom-1.0.12.tgz).

````text

(The MIT License)

Copyright (c) 2016 Wilson Page <wilsonpage@me.com>

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the 'Software'), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED 'AS IS', WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
````

<a id="license-9ea8903938dab5f1"></a>

### internmap@2.0.3

Original: `package/LICENSE` in [internmap@2.0.3](https://registry.npmjs.org/internmap/-/internmap-2.0.3.tgz).

````text
Copyright 2021 Mike Bostock

Permission to use, copy, modify, and/or distribute this software for any purpose
with or without fee is hereby granted, provided that the above copyright notice
and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND
FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS
OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER
TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF
THIS SOFTWARE.
````

<a id="license-a07bc24468b9654c"></a>

### js-yaml@4.3.0

Original: `package/LICENSE` in [js-yaml@4.3.0](https://registry.npmjs.org/js-yaml/-/js-yaml-4.3.0.tgz).

````text
(The MIT License)

Copyright (C) 2011-2015 by Vitaly Puzrin

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
````

<a id="license-766ccc1f306c885a"></a>

### katex@0.16.47

Original: `package/LICENSE` in [katex@0.16.47](https://registry.npmjs.org/katex/-/katex-0.16.47.tgz).

````text
The MIT License (MIT)

Copyright (c) 2013-2020 Khan Academy and other contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
````

<a id="license-66b333b0f66759a0"></a>

### khroma@2.1.0

Original: `package/license` in [khroma@2.1.0](https://registry.npmjs.org/khroma/-/khroma-2.1.0.tgz).

````text
The MIT License (MIT)

Copyright (c) 2019-present Fabio Spampinato, Andrew Maney

Permission is hereby granted, free of charge, to any person obtaining a
copy of this software and associated documentation files (the "Software"),
to deal in the Software without restriction, including without limitation
the rights to use, copy, modify, merge, publish, distribute, sublicense,
and/or sell copies of the Software, and to permit persons to whom the
Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
DEALINGS IN THE SOFTWARE.
````

<a id="license-eabb762d8a95109a"></a>

### layout-base@1.0.2, layout-base@2.0.1

Original: `package/LICENSE` in [layout-base@1.0.2](https://registry.npmjs.org/layout-base/-/layout-base-1.0.2.tgz).

Original: `package/LICENSE` in [layout-base@2.0.1](https://registry.npmjs.org/layout-base/-/layout-base-2.0.1.tgz).

````text
MIT License

Copyright (c) 2019 iVis@Bilkent

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
````

<a id="license-f71e8ed126b46346"></a>

### lodash-es@4.18.1

Original: `package/LICENSE` in [lodash-es@4.18.1](https://registry.npmjs.org/lodash-es/-/lodash-es-4.18.1.tgz).

````text
Copyright OpenJS Foundation and other contributors <https://openjsf.org/>

Based on Underscore.js, copyright Jeremy Ashkenas,
DocumentCloud and Investigative Reporters & Editors <http://underscorejs.org/>

This software consists of voluntary contributions made by many
individuals. For exact contribution history, see the revision history
available at https://github.com/lodash/lodash

The following license applies to all parts of this software except as
documented below:

====

Permission is hereby granted, free of charge, to any person obtaining
a copy of this software and associated documentation files (the
"Software"), to deal in the Software without restriction, including
without limitation the rights to use, copy, modify, merge, publish,
distribute, sublicense, and/or sell copies of the Software, and to
permit persons to whom the Software is furnished to do so, subject to
the following conditions:

The above copyright notice and this permission notice shall be
included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

====

Copyright and related rights for sample code are waived via CC0. Sample
code is defined as all source code displayed within the prose of the
documentation.

CC0: http://creativecommons.org/publicdomain/zero/1.0/

====

Files located in the node_modules and vendor directories are externally
maintained libraries used by this software which have their own
licenses; we recommend you read them, as their terms may differ from the
terms above.
````

<a id="license-8e3a3f82f59a6095"></a>

### marked@16.3.0

Original: `package/LICENSE.md` in [marked@16.3.0](https://registry.npmjs.org/marked/-/marked-16.3.0.tgz).

````text
# License information

## Contribution License Agreement

If you contribute code to this project, you are implicitly allowing your code
to be distributed under the MIT license. You are also implicitly verifying that
all code is your original work. `</legalese>`

## Marked

Copyright (c) 2018+, MarkedJS (https://github.com/markedjs/)
Copyright (c) 2011-2018, Christopher Jeffrey (https://github.com/chjj/)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.

## Markdown

Copyright © 2004, John Gruber
http://daringfireball.net/
All rights reserved.

Redistribution and use in source and binary forms, with or without modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this list of conditions and the following disclaimer.
* Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the following disclaimer in the documentation and/or other materials provided with the distribution.
* Neither the name “Markdown” nor the names of its contributors may be used to endorse or promote products derived from this software without specific prior written permission.

This software is provided by the copyright holders and contributors “as is” and any express or implied warranties, including, but not limited to, the implied warranties of merchantability and fitness for a particular purpose are disclaimed. In no event shall the copyright owner or contributors be liable for any direct, indirect, incidental, special, exemplary, or consequential damages (including, but not limited to, procurement of substitute goods or services; loss of use, data, or profits; or business interruption) however caused and on any theory of liability, whether in contract, strict liability, or tort (including negligence or otherwise) arising in any way out of the use of this software, even if advised of the possibility of such damage.
````

<a id="license-dca9a392272606ac"></a>

### roughjs@4.6.6

Original: `package/LICENSE` in [roughjs@4.6.6](https://registry.npmjs.org/roughjs/-/roughjs-4.6.6.tgz).

````text
MIT License

Copyright (c) 2019 Preet Shihn

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
````

<a id="license-88c579c6ee44b411"></a>

### stylis@4.3.6

Original: `package/LICENSE` in [stylis@4.3.6](https://registry.npmjs.org/stylis/-/stylis-4.3.6.tgz).

````text
MIT License

Copyright (c) 2016-present Sultan Tarimo

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
````

<a id="license-eff13095d9d762c3"></a>

### ts-dedent@2.2.0

Original: `package/LICENSE` in [ts-dedent@2.2.0](https://registry.npmjs.org/ts-dedent/-/ts-dedent-2.2.0.tgz).

````text
MIT License

Copyright (c) 2018 Tamino Martinius

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
````

<a id="license-beaa6b04fb82e41d"></a>

### uuid@14.0.0

Original: `package/LICENSE.md` in [uuid@14.0.0](https://registry.npmjs.org/uuid/-/uuid-14.0.0.tgz).

````text
The MIT License (MIT)

Copyright (c) 2010-2020 Robert Kieffer and other contributors

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
````

<a id="license-ec9ee83580841e8e"></a>

### vscode-jsonrpc@8.2.0, vscode-languageserver-protocol@3.17.5, vscode-languageserver-types@3.17.5

Original: `package/License.txt` in [vscode-jsonrpc@8.2.0](https://registry.npmjs.org/vscode-jsonrpc/-/vscode-jsonrpc-8.2.0.tgz).

Original: `package/License.txt` in [vscode-languageserver-protocol@3.17.5](https://registry.npmjs.org/vscode-languageserver-protocol/-/vscode-languageserver-protocol-3.17.5.tgz).

Original: `package/License.txt` in [vscode-languageserver-types@3.17.5](https://registry.npmjs.org/vscode-languageserver-types/-/vscode-languageserver-types-3.17.5.tgz).

````text
Copyright (c) Microsoft Corporation

All rights reserved.

MIT License

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED *AS IS*, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
````

<a id="license-a89123562fe364dc"></a>

### vscode-jsonrpc@8.2.0, vscode-languageserver-types@3.17.5

Original: `package/thirdpartynotices.txt` in [vscode-jsonrpc@8.2.0](https://registry.npmjs.org/vscode-jsonrpc/-/vscode-jsonrpc-8.2.0.tgz).

Original: `package/thirdpartynotices.txt` in [vscode-languageserver-types@3.17.5](https://registry.npmjs.org/vscode-languageserver-types/-/vscode-languageserver-types-3.17.5.tgz).

````text
NOTICES AND INFORMATION
Do Not Translate or Localize

This software incorporates material from third parties.
Microsoft makes certain open source code available at https://3rdpartysource.microsoft.com,
or you may send a check or money order for US $5.00, including the product name,
the open source component name, platform, and version number, to:

Source Code Compliance Team
Microsoft Corporation
One Microsoft Way
Redmond, WA 98052
USA

Notwithstanding any other terms, you may reverse engineer this software to the extent
required to debug changes to any libraries licensed under the GNU Lesser General Public License.
````

<a id="license-9265d27cf75775aa"></a>

### vscode-languageserver-protocol@3.17.5

Original: `package/thirdpartynotices.txt` in [vscode-languageserver-protocol@3.17.5](https://registry.npmjs.org/vscode-languageserver-protocol/-/vscode-languageserver-protocol-3.17.5.tgz).

````text
THIRD-PARTY SOFTWARE NOTICES AND INFORMATION
For Microsoft vscode-languageclient

This project incorporates material from the project(s) listed below (collectively, “Third Party Code”).
Microsoft is not the original author of the Third Party Code.  The original copyright notice and license
under which Microsoft received such Third Party Code are set out below. This Third Party Code is licensed
to you under their original license terms set forth below.  Microsoft reserves all other rights not expressly
granted, whether by implication, estoppel or otherwise.

1.       DefinitelyTyped version 0.0.1 (https://github.com/borisyankov/DefinitelyTyped)

This project is licensed under the MIT license.
Copyrights are respective of each contributor listed at the beginning of each definition file.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
````

<a id="monaco-upstream-notices"></a>

## Monaco upstream notices

Original `package/ThirdPartyNotices.txt` from [monaco-editor@0.56.0](https://registry.npmjs.org/monaco-editor/-/monaco-editor-0.56.0.tgz), reproduced verbatim. Component version labels inside this upstream document are retained as supplied; they are not a separate version audit of those components.

````text
THIRD-PARTY SOFTWARE NOTICES AND INFORMATION
Do Not Translate or Localize

This project incorporates components from the projects listed below. The original copyright notices and the licenses
under which Microsoft received such components are set forth below. Microsoft reserves all rights not expressly granted
herein, whether by implication, estoppel or otherwise.



%% nodejs path library (https://github.com/nodejs/node/tree/43dd49c9782848c25e5b03448c8a0f923f13c158)
=========================================
Copyright Joyent, Inc. and other Node contributors.

Permission is hereby granted, free of charge, to any person obtaining a
copy of this software and associated documentation files (the
"Software"), to deal in the Software without restriction, including
without limitation the rights to use, copy, modify, merge, publish,
distribute, sublicense, and/or sell copies of the Software, and to permit
persons to whom the Software is furnished to do so, subject to the
following conditions:

The above copyright notice and this permission notice shall be included
in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN
NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE
USE OR OTHER DEALINGS IN THE SOFTWARE.
=========================================
END OF nodejs path library NOTICES AND INFORMATION




%% markedjs NOTICES AND INFORMATION BEGIN HERE
=========================================
The MIT License (MIT)

Copyright (c) 2018+, MarkedJS (https://github.com/markedjs/)
Copyright (c) 2011-2018, Christopher Jeffrey (https://github.com/chjj/)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
=========================================
END OF markedjs NOTICES AND INFORMATION



%% typescript version 4.4.4 (https://github.com/microsoft/TypeScript)
=========================================
Copyright (c) Microsoft Corporation. All rights reserved.

Apache License

Version 2.0, January 2004

http://www.apache.org/licenses/

TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION

1. Definitions.

"License" shall mean the terms and conditions for use, reproduction, and distribution as defined by Sections 1 through 9 of this document.

"Licensor" shall mean the copyright owner or entity authorized by the copyright owner that is granting the License.

"Legal Entity" shall mean the union of the acting entity and all other entities that control, are controlled by, or are under common control with that entity. For the purposes of this definition, "control" means (i) the power, direct or indirect, to cause the direction or management of such entity, whether by contract or otherwise, or (ii) ownership of fifty percent (50%) or more of the outstanding shares, or (iii) beneficial ownership of such entity.

"You" (or "Your") shall mean an individual or Legal Entity exercising permissions granted by this License.

"Source" form shall mean the preferred form for making modifications, including but not limited to software source code, documentation source, and configuration files.

"Object" form shall mean any form resulting from mechanical transformation or translation of a Source form, including but not limited to compiled object code, generated documentation, and conversions to other media types.

"Work" shall mean the work of authorship, whether in Source or Object form, made available under the License, as indicated by a copyright notice that is included in or attached to the work (an example is provided in the Appendix below).

"Derivative Works" shall mean any work, whether in Source or Object form, that is based on (or derived from) the Work and for which the editorial revisions, annotations, elaborations, or other modifications represent, as a whole, an original work of authorship. For the purposes of this License, Derivative Works shall not include works that remain separable from, or merely link (or bind by name) to the interfaces of, the Work and Derivative Works thereof.

"Contribution" shall mean any work of authorship, including the original version of the Work and any modifications or additions to that Work or Derivative Works thereof, that is intentionally submitted to Licensor for inclusion in the Work by the copyright owner or by an individual or Legal Entity authorized to submit on behalf of the copyright owner. For the purposes of this definition, "submitted" means any form of electronic, verbal, or written communication sent to the Licensor or its representatives, including but not limited to communication on electronic mailing lists, source code control systems, and issue tracking systems that are managed by, or on behalf of, the Licensor for the purpose of discussing and improving the Work, but excluding communication that is conspicuously marked or otherwise designated in writing by the copyright owner as "Not a Contribution."

"Contributor" shall mean Licensor and any individual or Legal Entity on behalf of whom a Contribution has been received by Licensor and subsequently incorporated within the Work.

2. Grant of Copyright License. Subject to the terms and conditions of this License, each Contributor hereby grants to You a perpetual, worldwide, non-exclusive, no-charge, royalty-free, irrevocable copyright license to reproduce, prepare Derivative Works of, publicly display, publicly perform, sublicense, and distribute the Work and such Derivative Works in Source or Object form.

3. Grant of Patent License. Subject to the terms and conditions of this License, each Contributor hereby grants to You a perpetual, worldwide, non-exclusive, no-charge, royalty-free, irrevocable (except as stated in this section) patent license to make, have made, use, offer to sell, sell, import, and otherwise transfer the Work, where such license applies only to those patent claims licensable by such Contributor that are necessarily infringed by their Contribution(s) alone or by combination of their Contribution(s) with the Work to which such Contribution(s) was submitted. If You institute patent litigation against any entity (including a cross-claim or counterclaim in a lawsuit) alleging that the Work or a Contribution incorporated within the Work constitutes direct or contributory patent infringement, then any patent licenses granted to You under this License for that Work shall terminate as of the date such litigation is filed.

4. Redistribution. You may reproduce and distribute copies of the Work or Derivative Works thereof in any medium, with or without modifications, and in Source or Object form, provided that You meet the following conditions:

You must give any other recipients of the Work or Derivative Works a copy of this License; and

You must cause any modified files to carry prominent notices stating that You changed the files; and

You must retain, in the Source form of any Derivative Works that You distribute, all copyright, patent, trademark, and attribution notices from the Source form of the Work, excluding those notices that do not pertain to any part of the Derivative Works; and

If the Work includes a "NOTICE" text file as part of its distribution, then any Derivative Works that You distribute must include a readable copy of the attribution notices contained within such NOTICE file, excluding those notices that do not pertain to any part of the Derivative Works, in at least one of the following places: within a NOTICE text file distributed as part of the Derivative Works; within the Source form or documentation, if provided along with the Derivative Works; or, within a display generated by the Derivative Works, if and wherever such third-party notices normally appear. The contents of the NOTICE file are for informational purposes only and do not modify the License. You may add Your own attribution notices within Derivative Works that You distribute, alongside or as an addendum to the NOTICE text from the Work, provided that such additional attribution notices cannot be construed as modifying the License. You may add Your own copyright statement to Your modifications and may provide additional or different license terms and conditions for use, reproduction, or distribution of Your modifications, or for any such Derivative Works as a whole, provided Your use, reproduction, and distribution of the Work otherwise complies with the conditions stated in this License.

5. Submission of Contributions. Unless You explicitly state otherwise, any Contribution intentionally submitted for inclusion in the Work by You to the Licensor shall be under the terms and conditions of this License, without any additional terms or conditions. Notwithstanding the above, nothing herein shall supersede or modify the terms of any separate license agreement you may have executed with Licensor regarding such Contributions.

6. Trademarks. This License does not grant permission to use the trade names, trademarks, service marks, or product names of the Licensor, except as required for reasonable and customary use in describing the origin of the Work and reproducing the content of the NOTICE file.

7. Disclaimer of Warranty. Unless required by applicable law or agreed to in writing, Licensor provides the Work (and each Contributor provides its Contributions) on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied, including, without limitation, any warranties or conditions of TITLE, NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A PARTICULAR PURPOSE. You are solely responsible for determining the appropriateness of using or redistributing the Work and assume any risks associated with Your exercise of permissions under this License.

8. Limitation of Liability. In no event and under no legal theory, whether in tort (including negligence), contract, or otherwise, unless required by applicable law (such as deliberate and grossly negligent acts) or agreed to in writing, shall any Contributor be liable to You for damages, including any direct, indirect, special, incidental, or consequential damages of any character arising as a result of this License or out of the use or inability to use the Work (including but not limited to damages for loss of goodwill, work stoppage, computer failure or malfunction, or any and all other commercial damages or losses), even if such Contributor has been advised of the possibility of such damages.

9. Accepting Warranty or Additional Liability. While redistributing the Work or Derivative Works thereof, You may choose to offer, and charge a fee for, acceptance of support, warranty, indemnity, or other liability obligations and/or rights consistent with this License. However, in accepting such obligations, You may act only on Your own behalf and on Your sole responsibility, not on behalf of any other Contributor, and only if You agree to indemnify, defend, and hold each Contributor harmless for any liability incurred by, or claims asserted against, such Contributor by reason of your accepting any such warranty or additional liability.

END OF TERMS AND CONDITIONS


The TypeScript software incorporates third party material from the projects listed below. The original copyright notice and the license under which Microsoft received such third party material are set forth below. Microsoft reserves all other rights not expressly granted, whether by implication, estoppel or otherwise.


------------------- DefinitelyTyped --------------------
This file is based on or incorporates material from the projects listed below (collectively "Third Party Code"). Microsoft is not the original author of the Third Party Code. The original copyright notice and the license, under which Microsoft received such Third Party Code, are set forth below. Such licenses and notices are provided for informational purposes only. Microsoft, not the third party, licenses the Third Party Code to you under the terms set forth in the EULA for the Microsoft Product. Microsoft reserves all other rights not expressly granted under this agreement, whether by implication, estoppel or otherwise.
DefinitelyTyped
This project is licensed under the MIT license. Copyrights are respective of each contributor listed at the beginning of each definition file. Provided for Informational Purposes Only

MIT License
Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the ""Software""), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
--------------------------------------------------------------------------------------

------------------- Unicode --------------------
UNICODE, INC. LICENSE AGREEMENT - DATA FILES AND SOFTWARE

Unicode Data Files include all data files under the directories
http://www.unicode.org/Public/, http://www.unicode.org/reports/,
http://www.unicode.org/cldr/data/, http://source.icu-project.org/repos/icu/, and
http://www.unicode.org/utility/trac/browser/.

Unicode Data Files do not include PDF online code charts under the
directory http://www.unicode.org/Public/.

Software includes any source code published in the Unicode Standard
or under the directories
http://www.unicode.org/Public/, http://www.unicode.org/reports/,
http://www.unicode.org/cldr/data/, http://source.icu-project.org/repos/icu/, and
http://www.unicode.org/utility/trac/browser/.

NOTICE TO USER: Carefully read the following legal agreement.
BY DOWNLOADING, INSTALLING, COPYING OR OTHERWISE USING UNICODE INC.'S
DATA FILES ("DATA FILES"), AND/OR SOFTWARE ("SOFTWARE"),
YOU UNEQUIVOCALLY ACCEPT, AND AGREE TO BE BOUND BY, ALL OF THE
TERMS AND CONDITIONS OF THIS AGREEMENT.
IF YOU DO NOT AGREE, DO NOT DOWNLOAD, INSTALL, COPY, DISTRIBUTE OR USE
THE DATA FILES OR SOFTWARE.

COPYRIGHT AND PERMISSION NOTICE

Copyright (c) 1991-2017 Unicode, Inc. All rights reserved.
Distributed under the Terms of Use in http://www.unicode.org/copyright.html.

Permission is hereby granted, free of charge, to any person obtaining
a copy of the Unicode data files and any associated documentation
(the "Data Files") or Unicode software and any associated documentation
(the "Software") to deal in the Data Files or Software
without restriction, including without limitation the rights to use,
copy, modify, merge, publish, distribute, and/or sell copies of
the Data Files or Software, and to permit persons to whom the Data Files
or Software are furnished to do so, provided that either
(a) this copyright and permission notice appear with all copies
of the Data Files or Software, or
(b) this copyright and permission notice appear in associated
Documentation.

THE DATA FILES AND SOFTWARE ARE PROVIDED "AS IS", WITHOUT WARRANTY OF
ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE
WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT OF THIRD PARTY RIGHTS.
IN NO EVENT SHALL THE COPYRIGHT HOLDER OR HOLDERS INCLUDED IN THIS
NOTICE BE LIABLE FOR ANY CLAIM, OR ANY SPECIAL INDIRECT OR CONSEQUENTIAL
DAMAGES, OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS OF USE,
DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER
TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR
PERFORMANCE OF THE DATA FILES OR SOFTWARE.

Except as contained in this notice, the name of a copyright holder
shall not be used in advertising or otherwise to promote the sale,
use or other dealings in these Data Files or Software without prior
written authorization of the copyright holder.
-------------------------------------------------------------------------------------

-------------------Document Object Model-----------------------------
DOM

W3C License
This work is being provided by the copyright holders under the following license.
By obtaining and/or copying this work, you (the licensee) agree that you have read, understood, and will comply with the following terms and conditions.
Permission to copy, modify, and distribute this work, with or without modification, for any purpose and without fee or royalty is hereby granted, provided that you include the following
on ALL copies of the work or portions thereof, including modifications:
* The full text of this NOTICE in a location viewable to users of the redistributed or derivative work.
* Any pre-existing intellectual property disclaimers, notices, or terms and conditions. If none exist, the W3C Software and Document Short Notice should be included.
* Notice of any changes or modifications, through a copyright statement on the new code or document such as "This software or document includes material copied from or derived
from [title and URI of the W3C document]. Copyright © [YEAR] W3C® (MIT, ERCIM, Keio, Beihang)."
Disclaimers
THIS WORK IS PROVIDED "AS IS," AND COPYRIGHT HOLDERS MAKE NO REPRESENTATIONS OR WARRANTIES, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO, WARRANTIES OF MERCHANTABILITY OR
FITNESS FOR ANY PARTICULAR PURPOSE OR THAT THE USE OF THE SOFTWARE OR DOCUMENT WILL NOT INFRINGE ANY THIRD PARTY PATENTS, COPYRIGHTS, TRADEMARKS OR OTHER RIGHTS.
COPYRIGHT HOLDERS WILL NOT BE LIABLE FOR ANY DIRECT, INDIRECT, SPECIAL OR CONSEQUENTIAL DAMAGES ARISING OUT OF ANY USE OF THE SOFTWARE OR DOCUMENT.
The name and trademarks of copyright holders may NOT be used in advertising or publicity pertaining to the work without specific, written prior permission.
Title to copyright in this work will at all times remain with copyright holders.

---------

DOM
Copyright © 2018 WHATWG (Apple, Google, Mozilla, Microsoft). This work is licensed under a Creative Commons Attribution 4.0 International License: Attribution 4.0 International
=======================================================================
Creative Commons Corporation ("Creative Commons") is not a law firm and does not provide legal services or legal advice. Distribution of Creative Commons public licenses does not create a lawyer-client or other relationship. Creative Commons makes its licenses and related information available on an "as-is" basis. Creative Commons gives no warranties regarding its licenses, any material licensed under their terms and conditions, or any related information. Creative Commons disclaims all liability for damages resulting from their use to the fullest extent possible. Using Creative Commons Public Licenses Creative Commons public licenses provide a standard set of terms and conditions that creators and other rights holders may use to share original works of authorship and other material subject to copyright and certain other rights specified in the public license below. The following considerations are for informational purposes only, are not exhaustive, and do not form part of our licenses. Considerations for licensors: Our public licenses are intended for use by those authorized to give the public permission to use material in ways otherwise restricted by copyright and certain other rights. Our licenses are irrevocable. Licensors should read and understand the terms and conditions of the license they choose before applying it. Licensors should also secure all rights necessary before applying our licenses so that the public can reuse the material as expected. Licensors should clearly mark any material not subject to the license. This includes other CC- licensed material, or material used under an exception or limitation to copyright. More considerations for licensors:

wiki.creativecommons.org/Considerations_for_licensors Considerations for the public: By using one of our public licenses, a licensor grants the public permission to use the licensed material under specified terms and conditions. If the licensor's permission is not necessary for any reason--for example, because of any applicable exception or limitation to copyright--then that use is not regulated by the license. Our licenses grant only permissions under copyright and certain other rights that a licensor has authority to grant. Use of the licensed material may still be restricted for other reasons, including because others have copyright or other rights in the material. A licensor may make special requests, such as asking that all changes be marked or described. Although not required by our licenses, you are encouraged to respect those requests where reasonable. More_considerations for the public: wiki.creativecommons.org/Considerations_for_licensees =======================================================================
Creative Commons Attribution 4.0 International Public License By exercising the Licensed Rights (defined below), You accept and agree to be bound by the terms and conditions of this Creative Commons Attribution 4.0 International Public License ("Public License"). To the extent this Public License may be interpreted as a contract, You are granted the Licensed Rights in consideration of Your acceptance of these terms and conditions, and the Licensor grants You such rights in consideration of benefits the Licensor receives from making the Licensed Material available under these terms and conditions. Section 1 -- Definitions. a. Adapted Material means material subject to Copyright and Similar Rights that is derived from or based upon the Licensed Material and in which the Licensed Material is translated, altered, arranged, transformed, or otherwise modified in a manner requiring permission under the Copyright and Similar Rights held by the Licensor. For purposes of this Public License, where the Licensed Material is a musical work, performance, or sound recording, Adapted Material is always produced where the Licensed Material is synched in timed relation with a moving image. b. Adapter's License means the license You apply to Your Copyright and Similar Rights in Your contributions to Adapted Material in accordance with the terms and conditions of this Public License. c. Copyright and Similar Rights means copyright and/or similar rights closely related to copyright including, without limitation, performance, broadcast, sound recording, and Sui Generis Database Rights, without regard to how the rights are labeled or categorized. For purposes of this Public License, the rights specified in Section 2(b)(1)-(2) are not Copyright and Similar Rights. d. Effective Technological Measures means those measures that, in the absence of proper authority, may not be circumvented under laws fulfilling obligations under Article 11 of the WIPO Copyright Treaty adopted on December 20, 1996, and/or similar international agreements. e. Exceptions and Limitations means fair use, fair dealing, and/or any other exception or limitation to Copyright and Similar Rights that applies to Your use of the Licensed Material. f. Licensed Material means the artistic or literary work, database, or other material to which the Licensor applied this Public License. g. Licensed Rights means the rights granted to You subject to the terms and conditions of this Public License, which are limited to all Copyright and Similar Rights that apply to Your use of the Licensed Material and that the Licensor has authority to license. h. Licensor means the individual(s) or entity(ies) granting rights under this Public License. i. Share means to provide material to the public by any means or process that requires permission under the Licensed Rights, such as reproduction, public display, public performance, distribution, dissemination, communication, or importation, and to make material available to the public including in ways that members of the public may access the material from a place and at a time individually chosen by them. j. Sui Generis Database Rights means rights other than copyright resulting from Directive 96/9/EC of the European Parliament and of the Council of 11 March 1996 on the legal protection of databases, as amended and/or succeeded, as well as other essentially equivalent rights anywhere in the world. k. You means the individual or entity exercising the Licensed Rights under this Public License. Your has a corresponding meaning. Section 2 -- Scope. a. License grant. 1. Subject to the terms and conditions of this Public License, the Licensor hereby grants You a worldwide, royalty-free, non-sublicensable, non-exclusive, irrevocable license to exercise the Licensed Rights in the Licensed Material to: a. reproduce and Share the Licensed Material, in whole or in part; and b. produce, reproduce, and Share Adapted Material. 2. Exceptions and Limitations. For the avoidance of doubt, where Exceptions and Limitations apply to Your use, this Public License does not apply, and You do not need to comply with its terms and conditions. 3. Term. The term of this Public License is specified in Section 6(a). 4. Media and formats; technical modifications allowed. The Licensor authorizes You to exercise the Licensed Rights in all media and formats whether now known or hereafter created, and to make technical modifications necessary to do so. The Licensor waives and/or agrees not to assert any right or authority to forbid You from making technical modifications necessary to exercise the Licensed Rights, including technical modifications necessary to circumvent Effective Technological Measures. For purposes of this Public License, simply making modifications authorized by this Section 2(a) (4) never produces Adapted Material. 5. Downstream recipients. a. Offer from the Licensor -- Licensed Material. Every recipient of the Licensed Material automatically receives an offer from the Licensor to exercise the Licensed Rights under the terms and conditions of this Public License. b. No downstream restrictions. You may not offer or impose any additional or different terms or conditions on, or apply any Effective Technological Measures to, the Licensed Material if doing so restricts exercise of the Licensed Rights by any recipient of the Licensed Material. 6. No endorsement. Nothing in this Public License constitutes or may be construed as permission to assert or imply that You are, or that Your use of the Licensed Material is, connected with, or sponsored, endorsed, or granted official status by, the Licensor or others designated to receive attribution as provided in Section 3(a)(1)(A)(i). b. Other rights. 1. Moral rights, such as the right of integrity, are not licensed under this Public License, nor are publicity, privacy, and/or other similar personality rights; however, to the extent possible, the Licensor waives and/or agrees not to assert any such rights held by the Licensor to the limited extent necessary to allow You to exercise the Licensed Rights, but not otherwise. 2. Patent and trademark rights are not licensed under this Public License. 3. To the extent possible, the Licensor waives any right to collect royalties from You for the exercise of the Licensed Rights, whether directly or through a collecting society under any voluntary or waivable statutory or compulsory licensing scheme. In all other cases the Licensor expressly reserves any right to collect such royalties. Section 3 -- License Conditions. Your exercise of the Licensed Rights is expressly made subject to the following conditions. a. Attribution. 1. If You Share the Licensed Material (including in modified form), You must: a. retain the following if it is supplied by the Licensor with the Licensed Material: i. identification of the creator(s) of the Licensed Material and any others designated to receive attribution, in any reasonable manner requested by the Licensor (including by pseudonym if designated); ii. a copyright notice; iii. a notice that refers to this Public License; iv. a notice that refers to the disclaimer of warranties; v. a URI or hyperlink to the Licensed Material to the extent reasonably practicable; b. indicate if You modified the Licensed Material and retain an indication of any previous modifications; and c. indicate the Licensed Material is licensed under this Public License, and include the text of, or the URI or hyperlink to, this Public License. 2. You may satisfy the conditions in Section 3(a)(1) in any reasonable manner based on the medium, means, and context in which You Share the Licensed Material. For example, it may be reasonable to satisfy the conditions by providing a URI or hyperlink to a resource that includes the required information. 3. If requested by the Licensor, You must remove any of the information required by Section 3(a)(1)(A) to the extent reasonably practicable. 4. If You Share Adapted Material You produce, the Adapter's License You apply must not prevent recipients of the Adapted Material from complying with this Public License. Section 4 -- Sui Generis Database Rights. Where the Licensed Rights include Sui Generis Database Rights that apply to Your use of the Licensed Material: a. for the avoidance of doubt, Section 2(a)(1) grants You the right to extract, reuse, reproduce, and Share all or a substantial portion of the contents of the database; b. if You include all or a substantial portion of the database contents in a database in which You have Sui Generis Database Rights, then the database in which You have Sui Generis Database Rights (but not its individual contents) is Adapted Material; and c. You must comply with the conditions in Section 3(a) if You Share all or a substantial portion of the contents of the database. For the avoidance of doubt, this Section 4 supplements and does not replace Your obligations under this Public License where the Licensed Rights include other Copyright and Similar Rights. Section 5 -- Disclaimer of Warranties and Limitation of Liability. a. UNLESS OTHERWISE SEPARATELY UNDERTAKEN BY THE LICENSOR, TO THE EXTENT POSSIBLE, THE LICENSOR OFFERS THE LICENSED MATERIAL AS-IS AND AS-AVAILABLE, AND MAKES NO REPRESENTATIONS OR WARRANTIES OF ANY KIND CONCERNING THE LICENSED MATERIAL, WHETHER EXPRESS, IMPLIED, STATUTORY, OR OTHER. THIS INCLUDES, WITHOUT LIMITATION, WARRANTIES OF TITLE, MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, NON-INFRINGEMENT, ABSENCE OF LATENT OR OTHER DEFECTS, ACCURACY, OR THE PRESENCE OR ABSENCE OF ERRORS, WHETHER OR NOT KNOWN OR DISCOVERABLE. WHERE DISCLAIMERS OF WARRANTIES ARE NOT ALLOWED IN FULL OR IN PART, THIS DISCLAIMER MAY NOT APPLY TO YOU. b. TO THE EXTENT POSSIBLE, IN NO EVENT WILL THE LICENSOR BE LIABLE TO YOU ON ANY LEGAL THEORY (INCLUDING, WITHOUT LIMITATION, NEGLIGENCE) OR OTHERWISE FOR ANY DIRECT, SPECIAL, INDIRECT, INCIDENTAL, CONSEQUENTIAL, PUNITIVE, EXEMPLARY, OR OTHER LOSSES, COSTS, EXPENSES, OR DAMAGES ARISING OUT OF THIS PUBLIC LICENSE OR USE OF THE LICENSED MATERIAL, EVEN IF THE LICENSOR HAS BEEN ADVISED OF THE POSSIBILITY OF SUCH LOSSES, COSTS, EXPENSES, OR DAMAGES. WHERE A LIMITATION OF LIABILITY IS NOT ALLOWED IN FULL OR IN PART, THIS LIMITATION MAY NOT APPLY TO YOU. c. The disclaimer of warranties and limitation of liability provided above shall be interpreted in a manner that, to the extent possible, most closely approximates an absolute disclaimer and waiver of all liability. Section 6 -- Term and Termination. a. This Public License applies for the term of the Copyright and Similar Rights licensed here. However, if You fail to comply with this Public License, then Your rights under this Public License terminate automatically. b. Where Your right to use the Licensed Material has terminated under Section 6(a), it reinstates: 1. automatically as of the date the violation is cured, provided it is cured within 30 days of Your discovery of the violation; or 2. upon express reinstatement by the Licensor. For the avoidance of doubt, this Section 6(b) does not affect any right the Licensor may have to seek remedies for Your violations of this Public License. c. For the avoidance of doubt, the Licensor may also offer the Licensed Material under separate terms or conditions or stop distributing the Licensed Material at any time; however, doing so will not terminate this Public License. d. Sections 1, 5, 6, 7, and 8 survive termination of this Public License. Section 7 -- Other Terms and Conditions. a. The Licensor shall not be bound by any additional or different terms or conditions communicated by You unless expressly agreed. b. Any arrangements, understandings, or agreements regarding the Licensed Material not stated herein are separate from and independent of the terms and conditions of this Public License. Section 8 -- Interpretation. a. For the avoidance of doubt, this Public License does not, and shall not be interpreted to, reduce, limit, restrict, or impose conditions on any use of the Licensed Material that could lawfully be made without permission under this Public License. b. To the extent possible, if any provision of this Public License is deemed unenforceable, it shall be automatically reformed to the minimum extent necessary to make it enforceable. If the provision cannot be reformed, it shall be severed from this Public License without affecting the enforceability of the remaining terms and conditions. c. No term or condition of this Public License will be waived and no failure to comply consented to unless expressly agreed to by the Licensor. d. Nothing in this Public License constitutes or may be interpreted as a limitation upon, or waiver of, any privileges and immunities that apply to the Licensor or You, including from the legal processes of any jurisdiction or authority. ======================================================================= Creative Commons is not a party to its public licenses. Notwithstanding, Creative Commons may elect to apply one of its public licenses to material it publishes and in those instances will be considered the "Licensor." Except for the limited purpose of indicating that material is shared under a Creative Commons public license or as otherwise permitted by the Creative Commons policies published at creativecommons.org/policies, Creative Commons does not authorize the use of the trademark "Creative Commons" or any other trademark or logo of Creative Commons without its prior written consent including, without limitation, in connection with any unauthorized modifications to any of its public licenses or any other arrangements, understandings, or agreements concerning use of licensed material. For the avoidance of doubt, this paragraph does not form part of the public licenses. Creative Commons may be contacted at creativecommons.org.

--------------------------------------------------------------------------------

----------------------Web Background Synchronization------------------------------

Web Background Synchronization Specification
Portions of spec © by W3C

W3C Community Final Specification Agreement
To secure commitments from participants for the full text of a Community or Business Group Report, the group may call for voluntary commitments to the following terms; a "summary" is
available. See also the related "W3C Community Contributor License Agreement".
1. The Purpose of this Agreement.
This Agreement sets forth the terms under which I make certain copyright and patent rights available to you for your implementation of the Specification.
Any other capitalized terms not specifically defined herein have the same meaning as those terms have in the "W3C Patent Policy", and if not defined there, in the "W3C Process Document".
2. Copyrights.
2.1. Copyright Grant. I grant to you a perpetual (for the duration of the applicable copyright), worldwide, non-exclusive, no-charge, royalty-free, copyright license, without any obligation for accounting to me, to reproduce, prepare derivative works of, publicly display, publicly perform, sublicense, distribute, and implement the Specification to the full extent of my copyright interest in the Specification.
2.2. Attribution. As a condition of the copyright grant, you must include an attribution to the Specification in any derivative work you make based on the Specification. That attribution must include, at minimum, the Specification name and version number.
3. Patents.
3.1. Patent Licensing Commitment. I agree to license my Essential Claims under the W3C Community RF Licensing Requirements. This requirement includes Essential Claims that I own and any that I have the right to license without obligation of payment or other consideration to an unrelated third party. W3C Community RF Licensing Requirements obligations made concerning the Specification and described in this policy are binding on me for the life of the patents in question and encumber the patents containing Essential Claims, regardless of changes in participation status or W3C Membership. I also agree to license my Essential Claims under the W3C Community RF Licensing Requirements in derivative works of the Specification so long as all normative portions of the Specification are maintained and that this licensing commitment does not extend to any portion of the derivative work that was not included in the Specification.
3.2. Optional, Additional Patent Grant. In addition to the provisions of Section 3.1, I may also, at my option, make certain intellectual property rights infringed by implementations of the Specification, including Essential Claims, available by providing those terms via the W3C Web site.
4. No Other Rights. Except as specifically set forth in this Agreement, no other express or implied patent, trademark, copyright, or other property rights are granted under this Agreement, including by implication, waiver, or estoppel.
5. Antitrust Compliance. I acknowledge that I may compete with other participants, that I am under no obligation to implement the Specification, that each participant is free to develop competing technologies and standards, and that each party is free to license its patent rights to third parties, including for the purpose of enabling competing technologies and standards.
6. Non-Circumvention. I agree that I will not intentionally take or willfully assist any third party to take any action for the purpose of circumventing my obligations under this Agreement.
7. Transition to W3C Recommendation Track. The Specification developed by the Project may transition to the W3C Recommendation Track. The W3C Team is responsible for notifying me that a Corresponding Working Group has been chartered. I have no obligation to join the Corresponding Working Group. If the Specification developed by the Project transitions to the W3C Recommendation Track, the following terms apply:
7.1. If I join the Corresponding Working Group. If I join the Corresponding Working Group, I will be subject to all W3C rules, obligations, licensing commitments, and policies that govern that Corresponding Working Group.
7.2. If I Do Not Join the Corresponding Working Group.
7.2.1. Licensing Obligations to Resulting Specification. If I do not join the Corresponding Working Group, I agree to offer patent licenses according to the W3C Royalty-Free licensing requirements described in Section 5 of the W3C Patent Policy for the portions of the Specification included in the resulting Recommendation. This licensing commitment does not extend to any portion of an implementation of the Recommendation that was not included in the Specification. This licensing commitment may not be revoked but may be modified through the exclusion process defined in Section 4 of the W3C Patent Policy. I am not required to join the Corresponding Working Group to exclude patents from the W3C Royalty-Free licensing commitment, but must otherwise follow the normal exclusion procedures defined by the W3C Patent Policy. The W3C Team will notify me of any Call for Exclusion in the Corresponding Working Group as set forth in Section 4.5 of the W3C Patent Policy.
7.2.2. No Disclosure Obligation. If I do not join the Corresponding Working Group, I have no patent disclosure obligations outside of those set forth in Section 6 of the W3C Patent Policy.
8. Conflict of Interest. I will disclose significant relationships when those relationships might reasonably be perceived as creating a conflict of interest with my role. I will notify W3C of any change in my affiliation using W3C-provided mechanisms.
9. Representations, Warranties and Disclaimers. I represent and warrant that I am legally entitled to grant the rights and promises set forth in this Agreement. IN ALL OTHER RESPECTS THE SPECIFICATION IS PROVIDED AS IS. The entire risk as to implementing or otherwise using the Specification is assumed by the implementer and user. Except as stated herein, I expressly disclaim any warranties (express, implied, or otherwise), including implied warranties of merchantability, non-infringement, fitness for a particular purpose, or title, related to the Specification. IN NO EVENT WILL ANY PARTY BE LIABLE TO ANY OTHER PARTY FOR LOST PROFITS OR ANY FORM OF INDIRECT, SPECIAL, INCIDENTAL, OR CONSEQUENTIAL DAMAGES OF ANY CHARACTER FROM ANY CAUSES OF ACTION OF ANY KIND WITH RESPECT TO THIS AGREEMENT, WHETHER BASED ON BREACH OF CONTRACT, TORT (INCLUDING NEGLIGENCE), OR OTHERWISE, AND WHETHER OR NOT THE OTHER PARTY HAS BEEN ADVISED OF THE POSSIBILITY OF SUCH DAMAGE. All of my obligations under Section 3 regarding the transfer, successors in interest, or assignment of Granted Claims will be satisfied if I notify the transferee or assignee of any patent that I know contains Granted Claims of the obligations under Section 3. Nothing in this Agreement requires me to undertake a patent search.
10. Definitions.
10.1. Agreement. Agreement means this W3C Community Final Specification Agreement.
10.2. Corresponding Working Group. Corresponding Working Group is a W3C Working Group that is chartered to develop a Recommendation, as defined in the W3C Process Document, that takes the Specification as an input.
10.3. Essential Claims. Essential Claims shall mean all claims in any patent or patent application in any jurisdiction in the world that would necessarily be infringed by implementation of the Specification. A claim is necessarily infringed hereunder only when it is not possible to avoid infringing it because there is no non-infringing alternative for implementing the normative portions of the Specification. Existence of a non-infringing alternative shall be judged based on the state of the art at the time of the publication of the Specification. The following are expressly excluded from and shall not be deemed to constitute Essential Claims:
10.3.1. any claims other than as set forth above even if contained in the same patent as Essential Claims; and
10.3.2. claims which would be infringed only by:
portions of an implementation that are not specified in the normative portions of the Specification, or
enabling technologies that may be necessary to make or use any product or portion thereof that complies with the Specification and are not themselves expressly set forth in the Specification (e.g., semiconductor manufacturing technology, compiler technology, object-oriented technology, basic operating system technology, and the like); or
the implementation of technology developed elsewhere and merely incorporated by reference in the body of the Specification.
10.3.3. design patents and design registrations.
For purposes of this definition, the normative portions of the Specification shall be deemed to include only architectural and interoperability requirements. Optional features in the RFC 2119 sense are considered normative unless they are specifically identified as informative. Implementation examples or any other material that merely illustrate the requirements of the Specification are informative, rather than normative.
10.4. I, Me, or My. I, me, or my refers to the signatory.
10.5 Project. Project means the W3C Community Group or Business Group for which I executed this Agreement.
10.6. Specification. Specification means the Specification identified by the Project as the target of this agreement in a call for Final Specification Commitments. W3C shall provide the authoritative mechanisms for the identification of this Specification.
10.7. W3C Community RF Licensing Requirements. W3C Community RF Licensing Requirements license shall mean a non-assignable, non-sublicensable license to make, have made, use, sell, have sold, offer to sell, import, and distribute and dispose of implementations of the Specification that:
10.7.1. shall be available to all, worldwide, whether or not they are W3C Members;
10.7.2. shall extend to all Essential Claims owned or controlled by me;
10.7.3. may be limited to implementations of the Specification, and to what is required by the Specification;
10.7.4. may be conditioned on a grant of a reciprocal RF license (as defined in this policy) to all Essential Claims owned or controlled by the licensee. A reciprocal license may be required to be available to all, and a reciprocal license may itself be conditioned on a further reciprocal license from all.
10.7.5. may not be conditioned on payment of royalties, fees or other consideration;
10.7.6. may be suspended with respect to any licensee when licensor issued by licensee for infringement of claims essential to implement the Specification or any W3C Recommendation;
10.7.7. may not impose any further conditions or restrictions on the use of any technology, intellectual property rights, or other restrictions on behavior of the licensee, but may include reasonable, customary terms relating to operation or maintenance of the license relationship such as the following: choice of law and dispute resolution;
10.7.8. shall not be considered accepted by an implementer who manifests an intent not to accept the terms of the W3C Community RF Licensing Requirements license as offered by the licensor.
10.7.9. The RF license conforming to the requirements in this policy shall be made available by the licensor as long as the Specification is in effect. The term of such license shall be for the life of the patents in question.
I am encouraged to provide a contact from which licensing information can be obtained and other relevant licensing information. Any such information will be made publicly available.
10.8. You or Your. You, you, or your means any person or entity who exercises copyright or patent rights granted under this Agreement, and any person that person or entity controls.

-------------------------------------------------------------------------------------

------------------- WebGL -----------------------------
Copyright (c) 2018 The Khronos Group Inc.

Permission is hereby granted, free of charge, to any person obtaining a
copy of this software and/or associated documentation files (the
"Materials"), to deal in the Materials without restriction, including
without limitation the rights to use, copy, modify, merge, publish,
distribute, sublicense, and/or sell copies of the Materials, and to
permit persons to whom the Materials are furnished to do so, subject to
the following conditions:

The above copyright notice and this permission notice shall be included
in all copies or substantial portions of the Materials.

THE MATERIALS ARE PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
MATERIALS OR THE USE OR OTHER DEALINGS IN THE MATERIALS.
------------------------------------------------------


=========================================
END OF typescript NOTICES AND INFORMATION




%% HTML 5.1 W3C Working Draft version 08 October 2015 (http://www.w3.org/TR/2015/WD-html51-20151008/)
=========================================
Copyright © 2015 W3C® (MIT, ERCIM, Keio, Beihang). This software or document includes material copied
from or derived from HTML 5.1 W3C Working Draft (http://www.w3.org/TR/2015/WD-html51-20151008/.)

THIS DOCUMENT IS PROVIDED "AS IS," AND COPYRIGHT HOLDERS MAKE NO REPRESENTATIONS OR WARRANTIES, EXPRESS OR IMPLIED, INCLUDING, BUT
NOT LIMITED TO, WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, NON-INFRINGEMENT, OR TITLE; THAT THE CONTENTS OF
THE DOCUMENT ARE SUITABLE FOR ANY PURPOSE; NOR THAT THE IMPLEMENTATION OF SUCH CONTENTS WILL NOT INFRINGE ANY THIRD PARTY
PATENTS, COPYRIGHTS, TRADEMARKS OR OTHER RIGHTS.

COPYRIGHT HOLDERS WILL NOT BE LIABLE FOR ANY DIRECT, INDIRECT, SPECIAL OR CONSEQUENTIAL DAMAGES ARISING OUT OF ANY USE OF THE
DOCUMENT OR THE PERFORMANCE OR IMPLEMENTATION OF THE CONTENTS THEREOF.

The name and trademarks of copyright holders may NOT be used in advertising or publicity pertaining to this document or its contents
without specific, written prior permission. Title to copyright in this document will at all times remain with copyright holders.
=========================================
END OF HTML 5.1 W3C Working Draft NOTICES AND INFORMATION




%% JS Beautifier version 1.6.2 (https://github.com/beautify-web/js-beautify)
=========================================
The MIT License (MIT)

Copyright (c) 2007-2017 Einar Lielmanis, Liam Newman, and contributors.

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
=========================================
END OF js-beautify NOTICES AND INFORMATION




%% Ionic documentation version 1.2.4 (https://github.com/driftyco/ionic-site)
=========================================
Copyright Drifty Co. http://drifty.com/.

Apache License

Version 2.0, January 2004

http://www.apache.org/licenses/

TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION

1. Definitions.

"License" shall mean the terms and conditions for use, reproduction, and distribution as defined by Sections 1 through 9 of this document.

"Licensor" shall mean the copyright owner or entity authorized by the copyright owner that is granting the License.

"Legal Entity" shall mean the union of the acting entity and all other entities that control, are controlled by, or are under common control with that entity. For the purposes of this definition, "control" means (i) the power, direct or indirect, to cause the direction or management of such entity, whether by contract or otherwise, or (ii) ownership of fifty percent (50%) or more of the outstanding shares, or (iii) beneficial ownership of such entity.

"You" (or "Your") shall mean an individual or Legal Entity exercising permissions granted by this License.

"Source" form shall mean the preferred form for making modifications, including but not limited to software source code, documentation source, and configuration files.

"Object" form shall mean any form resulting from mechanical transformation or translation of a Source form, including but not limited to compiled object code, generated documentation, and conversions to other media types.

"Work" shall mean the work of authorship, whether in Source or Object form, made available under the License, as indicated by a copyright notice that is included in or attached to the work (an example is provided in the Appendix below).

"Derivative Works" shall mean any work, whether in Source or Object form, that is based on (or derived from) the Work and for which the editorial revisions, annotations, elaborations, or other modifications represent, as a whole, an original work of authorship. For the purposes of this License, Derivative Works shall not include works that remain separable from, or merely link (or bind by name) to the interfaces of, the Work and Derivative Works thereof.

"Contribution" shall mean any work of authorship, including the original version of the Work and any modifications or additions to that Work or Derivative Works thereof, that is intentionally submitted to Licensor for inclusion in the Work by the copyright owner or by an individual or Legal Entity authorized to submit on behalf of the copyright owner. For the purposes of this definition, "submitted" means any form of electronic, verbal, or written communication sent to the Licensor or its representatives, including but not limited to communication on electronic mailing lists, source code control systems, and issue tracking systems that are managed by, or on behalf of, the Licensor for the purpose of discussing and improving the Work, but excluding communication that is conspicuously marked or otherwise designated in writing by the copyright owner as "Not a Contribution."

"Contributor" shall mean Licensor and any individual or Legal Entity on behalf of whom a Contribution has been received by Licensor and subsequently incorporated within the Work.

2. Grant of Copyright License. Subject to the terms and conditions of this License, each Contributor hereby grants to You a perpetual, worldwide, non-exclusive, no-charge, royalty-free, irrevocable copyright license to reproduce, prepare Derivative Works of, publicly display, publicly perform, sublicense, and distribute the Work and such Derivative Works in Source or Object form.

3. Grant of Patent License. Subject to the terms and conditions of this License, each Contributor hereby grants to You a perpetual, worldwide, non-exclusive, no-charge, royalty-free, irrevocable (except as stated in this section) patent license to make, have made, use, offer to sell, sell, import, and otherwise transfer the Work, where such license applies only to those patent claims licensable by such Contributor that are necessarily infringed by their Contribution(s) alone or by combination of their Contribution(s) with the Work to which such Contribution(s) was submitted. If You institute patent litigation against any entity (including a cross-claim or counterclaim in a lawsuit) alleging that the Work or a Contribution incorporated within the Work constitutes direct or contributory patent infringement, then any patent licenses granted to You under this License for that Work shall terminate as of the date such litigation is filed.

4. Redistribution. You may reproduce and distribute copies of the Work or Derivative Works thereof in any medium, with or without modifications, and in Source or Object form, provided that You meet the following conditions:

You must give any other recipients of the Work or Derivative Works a copy of this License; and

You must cause any modified files to carry prominent notices stating that You changed the files; and

You must retain, in the Source form of any Derivative Works that You distribute, all copyright, patent, trademark, and attribution notices from the Source form of the Work, excluding those notices that do not pertain to any part of the Derivative Works; and

If the Work includes a "NOTICE" text file as part of its distribution, then any Derivative Works that You distribute must include a readable copy of the attribution notices contained within such NOTICE file, excluding those notices that do not pertain to any part of the Derivative Works, in at least one of the following places: within a NOTICE text file distributed as part of the Derivative Works; within the Source form or documentation, if provided along with the Derivative Works; or, within a display generated by the Derivative Works, if and wherever such third-party notices normally appear. The contents of the NOTICE file are for informational purposes only and do not modify the License. You may add Your own attribution notices within Derivative Works that You distribute, alongside or as an addendum to the NOTICE text from the Work, provided that such additional attribution notices cannot be construed as modifying the License. You may add Your own copyright statement to Your modifications and may provide additional or different license terms and conditions for use, reproduction, or distribution of Your modifications, or for any such Derivative Works as a whole, provided Your use, reproduction, and distribution of the Work otherwise complies with the conditions stated in this License.

5. Submission of Contributions. Unless You explicitly state otherwise, any Contribution intentionally submitted for inclusion in the Work by You to the Licensor shall be under the terms and conditions of this License, without any additional terms or conditions. Notwithstanding the above, nothing herein shall supersede or modify the terms of any separate license agreement you may have executed with Licensor regarding such Contributions.

6. Trademarks. This License does not grant permission to use the trade names, trademarks, service marks, or product names of the Licensor, except as required for reasonable and customary use in describing the origin of the Work and reproducing the content of the NOTICE file.

7. Disclaimer of Warranty. Unless required by applicable law or agreed to in writing, Licensor provides the Work (and each Contributor provides its Contributions) on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied, including, without limitation, any warranties or conditions of TITLE, NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A PARTICULAR PURPOSE. You are solely responsible for determining the appropriateness of using or redistributing the Work and assume any risks associated with Your exercise of permissions under this License.

8. Limitation of Liability. In no event and under no legal theory, whether in tort (including negligence), contract, or otherwise, unless required by applicable law (such as deliberate and grossly negligent acts) or agreed to in writing, shall any Contributor be liable to You for damages, including any direct, indirect, special, incidental, or consequential damages of any character arising as a result of this License or out of the use or inability to use the Work (including but not limited to damages for loss of goodwill, work stoppage, computer failure or malfunction, or any and all other commercial damages or losses), even if such Contributor has been advised of the possibility of such damages.

9. Accepting Warranty or Additional Liability. While redistributing the Work or Derivative Works thereof, You may choose to offer, and charge a fee for, acceptance of support, warranty, indemnity, or other liability obligations and/or rights consistent with this License. However, in accepting such obligations, You may act only on Your own behalf and on Your sole responsibility, not on behalf of any other Contributor, and only if You agree to indemnify, defend, and hold each Contributor harmless for any liability incurred by, or claims asserted against, such Contributor by reason of your accepting any such warranty or additional liability.

END OF TERMS AND CONDITIONS
=========================================
END OF Ionic documentation NOTICES AND INFORMATION




%% vscode-swift version 0.0.1 (https://github.com/owensd/vscode-swift)
=========================================
The MIT License (MIT)

Copyright (c) 2015 David Owens II

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
=========================================
END OF vscode-swift NOTICES AND INFORMATION

````

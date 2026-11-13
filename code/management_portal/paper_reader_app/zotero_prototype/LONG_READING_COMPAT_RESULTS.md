# Long-document and legacy-format compatibility (Dev evidence)

No production code, database, personal PDF, historical record, or model was
modified. Kimi stays paused. No publish or activation was run.

## Current implementation and compatibility boundary

`http_transport.js` deliberately supplies `savePosition: async () => {}` and does
not load a position. `host.js` forwards initial.position and changes of native
view state, but the formal transport does not persist them. The production
`zotero_adapter.js` captureAnchor returns undefined; Reader's anchor prop is not
passed into connectZotero. Therefore same-instance layout changes can preserve
the live reader position, but closing/reopening and reloading do not restore it.
The test explicitly reproduces page 24 becoming page 1 after reopen. This is a
confirmed product gap, not a test of persistence failure recovery.

Old `portal_pdf_preview.js` captures an in-memory anchor:
`{kind:'pdf-page',pageNumber,pageXRatio,pageYRatio,viewportX,viewportY}`. Its
restoreAnchor maps page ratios to viewport scroll. This is not Zotero's native
viewState. This review does not claim a migration/import of any browser-native
PDF.js view-history store; none is implemented by the current Zotero adapter.

Old `paper_highlights.py` stores private association rows under actor,
`paper-highlights`, Paper ID and PDF fingerprint. IDs are UUID-shaped, rects
contain 1-based page and normalized x/y/width/height. Optional native PDF.js
annotations use annotationType=9, pageIndex, rotation, rect and quadrilaterals.
New Reader annotations use stable annotation IDs, account/Paper/immutable
attachment-version scope, type=highlight/note, and position.pageIndex (zero-based)
plus PDF-space rects. The transport reads only the new AnnotationStore. Old
entries are neither deleted nor imported nor displayed automatically. Coordinate
conversion alone cannot prove that an old fingerprint denotes the new immutable
attachment; a future migration must establish that binding and retain receipts.

## New reproducible evidence

From code root, sequentially:

```
node tests/cases/javascript/zotero_reader_long_compat.cjs chromium
node tests/cases/javascript/zotero_reader_long_compat.cjs firefox
```

Both browser commands exited 0. Fixture generates 36 vector/text pages in memory;
pages 28 and 30 have intrinsic /Rotate 270 and /Rotate 180. Each page has separate
left/right column text markers. It loads the real current production adapter,
native Reader bundle, host and HTTP persistence client. An in-memory test HTTP
server supplies only the synthetic PDF and annotations; this does not repeat or
replace the prior real-cookie/PG integration evidence.

New assertions:

- Navigate through the real page-number input to page 24; six actual layout
  transitions change width and invoke production setFullscreen. The same iframe
  Window and page number remain. This does not assert exact pixel scroll offset
  across different widths or long-term memory/resource behavior.
- On pages 28/30, use the real highlight tool and mouse drag on the left column
  text layer, then close through the save barrier. Saved annotations contain a
  highlight with the correct zero-based page index, LEFT text and no RIGHT text.
  It does not assert that a drag selects every leading/trailing character or that
  complex cross-column reading order is correct.
- Reopen and click each saved annotation's sidebar header. Assert actual page
  number and rendered page/viewport intersection. Map its saved PDF rectangle
  center with the fixture's 180/270-degree transform; assert that exact center
  lies inside the real PDF iframe viewport. These are DOM geometry assertions,
  not screenshot pixel-diff claims.
- Navigate to page24, close and reopen. Confirm page1, documenting the actual
  missing persistence behavior without manufacturing a restore assertion.

Fixture corrections during development: revision must be a string and commit
responses must acknowledge operation_id, matching the real transport protocol.
Page-number tests wait for the visible 36-page count and commit through Enter/
blur (Firefox initially raced initial navigation). The Window identity is stored
without returning the Window across Playwright serialization. No product change
was made to hide these fixture issues.

Prior short-PDF save/account/delete, same-instance draft, three-page 0/90-degree
note positioning, and loading-close tests were not rerun. This slice adds long
navigation and actual 180/270-degree text-highlight selection/center evidence.

## Remaining work / release boundary

Position persistence requires a separately reviewed backend contract: formal
principal and authoritative Reader descriptor; bounded native viewState per
account/Paper/attachment-version; stale-session/write ordering; missing/invalid
state handling; explicit policy for old PDF.js anchors. This slice adds no such
endpoint and performs no migration.

Unverified: complex cross-column/multipage selections, precise offset restoration,
0/90-degree text-highlighting additions, scanned/OCR text extraction, thousands of
pages, resource-leak soak testing, real-user historical migration. A scanned PDF
without OCR has no selectable text; this test makes no regression claim about it.

Proposed change scope is only this report and the new cjs test. No host, adapter,
shared Paper page, bundle, backend, runtime or schema edits. There is no runtime
feature change to publish; if these test/docs files are later published alone,
use the agreed --no-activate path and coordinate timing with the parent.

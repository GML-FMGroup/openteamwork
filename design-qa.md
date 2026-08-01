**Comparison Target**

- Source visual truth: `$TMPDIR/codex-clipboard-66b16224-cab1-4926-94b5-318dec44b81f.png`, together with the explicit requirements to use English desktop chrome and remove the large empty Settings region through a more rational arrangement of Runtime status, Connection config, Connection, Diagnostics, and Paths.
- Browser-rendered implementation: `$TMPDIR/openppx-settings-english-balanced.png` (top state) and `$TMPDIR/openppx-settings-english-balanced-matched.png` (bottom-of-page state).
- Side-by-side comparison: `$TMPDIR/openppx-settings-phase49-side-by-side.png`.
- Viewport: 1171 × 903 CSS px with device scale factor 1, matching the source's inferred 1171 × 903 CSS viewport.
- Pixels and normalization: source 2342 × 1806 px at inferred 2× density was downsampled to 1171 × 903 px. Both implementation captures are 1171 × 903 px at 1× density. The normalized source and bottom implementation were assembled into a 2342 × 903 px comparison.
- State: source uses the real local node after scrolling through the former unequal-column layout. Implementation uses deterministic mock runtime values and the new compact layout at its maximum scroll position. Runtime values differ intentionally; hierarchy, density, language, card sizing, and empty-space behavior are the comparison targets.

**Full-view Comparison Evidence**

- The source leaves approximately half of the left content column empty while Connection and Diagnostics continue down the right column.
- The implementation replaces independent unequal stacks with a hierarchy-driven grid: Runtime status and Connection config span the page, Connection and Diagnostics share one balanced row, and Paths spans the page below them.
- Connection and Diagnostics contain ten facts each, so their card heights and internal row rhythm match. Paths presents four values horizontally instead of building a tall one-column stack.
- At the maximum scroll position, the new page still has continuous card content across the full width; the marked large blank region is gone.
- All visible navigation, controls, labels, empty states, status copy, and Settings content are English. Dynamic node/runtime values remain unchanged.

**Focused Region Comparison Evidence**

- A separate crop was not required: the normalized 2342 × 903 side-by-side image keeps the full Settings content legible, and the accessibility snapshot independently confirms every control label and section heading.
- The LAN interaction state was also exercised: selecting `Connect to an OpenPPX Node on the LAN` reveals the English `Access Token` field and its English placeholder without shifting or breaking the card grid.

**Required Fidelity Surfaces**

- Fonts and typography: existing family, weights, sizes, line heights, letter spacing, antialiasing, heading hierarchy, and uppercase diagnostic labels are preserved. Longer English controls fit without clipping.
- Spacing and layout rhythm: 12 px card gaps, paired card widths, matched Connection/Diagnostics heights, compact Runtime row, three-column configuration fields, and four-column Paths remove the uneven vertical rhythm. Responsive rules collapse paired cards and field grids at narrower widths.
- Colors and visual tokens: the neutral OpenWorker-inspired palette, white cards, warm paper background, hairline borders, dark primary controls, and green health state remain unchanged.
- Image quality and asset fidelity: no image, logo, illustration, or icon asset was added, replaced, or degraded. Existing brand and shell icons remain sharp.
- Copy and content: desktop-owned UI is English. User/Agent conversation content remains data and is not translated. The legacy Chinese generic session title is recognized only for cached-data compatibility and is not rendered as new UI copy.

**Findings**

- No actionable P0, P1, or P2 mismatch remains for the requested language standardization and Settings layout correction.

**Comparison History**

- Iteration 1 source issue: two independent Settings columns had very different total heights, leaving a large empty left region while Connection and Diagnostics continued on the right. Desktop chrome also mixed Chinese and English.
- Fix: replaced the column stacks with full-width/pair/full-width grid placement; balanced Connection and Diagnostics to ten facts each; made Connection config and Paths horizontal at desktop width; translated desktop-owned interface copy and validation messages to English.
- Post-fix evidence: the source-equivalent browser captures and side-by-side comparison show continuous full-width content, balanced information cards, English controls, and no hidden persistent actions. No additional P0/P1/P2 fix was required.

**Primary Interactions and Runtime Checks**

- Opened the workspace and confirmed English navigation, Session search, composer, task panel, status, and artifact empty state.
- Activated `Connections & Settings` through its unique accessible button.
- Verified Runtime status, Connection config, Connection, Diagnostics, and Paths render in the intended order.
- Switched Run location from local to LAN and verified Access Token appears with English copy.
- Browser console warnings/errors checked: none.

**Open Questions**

- None for this scoped iteration.

**Implementation Checklist**

- [x] Translate desktop-owned interface copy to English.
- [x] Preserve dynamic conversation content.
- [x] Use a full-width Runtime status card.
- [x] Use a full-width horizontal Connection config card.
- [x] Balance Connection and Diagnostics in one row.
- [x] Use a full-width horizontal Paths card.
- [x] Preserve responsive single-column behavior.
- [x] Run typecheck, desktop tests, client API tests, interaction checks, and visual comparison.

**Follow-up Polish**

- None required for this change.

final result: passed

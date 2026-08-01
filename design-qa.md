**Comparison Target**

- Source visual truth: `$TMPDIR/codex-clipboard-6102f843-6f63-454e-b570-b089d95945b2.png`
- Implementation screenshot: `$TMPDIR/com.openai.sky.CUAService/OpenPPX Desktop Screenshot 2026-08-01 at 8.30.10 PM.jpeg`
- Focused comparison: `$TMPDIR/openppx-session-brace.F4uWQT/comparison.png`
- Viewport: OpenPPX Desktop window at 1203 × 768 px.
- Pixels and normalization: source 612 × 534 px; implementation 1203 × 768 px. The Session regions were cropped and normalized to 520 px high, then placed side by side in a 1146 × 524 px comparison image. The desktop capture used the native Computer Use screenshot density.
- State: left sidebar expanded, `low-main` selected, first Session selected, Session list visible.

**Full-view Comparison Evidence**

- The source is a focused Session-list screenshot rather than a full application view, so full-screen fidelity outside the left sidebar is not part of this change.
- The full implementation screenshot was checked for regressions: the three-column workspace, sidebar width, search field, Session list, composer, and inspector remain intact.

**Focused Region Comparison Evidence**

- The side-by-side comparison shows that the selected Session keeps its light-gray rounded background while the black brace-shaped left outline is absent.
- Typography: title, metadata, date weight, hierarchy, wrapping, and truncation are unchanged by the CSS update.
- Spacing and layout rhythm: row padding, radius, inter-row gaps, and search-to-list spacing are unchanged.
- Colors and visual tokens: the existing selected-row gray token remains; no new color was introduced.
- Image quality and asset fidelity: no image or icon asset is affected; the native desktop screenshot is sharp enough to inspect the selected-row edge.
- Copy and content: Session titles, `OpenPPX session`, and dates remain unchanged.

**Findings**

- No actionable P0, P1, or P2 mismatch remains for the requested selected-Session state.

**Comparison History**

- Iteration 1: removing the active-row inset shadow alone did not fully remove the brace. The real desktop capture still showed the global `:focus-visible` outline clipped around the active rounded row.
- Fix: added an active Session focus rule that suppresses this redundant outline while retaining the light-gray selected state.
- Iteration 2: rebuilt and launched a fresh packaged desktop app. The focused side-by-side comparison confirms the brace is gone and no layout or token drift was introduced.

**Open Questions**

- None for this scoped visual change.

**Implementation Checklist**

- [x] Remove the active-row inset accent.
- [x] Remove the redundant active-row focus outline.
- [x] Preserve the selected light-gray background.
- [x] Run typecheck and desktop tests.
- [x] Rebuild and visually verify the packaged desktop app.

**Follow-up Polish**

- None required for this change.

final result: passed

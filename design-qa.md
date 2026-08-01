**Comparison Target**

- Source visual truth: `$TMPDIR/codex-clipboard-64bcc2c9-c340-4806-ba30-0d8b5828148e.png`.
- Implementation screenshot: `$TMPDIR/com.openai.sky.CUAService/OpenPPX Desktop Screenshot 2026-08-01 at 9.22.41 PM.jpeg`.
- Focused comparison: `$TMPDIR/openppx-regular-labels.yehOfz/regular-labels-comparison.png`.
- Viewport: OpenPPX Desktop window at 1203 × 768 px.
- Pixels and normalization: source 2460 × 1350 px; implementation 1203 × 768 px. The annotated top bar and inspector regions were cropped, aspect-fit into equal 540 × 300 px panels, and assembled into a 1088 × 608 px comparison image. The implementation uses the native Computer Use screenshot density.
- State: three-column workspace, `low-main` and `hi` selected, Progress and Artifacts expanded, completed-run status visible.

**Full-view Comparison Evidence**

- The packaged desktop screenshot preserves the three-column layout, top-bar height, transcript width, inspector structure, and composer placement.
- The weight-only changes introduce no wrapping, clipping, overflow, or spacing shift.

**Focused Region Comparison Evidence**

- Fonts and typography: the Session title, Progress/Artifacts headings, and current-run status now use regular weight 400. Font family, size, line height, letter spacing, antialiasing, and hierarchy are otherwise unchanged.
- Spacing and layout rhythm: breadcrumb gaps, disclosure-row height, chevron alignment, run-summary spacing, and divider positions are unchanged.
- Colors and visual tokens: no foreground, background, border, opacity, or semantic token changed.
- Image quality and asset fidelity: no image, logo, illustration, or icon asset changed; the focused comparison is sharp enough to judge weight and alignment.
- Copy and content: Session title, Progress, Artifacts, CURRENT RUN, and status text remain unchanged.

**Findings**

- No actionable P0, P1, or P2 mismatch remains for the requested regular-weight labels.

**Comparison History**

- Iteration 1: the annotated screenshot identified excessive boldness in the top Session title, Progress/Artifacts disclosure labels, and current-run status.
- Fix: changed `.topbar-copy strong`, `.inspector-section-toggle`, and `.run-summary strong` to weight 400.
- Post-fix evidence: the packaged desktop screenshot and focused two-row comparison confirm all highlighted labels are regular weight without layout drift. No additional P0/P1/P2 fix was needed.

**Open Questions**

- None for this scoped typography change.

**Implementation Checklist**

- [x] Use regular weight for the current Session title.
- [x] Use regular weight for Progress and Artifacts.
- [x] Use regular weight for every current-run status variant.
- [x] Preserve sizes, colors, spacing, content, and interactions.
- [x] Run typecheck and desktop tests.
- [x] Rebuild and visually verify the packaged desktop app.

**Follow-up Polish**

- None required for this change.

final result: passed

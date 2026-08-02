**Comparison Target**

- Source visual truth: `$TMPDIR/codex-clipboard-1162d2b9-ed0b-48db-b02f-4d3cf5f96f0c.png`.
- Browser-rendered collapsed Chat view: `$TMPDIR/openppx-phase52-collapsed.png`.
- Browser-rendered expanded Chat view: `$TMPDIR/openppx-phase52-expanded.png`.
- Browser-rendered collapsed Settings view: `$TMPDIR/openppx-phase52-settings-collapsed.png`.
- Browser-rendered narrow views: `$TMPDIR/openppx-phase52-narrow-collapsed.png` and `$TMPDIR/openppx-phase52-narrow-expanded.png`.
- Focused side-by-side comparison: `$TMPDIR/openppx-phase52-comparison.png` (source on the left, implementation on the right).
- Desktop viewport: 1171 × 903 CSS px; screenshot output: 1171 × 903 px.
- Narrow viewport: 720 × 800 CSS px; screenshot output: 720 × 800 px.
- State: sidebar collapsed in Chat and Settings, then reopened in desktop and narrow-window layouts.

**Full-view Comparison Evidence**

- The collapsed `ContextSidebar` branch renders no DOM and the application grid reports `0px 879px 292px`; no blank or decorative rail remains.
- Chat uses the released width immediately while preserving the existing center transcript and right task panel.
- Settings uses the same top-bar recovery control and fills the released width without restoring a separate navigation strip.
- The top-bar opener starts at CSS x=72px, keeping it after the macOS traffic-light safe area.

**Focused Region Comparison Evidence**

- The normalized source crop shows the unwanted `P`, Workspace, Settings, Node-status, and bottom-chevron rail.
- The implementation crop shows no left rail or left border; only the single top-bar recovery chevron remains.
- DOM evidence confirms zero `[aria-label="OpenPPX navigation"]` elements and zero collapsed-rail elements while collapsed, with exactly one `Open sidebar` button.

**Required Fidelity Surfaces**

- Fonts and typography: top-bar labels and application content retain the existing type scale, weights, and neutral palette.
- Spacing and layout rhythm: the collapsed left grid track changes from 64px to 0px; the opener is 30 × 30px at x=72px, and no vertical strip consumes content width.
- Colors and visual tokens: no new accent color, surface, border, shadow, or status treatment was introduced.
- Image quality and asset fidelity: no raster or brand asset was added, removed, replaced, or degraded; existing icon components provide the chevrons.
- Copy and content: Node, Agent, Session, task, Settings, and inspector content are unchanged. Only redundant collapsed-rail chrome is removed.

**Interactions and Responsive Checks**

- Clicking `Open sidebar` restores the expanded desktop navigation to 244px and removes the opener.
- `⌘B` remains covered by the same sidebar state toggle regression.
- Settings exposes exactly one top-bar opener while collapsed.
- At 720 × 800, the collapsed layout has no sidebar DOM and the opener remains available; activating it restores a 270px absolute overlay sidebar.
- Browser console warnings/errors checked: none. Only Vite debug connection messages and the React development-tools information message were present.

**Findings**

- No actionable P0, P1, or P2 mismatch remains for the requested rail removal.

**Comparison History**

- Iteration 1 source issue: collapsing the sidebar preserved a 64px rail with duplicated brand/navigation/status controls and a bottom restore button.
- Fix: return no collapsed sidebar component, set the collapsed grid track to 0px, and place one restore action in both application top bars.
- Responsive follow-up: allow the real expanded sidebar to return as an overlay at the narrow breakpoint instead of hiding it unconditionally.
- Post-fix evidence: desktop Chat, desktop Settings, narrow collapsed, and narrow reopened states all preserve a clear recovery path without recreating the vertical rail.

**Implementation Checklist**

- [x] Remove the collapsed sidebar rail and release its grid width.
- [x] Add a macOS-safe top-bar recovery control in Chat and Settings.
- [x] Preserve expanded navigation, `⌘B`, and application state.
- [x] Preserve narrow-window recovery with an overlay sidebar.
- [x] Add focused regression coverage and run the full desktop suite.
- [x] Capture and compare the requested collapsed region.

**Follow-up Polish**

- None required for this change.

final result: passed

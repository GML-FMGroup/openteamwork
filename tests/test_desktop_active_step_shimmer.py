"""Style-contract coverage for active Desktop work feedback."""

from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STYLES = (PROJECT_ROOT / "apps/desktop/app/src/styles.css").read_text(encoding="utf-8")
WORKSPACE_STYLES = (PROJECT_ROOT / "apps/desktop/app/src/workspace.css").read_text(
    encoding="utf-8"
)


def test_active_step_uses_a_broad_brand_highlight() -> None:
    """The running phase should animate a visible surface, not only dark glyphs."""

    assert "--activity-shimmer-text-peak:" in STYLES
    assert "--activity-shimmer-band-core:" in STYLES
    assert (
        ".activity-phase.running > summary .activity-phase-copy::before"
        in WORKSPACE_STYLES
    )
    assert "animation: activity-copy-sweep 1.25s linear infinite;" in WORKSPACE_STYLES
    assert "var(--activity-shimmer-text-peak)" in WORKSPACE_STYLES
    assert re.search(
        r"@keyframes activity-copy-sweep[\s\S]*translate3d\(340%, 0, 0\)",
        WORKSPACE_STYLES,
    )


def test_reduced_motion_removes_both_shimmer_layers() -> None:
    """Reduced motion should retain a static readable running state."""

    assert re.search(
        r"@media \(prefers-reduced-motion: reduce\)"
        r"[\s\S]*\.activity-phase\.running > summary \.activity-phase-copy::before"
        r"[\s\S]*content: none",
        WORKSPACE_STYLES,
    )
    assert re.search(
        r"@media \(prefers-reduced-motion: reduce\)"
        r"[\s\S]*\.activity-phase\.running > summary \.activity-phase-copy > strong"
        r"[\s\S]*background: none",
        WORKSPACE_STYLES,
    )

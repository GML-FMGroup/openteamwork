import type { DesktopPlatform } from "../../types";

interface WindowDragRegionProps {
  platform: DesktopPlatform;
}

/** Provide a non-interactive native window-drag surface when macOS hides the title bar. */
export function WindowDragRegion({ platform }: WindowDragRegionProps) {
  if (platform !== "macos") {
    return null;
  }
  return <div className="window-drag-region" aria-hidden="true" />;
}

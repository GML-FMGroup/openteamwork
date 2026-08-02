import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type RefObject,
} from "react";

export type WorkspaceColumn = "left" | "right";

export const COLUMN_WIDTH_STORAGE_KEY = "openppx.desktop.column-widths.v1";
export const COLUMN_WIDTH_LIMITS = {
  left: { min: 220, max: 420 },
  right: { min: 260, max: 520 },
} as const;

const CENTER_COLUMN_MIN = 520;
const COLUMN_KEYBOARD_STEP = 16;
const COMPACT_QUERY = "(max-width: 1080px)";
const DENSE_QUERY = "(max-width: 1279px)";

interface ColumnPreferences {
  left: number | null;
  right: number | null;
}

interface DragSnapshot {
  side: WorkspaceColumn;
  startX: number;
  startWidth: number;
  otherWidth: number;
  shellWidth: number;
}

type ColumnLayoutStyle = CSSProperties & {
  "--left-column-custom"?: string;
  "--right-column-custom"?: string;
};

export interface ColumnLayoutController {
  shellRef: RefObject<HTMLDivElement | null>;
  style: ColumnLayoutStyle;
  compactLayout: boolean;
  resizingColumn: WorkspaceColumn | null;
  leftWidth: number;
  rightWidth: number;
  beginResize: (side: WorkspaceColumn, clientX: number) => void;
  resizeWithKeyboard: (side: WorkspaceColumn, key: string, largeStep: boolean) => boolean;
  resetColumn: (side: WorkspaceColumn) => void;
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(Math.max(Math.round(value), minimum), maximum);
}

function mediaMatches(query: string): boolean {
  try {
    return typeof window.matchMedia === "function" && window.matchMedia(query).matches;
  } catch {
    return false;
  }
}

function responsiveDefaults(denseLayout: boolean): { left: number; right: number } {
  return denseLayout ? { left: 240, right: 300 } : { left: 252, right: 316 };
}

function readPreferences(): ColumnPreferences {
  try {
    const raw = window.localStorage?.getItem(COLUMN_WIDTH_STORAGE_KEY);
    if (!raw) {
      return { left: null, right: null };
    }
    const parsed = JSON.parse(raw) as Partial<Record<WorkspaceColumn, unknown>>;
    return {
      left:
        typeof parsed.left === "number" && Number.isFinite(parsed.left)
          ? clamp(parsed.left, COLUMN_WIDTH_LIMITS.left.min, COLUMN_WIDTH_LIMITS.left.max)
          : null,
      right:
        typeof parsed.right === "number" && Number.isFinite(parsed.right)
          ? clamp(parsed.right, COLUMN_WIDTH_LIMITS.right.min, COLUMN_WIDTH_LIMITS.right.max)
          : null,
    };
  } catch {
    return { left: null, right: null };
  }
}

function persistPreferences(preferences: ColumnPreferences): void {
  try {
    const payload: Partial<Record<WorkspaceColumn, number>> = {};
    if (preferences.left !== null) {
      payload.left = preferences.left;
    }
    if (preferences.right !== null) {
      payload.right = preferences.right;
    }
    if (Object.keys(payload).length === 0) {
      window.localStorage?.removeItem(COLUMN_WIDTH_STORAGE_KEY);
      return;
    }
    window.localStorage?.setItem(COLUMN_WIDTH_STORAGE_KEY, JSON.stringify(payload));
  } catch {
    // Layout preferences are best-effort and must never block the workspace.
  }
}

/** Manage resizable Desktop columns without coupling pointer mechanics to the App shell. */
export function useColumnLayout(): ColumnLayoutController {
  const shellRef = useRef<HTMLDivElement | null>(null);
  const [preferences, setPreferences] = useState<ColumnPreferences>(readPreferences);
  const preferencesRef = useRef(preferences);
  const [compactLayout, setCompactLayout] = useState(() => mediaMatches(COMPACT_QUERY));
  const [denseLayout, setDenseLayout] = useState(() => mediaMatches(DENSE_QUERY));
  const [resizingColumn, setResizingColumn] = useState<WorkspaceColumn | null>(null);
  const dragRef = useRef<DragSnapshot | null>(null);
  const dragCleanupRef = useRef<(() => void) | null>(null);
  const defaults = responsiveDefaults(denseLayout);
  const leftWidth = preferences.left ?? defaults.left;
  const rightWidth = preferences.right ?? defaults.right;

  const updatePreferences = useCallback((next: ColumnPreferences, persist: boolean): void => {
    preferencesRef.current = next;
    setPreferences(next);
    if (persist) {
      persistPreferences(next);
    }
  }, []);

  const shellWidth = useCallback((): number => {
    const renderedWidth = shellRef.current?.clientWidth ?? 0;
    const safeDefault = defaults.left + defaults.right + CENTER_COLUMN_MIN;
    return renderedWidth > 0 ? renderedWidth : Math.max(window.innerWidth || 0, safeDefault);
  }, [defaults.left, defaults.right]);

  const constrainedWidth = useCallback(
    (side: WorkspaceColumn, value: number, availableWidth: number, otherWidth: number): number => {
      const limits = COLUMN_WIDTH_LIMITS[side];
      const centerSafeMaximum = Math.max(limits.min, availableWidth - otherWidth - CENTER_COLUMN_MIN);
      return clamp(value, limits.min, Math.min(limits.max, centerSafeMaximum));
    },
    [],
  );

  const setColumnWidth = useCallback(
    (side: WorkspaceColumn, value: number, availableWidth: number, otherWidth: number, persist: boolean): void => {
      const nextWidth = constrainedWidth(side, value, availableWidth, otherWidth);
      const next = { ...preferencesRef.current, [side]: nextWidth };
      updatePreferences(next, persist);
    },
    [constrainedWidth, updatePreferences],
  );

  const resetColumn = useCallback(
    (side: WorkspaceColumn): void => {
      const next = { ...preferencesRef.current, [side]: null };
      updatePreferences(next, true);
    },
    [updatePreferences],
  );

  const beginResize = useCallback(
    (side: WorkspaceColumn, clientX: number): void => {
      dragCleanupRef.current?.();
      const drag: DragSnapshot = {
        side,
        startX: clientX,
        startWidth: side === "left" ? leftWidth : rightWidth,
        otherWidth: side === "left" ? rightWidth : leftWidth,
        shellWidth: shellWidth(),
      };
      dragRef.current = drag;
      setResizingColumn(side);

      const handlePointerMove = (event: PointerEvent): void => {
        const activeDrag = dragRef.current;
        if (!activeDrag) {
          return;
        }
        const pointerDelta = event.clientX - activeDrag.startX;
        const nextWidth =
          activeDrag.side === "left"
            ? activeDrag.startWidth + pointerDelta
            : activeDrag.startWidth - pointerDelta;
        setColumnWidth(activeDrag.side, nextWidth, activeDrag.shellWidth, activeDrag.otherWidth, false);
      };

      const removeDragListeners = (): void => {
        window.removeEventListener("pointermove", handlePointerMove);
        window.removeEventListener("pointerup", finishResize);
        window.removeEventListener("pointercancel", finishResize);
        window.removeEventListener("blur", finishResize);
        if (dragCleanupRef.current === removeDragListeners) {
          dragCleanupRef.current = null;
        }
      };

      const finishResize = (): void => {
        persistPreferences(preferencesRef.current);
        dragRef.current = null;
        setResizingColumn(null);
        removeDragListeners();
      };

      window.addEventListener("pointermove", handlePointerMove);
      window.addEventListener("pointerup", finishResize);
      window.addEventListener("pointercancel", finishResize);
      window.addEventListener("blur", finishResize);
      dragCleanupRef.current = removeDragListeners;
    },
    [leftWidth, rightWidth, setColumnWidth, shellWidth],
  );

  useEffect(
    () => () => {
      dragCleanupRef.current?.();
    },
    [],
  );

  const resizeWithKeyboard = useCallback(
    (side: WorkspaceColumn, key: string, largeStep: boolean): boolean => {
      if (key === "Home") {
        resetColumn(side);
        return true;
      }
      if (key !== "ArrowLeft" && key !== "ArrowRight") {
        return false;
      }
      const step = COLUMN_KEYBOARD_STEP * (largeStep ? 2 : 1);
      const direction = key === "ArrowRight" ? 1 : -1;
      const currentWidth = side === "left" ? leftWidth : rightWidth;
      const otherWidth = side === "left" ? rightWidth : leftWidth;
      const nextWidth = currentWidth + (side === "left" ? direction : -direction) * step;
      setColumnWidth(side, nextWidth, shellWidth(), otherWidth, true);
      return true;
    },
    [leftWidth, resetColumn, rightWidth, setColumnWidth, shellWidth],
  );

  useEffect(() => {
    if (typeof window.matchMedia !== "function") {
      return;
    }
    const compactQuery = window.matchMedia(COMPACT_QUERY);
    const denseQuery = window.matchMedia(DENSE_QUERY);
    const syncMedia = (): void => {
      setCompactLayout(compactQuery.matches);
      setDenseLayout(denseQuery.matches);
    };
    syncMedia();
    compactQuery.addEventListener("change", syncMedia);
    denseQuery.addEventListener("change", syncMedia);
    return () => {
      compactQuery.removeEventListener("change", syncMedia);
      denseQuery.removeEventListener("change", syncMedia);
    };
  }, []);

  useEffect(() => {
    const constrainPreferencesToWindow = (): void => {
      if (compactLayout) {
        return;
      }
      const availableWidth = shellWidth();
      const current = preferencesRef.current;
      let nextLeft = current.left ?? defaults.left;
      let nextRight = current.right ?? defaults.right;
      let overflow = nextLeft + nextRight + CENTER_COLUMN_MIN - availableWidth;
      if (overflow <= 0) {
        return;
      }
      const rightReduction = Math.min(overflow, nextRight - COLUMN_WIDTH_LIMITS.right.min);
      nextRight -= rightReduction;
      overflow -= rightReduction;
      nextLeft -= Math.min(overflow, nextLeft - COLUMN_WIDTH_LIMITS.left.min);
      const next: ColumnPreferences = {
        left: current.left === null && nextLeft === defaults.left ? null : nextLeft,
        right: current.right === null && nextRight === defaults.right ? null : nextRight,
      };
      if (next.left !== current.left || next.right !== current.right) {
        updatePreferences(next, true);
      }
    };

    constrainPreferencesToWindow();
    window.addEventListener("resize", constrainPreferencesToWindow);
    return () => window.removeEventListener("resize", constrainPreferencesToWindow);
  }, [compactLayout, defaults.left, defaults.right, shellWidth, updatePreferences]);

  const style = useMemo<ColumnLayoutStyle>(() => {
    const next: ColumnLayoutStyle = {};
    if (preferences.left !== null) {
      next["--left-column-custom"] = `${preferences.left}px`;
    }
    if (preferences.right !== null) {
      next["--right-column-custom"] = `${preferences.right}px`;
    }
    return next;
  }, [preferences.left, preferences.right]);

  return {
    shellRef,
    style,
    compactLayout,
    resizingColumn,
    leftWidth,
    rightWidth,
    beginResize,
    resizeWithKeyboard,
    resetColumn,
  };
}

import type { KeyboardEvent, PointerEvent } from "react";
import type { WorkspaceColumn } from "../../hooks/use-column-layout";

interface ColumnResizeHandleProps {
  side: WorkspaceColumn;
  label: string;
  value: number;
  minimum: number;
  maximum: number;
  active: boolean;
  onResizeStart: (clientX: number) => void;
  onKeyboardResize: (key: string, largeStep: boolean) => boolean;
  onReset: () => void;
}

/** Accessible, visually quiet hit target layered over a workspace column hairline. */
export function ColumnResizeHandle({
  side,
  label,
  value,
  minimum,
  maximum,
  active,
  onResizeStart,
  onKeyboardResize,
  onReset,
}: ColumnResizeHandleProps) {
  function handlePointerDown(event: PointerEvent<HTMLDivElement>): void {
    if (event.button !== 0) {
      return;
    }
    event.preventDefault();
    event.currentTarget.setPointerCapture?.(event.pointerId);
    onResizeStart(event.clientX);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>): void {
    if (onKeyboardResize(event.key, event.shiftKey)) {
      event.preventDefault();
    }
  }

  return (
    <div
      className={`column-resize-handle ${side} ${active ? "active" : ""}`}
      role="separator"
      aria-label={label}
      aria-orientation="vertical"
      aria-valuemin={minimum}
      aria-valuemax={maximum}
      aria-valuenow={value}
      tabIndex={0}
      title="Drag to resize · Double-click to reset"
      onPointerDown={handlePointerDown}
      onKeyDown={handleKeyDown}
      onDoubleClick={onReset}
    />
  );
}

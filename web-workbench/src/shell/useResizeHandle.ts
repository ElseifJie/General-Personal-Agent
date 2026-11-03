import { useRef, useState, type KeyboardEvent, type PointerEvent } from "react";

/**
 * Shared drag-to-resize behaviour for the split panes (left navigation, right
 * detail rail). The handle is a real separator: pointer capture keeps the drag
 * alive outside the strip, and the arrow keys move it 24px at a time.
 */
export interface ResizeHandleOptions {
  /** Which way the pointer must move to widen the pane. */
  growToward: "left" | "right";
  width: number;
  min: number;
  max: number;
  label: string;
  /** Keyboard step; the shell and the rail both use one 24px nudge. */
  step?: number;
  onChange(width: number): void;
}

export interface ResizeHandleProps {
  role: "separator";
  "aria-orientation": "vertical";
  "aria-label": string;
  "aria-valuemin": number;
  "aria-valuemax": number;
  "aria-valuenow": number;
  tabIndex: number;
  onPointerDown(event: PointerEvent<HTMLElement>): void;
  onPointerMove(event: PointerEvent<HTMLElement>): void;
  onPointerUp(event: PointerEvent<HTMLElement>): void;
  onPointerCancel(event: PointerEvent<HTMLElement>): void;
  onKeyDown(event: KeyboardEvent<HTMLElement>): void;
}

export interface ResizeHandle {
  resizing: boolean;
  separatorProps: ResizeHandleProps;
}

function clampRound(value: number, min: number, max: number): number {
  const bounded = max < min ? max : Math.min(max, Math.max(min, value));
  return Math.round(bounded);
}

export function useResizeHandle({
  growToward,
  width,
  min,
  max,
  label,
  step = 24,
  onChange,
}: ResizeHandleOptions): ResizeHandle {
  const startRef = useRef<{ x: number; width: number } | null>(null);
  const [resizing, setResizing] = useState(false);
  const direction = growToward === "right" ? 1 : -1;

  function onPointerDown(event: PointerEvent<HTMLElement>) {
    // Only the primary button drags; right-click and middle-click do nothing.
    if (event.pointerType === "mouse" && event.button !== 0) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture?.(event.pointerId);
    startRef.current = { x: event.clientX, width };
    setResizing(true);
  }

  function onPointerMove(event: PointerEvent<HTMLElement>) {
    const start = startRef.current;
    if (!start) return;
    // A missed pointerup (window blur, OS drag, capture lost) must not leave
    // the rail glued to the cursor: no pressed button means the drag is over.
    if (event.buttons === 0) {
      endResize(event);
      return;
    }
    const delta = (event.clientX - start.x) * direction;
    onChange(clampRound(start.width + delta, min, max));
  }

  function endResize(event: PointerEvent<HTMLElement>) {
    const target = event.currentTarget;
    if (target.hasPointerCapture?.(event.pointerId)) {
      target.releasePointerCapture?.(event.pointerId);
    }
    startRef.current = null;
    setResizing(false);
  }

  function onKeyDown(event: KeyboardEvent<HTMLElement>) {
    let next: number | undefined;
    if (event.key === "ArrowRight") next = width + step * direction;
    else if (event.key === "ArrowLeft") next = width - step * direction;
    else if (event.key === "Home") next = min;
    else if (event.key === "End") next = max;
    if (next === undefined) return;
    event.preventDefault();
    onChange(clampRound(next, min, max));
  }

  return {
    resizing,
    separatorProps: {
      role: "separator",
      "aria-orientation": "vertical",
      "aria-label": label,
      "aria-valuemin": Math.round(min),
      "aria-valuemax": Math.round(max),
      "aria-valuenow": Math.round(width),
      tabIndex: 0,
      onPointerDown,
      onPointerMove,
      onPointerUp: endResize,
      onPointerCancel: endResize,
      onKeyDown,
    },
  };
}

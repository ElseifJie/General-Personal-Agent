import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";
import { SHELL_LAYOUT, type ShellLayoutResult } from "./layout";
import "./shell.css";

const focusableSelector =
  'a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])';

export interface AppFrameProps {
  layout: ShellLayoutResult;
  left: ReactNode;
  center: ReactNode;
  right?: ReactNode;
  leftLabel?: string;
  rightLabel?: string;
  /** Mobile navigation drawer; the page owns when it opens. */
  drawerOpen?: boolean;
  onCloseDrawer?(): void;
  drawerLabel?: string;
  /** Dismisses the full-screen right overlay on narrow screens. */
  onCloseOverlay?(): void;
  overlayCloseLabel?: string;
  onLeftWidthChange?(width: number): void;
  onRightWidthChange?(width: number): void;
  leftResizeLabel?: string;
  rightResizeLabel?: string;
}

export function AppFrame({
  layout,
  left,
  center,
  right,
  leftLabel = "Navigation",
  rightLabel = "Workspace panel",
  drawerOpen = false,
  onCloseDrawer,
  drawerLabel = "Close navigation",
  onCloseOverlay,
  overlayCloseLabel = "Close panel",
  onLeftWidthChange,
  onRightWidthChange,
  leftResizeLabel = "调整导航宽度",
  rightResizeLabel = "调整面板宽度",
}: AppFrameProps) {
  const leftRef = useRef<HTMLElement>(null);
  const resizeStartRef = useRef<{
    edge: "left" | "right";
    startX: number;
    startWidth: number;
  } | null>(null);
  const [resizingEdge, setResizingEdge] = useState<"left" | "right" | null>(
    null,
  );
  const style = {
    "--ui-shell-left-width": `${layout.leftWidth}px`,
    "--ui-shell-right-width": `${layout.rightWidth}px`,
  } as CSSProperties;
  const viewportWidth =
    layout.leftWidth + layout.centerWidth + layout.rightWidth;
  const rightMax = Math.max(
    Math.min(SHELL_LAYOUT.rightMin, viewportWidth),
    Math.min(
      Math.round(viewportWidth * SHELL_LAYOUT.rightMaxRatio),
      layout.rightWidth + layout.centerWidth - SHELL_LAYOUT.centerMin,
    ),
  );
  const rightMin = Math.min(SHELL_LAYOUT.rightMin, rightMax);
  const leftMax = SHELL_LAYOUT.leftMax;
  const clampLeft = (width: number) =>
    Math.round(Math.min(leftMax, Math.max(SHELL_LAYOUT.leftMin, width)));
  const clampRight = (width: number) =>
    Math.round(Math.min(rightMax, Math.max(rightMin, width)));

  function beginResize(
    edge: "left" | "right",
    event: ReactPointerEvent<HTMLDivElement>,
  ) {
    event.preventDefault();
    if (typeof event.currentTarget.setPointerCapture === "function") {
      event.currentTarget.setPointerCapture(event.pointerId);
    }
    resizeStartRef.current = {
      edge,
      startX: event.clientX,
      startWidth: edge === "left" ? layout.leftWidth : layout.rightWidth,
    };
    setResizingEdge(edge);
  }

  function moveResize(event: ReactPointerEvent<HTMLDivElement>) {
    const start = resizeStartRef.current;
    if (!start) return;
    const delta = event.clientX - start.startX;
    if (start.edge === "left") {
      onLeftWidthChange?.(clampLeft(start.startWidth + delta));
    } else {
      onRightWidthChange?.(clampRight(start.startWidth - delta));
    }
  }

  function endResize(event: ReactPointerEvent<HTMLDivElement>) {
    if (
      typeof event.currentTarget.hasPointerCapture === "function" &&
      event.currentTarget.hasPointerCapture(event.pointerId) &&
      typeof event.currentTarget.releasePointerCapture === "function"
    ) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    resizeStartRef.current = null;
    setResizingEdge(null);
  }

  function resizeKeyDown(
    edge: "left" | "right",
    event: ReactKeyboardEvent<HTMLDivElement>,
  ) {
    const step = 24;
    let next: number | undefined;
    if (edge === "left") {
      if (event.key === "ArrowRight") next = layout.leftWidth + step;
      else if (event.key === "ArrowLeft") next = layout.leftWidth - step;
      else if (event.key === "Home") next = SHELL_LAYOUT.leftMin;
      else if (event.key === "End") next = leftMax;
      if (next === undefined) return;
      event.preventDefault();
      onLeftWidthChange?.(clampLeft(next));
      return;
    }
    if (event.key === "ArrowLeft") next = layout.rightWidth + step;
    else if (event.key === "ArrowRight") next = layout.rightWidth - step;
    else if (event.key === "Home") next = rightMin;
    else if (event.key === "End") next = rightMax;
    if (next === undefined) return;
    event.preventDefault();
    onRightWidthChange?.(clampRight(next));
  }

  useEffect(() => {
    if (!drawerOpen) return;
    const aside = leftRef.current;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const trigger =
      aside?.querySelector<HTMLElement>("[data-drawer-trigger]") ?? null;
    const focusable = () =>
      Array.from(aside?.querySelectorAll<HTMLElement>(focusableSelector) ?? []);
    (trigger ?? focusable()[0])?.focus();

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseDrawer?.();
        return;
      }
      if (event.key !== "Tab") return;
      const controls = focusable();
      const first = controls[0];
      const last = controls.at(-1);
      if (!first || !last) {
        event.preventDefault();
        aside?.focus();
        return;
      }
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      } else if (!aside?.contains(document.activeElement)) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
      // The open-state trigger unmounts with the drawer; re-query so focus
      // lands on the collapsed rail's menu button instead of a detached node.
      aside?.querySelector<HTMLElement>("[data-drawer-trigger]")?.focus();
    };
  }, [drawerOpen, onCloseDrawer]);

  return (
    <div
      className="ui-app-frame"
      data-left-mode={layout.leftMode}
      data-right-mode={layout.rightMode}
      data-drawer={drawerOpen ? "open" : undefined}
      style={style}
    >
      <aside
        ref={leftRef}
        className="ui-app-frame__left"
        aria-label={leftLabel}
      >
        {left}
      </aside>
      {layout.leftMode === "expanded" && onLeftWidthChange ? (
        <div
          className={`ui-app-frame__resizer ui-app-frame__resizer--left${
            resizingEdge === "left" ? " is-resizing" : ""
          }`}
          role="separator"
          aria-orientation="vertical"
          aria-label={leftResizeLabel}
          aria-valuemin={SHELL_LAYOUT.leftMin}
          aria-valuemax={leftMax}
          aria-valuenow={layout.leftWidth}
          tabIndex={0}
          onPointerDown={(event) => beginResize("left", event)}
          onPointerMove={moveResize}
          onPointerUp={endResize}
          onPointerCancel={endResize}
          onKeyDown={(event) => resizeKeyDown("left", event)}
        />
      ) : null}
      {drawerOpen ? (
        <button
          type="button"
          className="ui-app-frame__drawer-backdrop"
          aria-label={drawerLabel}
          onClick={onCloseDrawer}
        />
      ) : null}
      <main
        className="ui-app-frame__center"
        tabIndex={-1}
        inert={drawerOpen || undefined}
      >
        {center}
      </main>
      {layout.rightMode === "closed" ? null : (
        <aside
          className={
            layout.rightMode === "overlay"
              ? "ui-app-frame__right ui-app-frame__right--overlay"
              : "ui-app-frame__right"
          }
          aria-label={rightLabel}
          inert={drawerOpen || undefined}
        >
          {layout.rightMode === "overlay" && onCloseOverlay ? (
            <button
              type="button"
              className="ui-app-frame__overlay-close"
              aria-label={overlayCloseLabel}
              onClick={onCloseOverlay}
            >
              ×
            </button>
          ) : null}
          {right}
        </aside>
      )}
      {layout.rightMode === "panel" && onRightWidthChange ? (
        <div
          className={`ui-app-frame__resizer ui-app-frame__resizer--right${
            resizingEdge === "right" ? " is-resizing" : ""
          }`}
          role="separator"
          aria-orientation="vertical"
          aria-label={rightResizeLabel}
          aria-valuemin={rightMin}
          aria-valuemax={rightMax}
          aria-valuenow={layout.rightWidth}
          tabIndex={0}
          onPointerDown={(event) => beginResize("right", event)}
          onPointerMove={moveResize}
          onPointerUp={endResize}
          onPointerCancel={endResize}
          onKeyDown={(event) => resizeKeyDown("right", event)}
        />
      ) : null}
    </div>
  );
}

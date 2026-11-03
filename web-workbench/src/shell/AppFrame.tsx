import { useEffect, useRef, type CSSProperties, type ReactNode } from "react";
import { SHELL_LAYOUT, type ShellLayoutResult } from "./layout";
import { useResizeHandle } from "./useResizeHandle";
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
  const leftHandle = useResizeHandle({
    growToward: "right",
    width: layout.leftWidth,
    min: SHELL_LAYOUT.leftMin,
    max: leftMax,
    label: leftResizeLabel,
    onChange: (width) => onLeftWidthChange?.(width),
  });
  const rightHandle = useResizeHandle({
    growToward: "left",
    width: layout.rightWidth,
    min: rightMin,
    max: rightMax,
    label: rightResizeLabel,
    onChange: (width) => onRightWidthChange?.(width),
  });

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
            leftHandle.resizing ? " is-resizing" : ""
          }`}
          {...leftHandle.separatorProps}
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
            rightHandle.resizing ? " is-resizing" : ""
          }`}
          {...rightHandle.separatorProps}
        />
      ) : null}
    </div>
  );
}

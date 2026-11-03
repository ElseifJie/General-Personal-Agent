import { X } from "lucide-react";
import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";

import { useResizeHandle } from "../shell/useResizeHandle";
import { IconButton } from "../ui/index";

/** The rail becomes a full-height overlay below this width (workbench.css). */
const PANEL_MODE_QUERY = "(min-width: 1360px)";
const WIDTH_STORAGE_KEY = "vwork.gpa.rail-width.v1";

/** 21rem keeps the default panel the width it was before it became resizable. */
export const RAIL_WIDTH = {
  default: 336,
  min: 280,
  max: 720,
  maxRatio: 0.62,
  /**
   * The transcript keeps this much, so its header's action row never has to
   * break the last button onto a line of its own.
   */
  centerMin: 560,
} as const;

/** Narrow enough to leave the transcript its 400px, wide enough to read. */
export function railWidthBounds(containerWidth: number): {
  min: number;
  max: number;
} {
  const available =
    containerWidth > 0
      ? containerWidth
      : typeof window === "undefined"
        ? 1280
        : window.innerWidth;
  const max = Math.max(
    RAIL_WIDTH.min,
    Math.min(
      RAIL_WIDTH.max,
      Math.round(available * RAIL_WIDTH.maxRatio),
      available - RAIL_WIDTH.centerMin,
    ),
  );
  return { min: Math.min(RAIL_WIDTH.min, max), max };
}

function readWidth(): number {
  if (typeof window === "undefined") return RAIL_WIDTH.default;
  try {
    const raw = window.localStorage.getItem(WIDTH_STORAGE_KEY);
    const parsed = raw === null ? Number.NaN : Number.parseInt(raw, 10);
    return Number.isFinite(parsed) ? parsed : RAIL_WIDTH.default;
  } catch {
    return RAIL_WIDTH.default;
  }
}

function writeWidth(width: number): void {
  try {
    window.localStorage.setItem(WIDTH_STORAGE_KEY, String(Math.round(width)));
  } catch {
    // Private mode: the width simply resets on the next visit.
  }
}

function usePanelMode(): boolean {
  const [panel, setPanel] = useState(() =>
    typeof window === "undefined"
      ? true
      : window.matchMedia(PANEL_MODE_QUERY).matches,
  );
  useEffect(() => {
    const query = window.matchMedia(PANEL_MODE_QUERY);
    const update = () => setPanel(query.matches);
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  return panel;
}

/**
 * The conversation side panel: a header, a scrolling body, and a drag strip on
 * its leading edge. Width is clamped to the space the transcript needs and is
 * remembered across conversations; in overlay mode the CSS owns the width and
 * the strip is not rendered, so no hidden control stays in the tab order.
 */
export function RailPane({
  id,
  label,
  title,
  icon,
  count,
  onClose,
  children,
}: {
  id: string;
  /** Accessible name for the panel region. */
  label: string;
  title: ReactNode;
  icon: ReactNode;
  count?: number;
  onClose: () => void;
  children: ReactNode;
}) {
  const asideRef = useRef<HTMLElement>(null);
  const panelMode = usePanelMode();
  const [width, setWidth] = useState(readWidth);
  const [containerWidth, setContainerWidth] = useState(0);

  useLayoutEffect(() => {
    const container = asideRef.current?.parentElement;
    if (!container) return;
    const measure = () => setContainerWidth(container.clientWidth);
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(container);
    return () => observer.disconnect();
  }, []);

  const bounds = useMemo(() => railWidthBounds(containerWidth), [containerWidth]);
  const current = Math.round(
    Math.min(bounds.max, Math.max(bounds.min, width)),
  );

  useEffect(() => {
    // A narrower window (or a wider navigation rail) shrinks the stored width
    // so the next open starts inside the current bounds.
    if (current !== width) setWidth(current);
  }, [current, width]);

  useEffect(() => {
    writeWidth(current);
  }, [current]);

  const handle = useResizeHandle({
    growToward: "left",
    width: current,
    min: bounds.min,
    max: bounds.max,
    label: "调整侧栏宽度",
    onChange: setWidth,
  });

  return (
    <aside
      ref={asideRef}
      id={id}
      className="gpa-rail-pane"
      aria-label={label}
      style={{ "--gpa-rail-width": `${current}px` } as CSSProperties}
    >
      {panelMode ? (
        <div
          className={`gpa-rail-pane__resizer${
            handle.resizing ? " is-resizing" : ""
          }`}
          {...handle.separatorProps}
        />
      ) : null}
      <header className="gpa-rail-pane__head">
        <h2 className="gpa-rail-pane__title">
          {icon}
          {title}
          {typeof count === "number" ? (
            <span className="gpa-rail-section__count">{count}</span>
          ) : null}
        </h2>
        <IconButton label="收起侧栏" size="small" onClick={onClose}>
          <X size={15} aria-hidden="true" />
        </IconButton>
      </header>
      <div className="gpa-rail-pane__scroll">{children}</div>
    </aside>
  );
}

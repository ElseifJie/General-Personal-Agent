import { MoreHorizontal } from "lucide-react";
import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import { IconButton } from "./Button";

export interface RowActionItem {
  icon?: ReactNode;
  text: string;
  danger?: boolean;
  disabled?: boolean;
  run: () => void;
}

export interface RowActionsMenuProps {
  /** Accessible label for the trigger, naming the row it acts on. */
  label: string;
  items: RowActionItem[];
}

/**
 * The "…" overflow menu for dense table rows: the full action list stays
 * discoverable without forcing the row (and therefore the whole table) to
 * widen. The panel is viewport-anchored so it escapes the table's horizontal
 * scroll container, and closes on outside press, Escape, or scroll.
 */
export function RowActionsMenu({ label, items }: RowActionsMenuProps) {
  const [open, setOpen] = useState(false);
  const [panelStyle, setPanelStyle] = useState<CSSProperties>();
  const containerRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const trigger = containerRef.current?.querySelector<HTMLElement>("button");
    if (trigger) {
      const rect = trigger.getBoundingClientRect();
      const menuHeight = items.length * 34 + 16;
      const opensUp = rect.bottom + menuHeight > window.innerHeight;
      setPanelStyle({
        top: opensUp ? undefined : rect.bottom + 4,
        bottom: opensUp ? window.innerHeight - rect.top + 4 : undefined,
        right: Math.max(8, window.innerWidth - rect.right),
      });
    }
    panelRef.current?.querySelector<HTMLButtonElement>("button")?.focus();
    const onPointerDown = (event: PointerEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      setOpen(false);
      containerRef.current?.querySelector<HTMLButtonElement>("button")?.focus();
    };
    const close = () => setOpen(false);
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    window.addEventListener("resize", close);
    window.addEventListener("scroll", close, true);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("resize", close);
      window.removeEventListener("scroll", close, true);
    };
  }, [open, items.length]);

  return (
    <div className="row-actions-menu" ref={containerRef}>
      <IconButton
        label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        size="small"
        onClick={() => setOpen((current) => !current)}
      >
        <MoreHorizontal size={15} aria-hidden="true" />
      </IconButton>
      {open ? (
        <div
          className="row-actions-menu__panel"
          role="menu"
          ref={panelRef}
          style={panelStyle}
        >
          {items.map((item, index) => (
            <button
              key={index}
              type="button"
              role="menuitem"
              className={`row-actions-menu__item${item.danger ? " row-actions-menu__item--danger" : ""}`}
              disabled={item.disabled}
              onClick={() => {
                setOpen(false);
                item.run();
              }}
            >
              {item.icon}
              {item.text}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

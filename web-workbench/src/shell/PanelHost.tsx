import type { KeyboardEvent, ReactNode } from "react";
import type { PanelDescriptor } from "./shell-state";

export interface PanelHostProps {
  panels: readonly PanelDescriptor[];
  activeId: string | null;
  onSelect(panelId: string): void;
  onClose(panelId: string): void;
  onReorder?(fromIndex: number, toIndex: number): void;
  renderPanel(panel: PanelDescriptor): ReactNode;
  emptyLabel?: string;
}

export function PanelHost({
  panels,
  activeId,
  onSelect,
  onClose,
  onReorder,
  renderPanel,
  emptyLabel = "暂未打开面板。",
}: PanelHostProps) {
  const active = panels.find((panel) => panel.id === activeId) ?? panels[0];

  const handleKeyDown = (event: KeyboardEvent, index: number) => {
    let next: number | undefined;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") {
      next = (index + 1) % panels.length;
    } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      next = (index - 1 + panels.length) % panels.length;
    } else if (event.key === "Home") {
      next = 0;
    } else if (event.key === "End") {
      next = panels.length - 1;
    }
    if (next === undefined) return;
    event.preventDefault();
    const target = panels[next];
    if (target) onSelect(target.id);
  };

  const focusAfterClose = (targetId?: string) => {
    const focus = () => {
      if (targetId) {
        document.getElementById(`panel-tab-${targetId}`)?.focus();
      } else {
        document.querySelector<HTMLElement>(".ui-panel-host__empty")?.focus();
      }
    };
    if (typeof window.requestAnimationFrame === "function") {
      window.requestAnimationFrame(focus);
    } else {
      focus();
    }
  };

  if (panels.length === 0) {
    return (
      <section className="ui-panel-host" aria-label="工作区面板">
        <p className="ui-panel-host__empty" tabIndex={-1}>
          {emptyLabel}
        </p>
      </section>
    );
  }

  return (
    <section className="ui-panel-host" aria-label="工作区面板">
      <div
        className="ui-panel-host__tabs"
        role="tablist"
        aria-label="已打开面板"
      >
        {panels.map((panel, index) => {
          const selected = panel.id === active?.id;
          return (
            <div
              key={panel.id}
              className="ui-panel-host__tab"
              data-active={selected}
            >
              <button
                type="button"
                role="tab"
                id={`panel-tab-${panel.id}`}
                aria-controls={`panel-${panel.id}`}
                aria-selected={selected}
                tabIndex={selected ? 0 : -1}
                onClick={() => onSelect(panel.id)}
                onKeyDown={(event) => handleKeyDown(event, index)}
                draggable
                onDragStart={(event) => {
                  event.dataTransfer.setData("text/plain", panel.id);
                  event.dataTransfer.effectAllowed = "move";
                }}
                onDragOver={(event) => event.preventDefault()}
                onDrop={(event) => {
                  event.preventDefault();
                  const from = panels.findIndex(
                    (candidate) =>
                      candidate.id === event.dataTransfer.getData("text/plain"),
                  );
                  if (from >= 0 && from !== index) onReorder?.(from, index);
                }}
              >
                {panel.title}
              </button>
              <button
                type="button"
                className="ui-panel-host__close"
                aria-label={`关闭 ${panel.title}`}
                onClick={() => {
                  const target = panels[index + 1] ?? panels[index - 1];
                  onClose(panel.id);
                  focusAfterClose(target?.id);
                }}
              >
                ×
              </button>
            </div>
          );
        })}
      </div>
      {active ? (
        <div
          className="ui-panel-host__body"
          role="tabpanel"
          id={`panel-${active.id}`}
          aria-labelledby={`panel-tab-${active.id}`}
        >
          {renderPanel(active)}
        </div>
      ) : null}
    </section>
  );
}

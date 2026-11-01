import type { ReactNode } from "react";
import { AppFrame } from "./AppFrame";
import { PanelHost } from "./PanelHost";
import type { PanelDescriptor } from "./shell-state";
import type { ShellLayoutController } from "./useShellLayout";

export interface WorkspaceFrameProps {
  shell: ShellLayoutController;
  left: ReactNode;
  /** Center content; `children` is accepted as an alias for routing trees. */
  center?: ReactNode;
  children?: ReactNode;
  panels?: readonly PanelDescriptor[];
  activePanelId?: string | null;
  onSelectPanel?(panelId: string): void;
  onClosePanel?(panelId: string): void;
  onReorderPanels?(fromIndex: number, toIndex: number): void;
  /** Overrides the persisted right-panel state for the current view. */
  rightVisible?: boolean;
  renderPanel?(panel: PanelDescriptor): ReactNode;
  drawerOpen?: boolean;
  onCloseDrawer?(): void;
  drawerLabel?: string;
  onCloseOverlay?(): void;
  overlayCloseLabel?: string;
  onLeftWidthChange?(width: number): void;
  onRightWidthChange?(width: number): void;
  leftLabel?: string;
  rightLabel?: string;
  emptyPanelLabel?: string;
}

/**
 * Composes the Harness frame with the panel host. The frame remains unaware of
 * routing; a page supplies navigation, the conversation surface, and the panel
 * renderer, so the same shell can host artifacts, files, or automations.
 */
export function WorkspaceFrame({
  shell,
  left,
  center,
  children,
  panels,
  activePanelId,
  onSelectPanel,
  onClosePanel,
  onReorderPanels,
  rightVisible,
  renderPanel,
  drawerOpen = false,
  onCloseDrawer,
  drawerLabel = "Close navigation",
  onCloseOverlay,
  overlayCloseLabel = "Close panel",
  onLeftWidthChange,
  onRightWidthChange,
  leftLabel = "工作区导航",
  rightLabel = "成果预览",
  emptyPanelLabel = "从任务中选择一项成果，在这里预览。",
}: WorkspaceFrameProps) {
  const hasPanels =
    (panels?.length ?? 0) > 0 &&
    onSelectPanel !== undefined &&
    onClosePanel !== undefined &&
    renderPanel !== undefined;
  const showRight =
    hasPanels && (rightVisible ?? shell.layout.rightMode !== "closed");
  const layout = showRight
    ? shell.layout
    : {
        ...shell.layout,
        rightMode: "closed" as const,
        rightWidth: 0,
        centerWidth: shell.layout.centerWidth + shell.layout.rightWidth,
      };
  const right = !showRight ? undefined : (
    <PanelHost
      panels={panels ?? []}
      activeId={activePanelId ?? null}
      onSelect={onSelectPanel!}
      onClose={onClosePanel!}
      {...(onReorderPanels ? { onReorder: onReorderPanels } : {})}
      renderPanel={renderPanel!}
      emptyLabel={emptyPanelLabel}
    />
  );

  return (
    <AppFrame
      layout={layout}
      left={left}
      center={center ?? children}
      right={right}
      leftLabel={leftLabel}
      rightLabel={rightLabel}
      drawerOpen={drawerOpen}
      drawerLabel={drawerLabel}
      {...(onCloseDrawer ? { onCloseDrawer } : {})}
      {...(onCloseOverlay ? { onCloseOverlay } : {})}
      overlayCloseLabel={overlayCloseLabel}
      {...(onLeftWidthChange ? { onLeftWidthChange } : {})}
      {...(onRightWidthChange ? { onRightWidthChange } : {})}
    />
  );
}

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  SHELL_LAYOUT,
  solveShellLayout,
  type ShellLayoutResult,
} from "./layout";
import {
  DEFAULT_SHELL_STATE,
  loadShellState,
  saveShellState,
  type PanelDescriptor,
  type ShellState,
  type ShellStorage,
} from "./shell-state";

export interface ShellLayoutController {
  layout: ShellLayoutResult;
  state: ShellState;
  setLeftWidth(width: number): void;
  setRightWidth(width: number): void;
  setLeftCollapsed(collapsed: boolean): void;
  setRightOpen(open: boolean): void;
  toggleLeft(): void;
  toggleRight(): void;
  openPanel(panel: PanelDescriptor): void;
  closePanel(panelId: string): void;
  setActivePanel(panelId: string): void;
  reorderPanels(fromIndex: number, toIndex: number): void;
}

function currentViewportWidth(): number {
  if (typeof window === "undefined") return 1440;
  return window.innerWidth || 1440;
}

function browserStorage(): ShellStorage | undefined {
  try {
    if (typeof window === "undefined") return undefined;
    return window.localStorage;
  } catch {
    return undefined;
  }
}

export function useShellLayout(
  input: {
    storage?: ShellStorage;
    initial?: Partial<ShellState>;
    /** Fixed viewport for tests; production reads window.innerWidth. */
    viewport?: number;
  } = {},
): ShellLayoutController {
  const storage = input.storage ?? browserStorage();
  const [state, setState] = useState<ShellState>(() => ({
    ...(storage ? loadShellState(storage) : DEFAULT_SHELL_STATE),
    ...input.initial,
  }));
  const [viewport, setViewport] = useState(
    () => input.viewport ?? currentViewportWidth(),
  );

  useEffect(() => {
    if (input.viewport !== undefined || typeof window === "undefined") return;
    const onResize = () => setViewport(currentViewportWidth());
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [input.viewport]);

  useEffect(() => {
    if (!storage) return;
    saveShellState(storage, state);
  }, [storage, state]);

  const layout = useMemo(
    () =>
      solveShellLayout({
        viewportWidth: viewport,
        requestedRightWidth: state.rightWidth,
        leftWidth: state.leftWidth,
        rightOpen: state.rightOpen,
        leftCollapsed:
          state.leftCollapsed || viewport < SHELL_LAYOUT.autoCollapseBelow,
      }),
    [
      viewport,
      state.leftCollapsed,
      state.leftWidth,
      state.rightOpen,
      state.rightWidth,
    ],
  );

  const setLeftWidth = useCallback(
    (width: number) =>
      setState((current) => ({ ...current, leftWidth: width })),
    [],
  );
  const setRightWidth = useCallback(
    (width: number) =>
      setState((current) => ({ ...current, rightWidth: width })),
    [],
  );
  const setLeftCollapsed = useCallback(
    (collapsed: boolean) =>
      setState((current) => ({ ...current, leftCollapsed: collapsed })),
    [],
  );
  const setRightOpen = useCallback(
    (open: boolean) => setState((current) => ({ ...current, rightOpen: open })),
    [],
  );
  const toggleLeft = useCallback(
    () =>
      setState((current) => ({
        ...current,
        leftCollapsed: !current.leftCollapsed,
      })),
    [],
  );
  const toggleRight = useCallback(
    () =>
      setState((current) => ({ ...current, rightOpen: !current.rightOpen })),
    [],
  );
  const openPanel = useCallback((panel: PanelDescriptor) => {
    setState((current) => {
      const existing = current.panels.find((item) => item.id === panel.id);
      if (
        existing &&
        existing.type === panel.type &&
        existing.title === panel.title &&
        existing.resourceRef === panel.resourceRef &&
        existing.conversationId === panel.conversationId &&
        current.activePanelId === panel.id &&
        current.rightOpen
      ) {
        return current;
      }
      const panels = existing
        ? current.panels.map((item) => (item.id === panel.id ? panel : item))
        : [...current.panels, panel];
      return {
        ...current,
        panels,
        activePanelId: panel.id,
        rightOpen: true,
      };
    });
  }, []);
  const closePanel = useCallback((panelId: string) => {
    setState((current) => {
      const index = current.panels.findIndex((panel) => panel.id === panelId);
      if (index < 0) return current;
      const panels = current.panels.filter((panel) => panel.id !== panelId);
      const activePanelId =
        current.activePanelId === panelId
          ? (panels[Math.min(index, panels.length - 1)]?.id ?? null)
          : current.activePanelId;
      return {
        ...current,
        panels,
        activePanelId,
        rightOpen: panels.length > 0 && current.rightOpen,
      };
    });
  }, []);
  const setActivePanel = useCallback((panelId: string) => {
    setState((current) =>
      current.panels.some((panel) => panel.id === panelId)
        ? { ...current, activePanelId: panelId, rightOpen: true }
        : current,
    );
  }, []);
  const reorderPanels = useCallback((fromIndex: number, toIndex: number) => {
    setState((current) => {
      if (
        fromIndex === toIndex ||
        fromIndex < 0 ||
        toIndex < 0 ||
        fromIndex >= current.panels.length ||
        toIndex >= current.panels.length
      ) {
        return current;
      }
      const panels = [...current.panels];
      const [moved] = panels.splice(fromIndex, 1);
      if (!moved) return current;
      panels.splice(toIndex, 0, moved);
      return { ...current, panels };
    });
  }, []);

  return {
    layout,
    state,
    setLeftWidth,
    setRightWidth,
    setLeftCollapsed,
    setRightOpen,
    toggleLeft,
    toggleRight,
    openPanel,
    closePanel,
    setActivePanel,
    reorderPanels,
  };
}

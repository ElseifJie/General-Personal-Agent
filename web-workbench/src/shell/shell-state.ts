import type { ShellLayoutResult } from "./layout";

export interface PanelDescriptor {
  id: string;
  type: string;
  title: string;
  resourceRef?: string;
  conversationId?: string;
}

export interface ShellState {
  leftWidth: number;
  rightWidth: number;
  leftCollapsed: boolean;
  rightOpen: boolean;
  panels: PanelDescriptor[];
  activePanelId: string | null;
}

export const SHELL_STATE_KEY = "vwork.shell.v1";

export const DEFAULT_SHELL_STATE: ShellState = {
  leftWidth: 280,
  rightWidth: 648,
  leftCollapsed: false,
  rightOpen: false,
  panels: [],
  activePanelId: null,
};

function clamp(value: unknown, min: number, max: number, fallback: number) {
  return typeof value === "number" && Number.isFinite(value)
    ? Math.min(Math.max(value, min), max)
    : fallback;
}

function parsePanel(value: unknown): PanelDescriptor | undefined {
  if (typeof value !== "object" || value === null) return undefined;
  const record = value as Record<string, unknown>;
  if (
    typeof record.id !== "string" ||
    typeof record.type !== "string" ||
    typeof record.title !== "string"
  ) {
    return undefined;
  }
  return {
    id: record.id,
    type: record.type,
    title: record.title,
    ...(typeof record.resourceRef === "string"
      ? { resourceRef: record.resourceRef }
      : {}),
    ...(typeof record.conversationId === "string"
      ? { conversationId: record.conversationId }
      : {}),
  };
}

export function sanitizeShellState(value: unknown): ShellState {
  if (typeof value !== "object" || value === null)
    return { ...DEFAULT_SHELL_STATE };
  const record = value as Record<string, unknown>;
  const panels = Array.isArray(record.panels)
    ? record.panels
        .map(parsePanel)
        .filter((panel): panel is PanelDescriptor => panel !== undefined)
        .slice(0, 12)
    : [];
  const activePanelId =
    typeof record.activePanelId === "string" &&
    panels.some((panel) => panel.id === record.activePanelId)
      ? record.activePanelId
      : (panels[0]?.id ?? null);
  return {
    leftWidth: clamp(record.leftWidth, 264, 420, DEFAULT_SHELL_STATE.leftWidth),
    rightWidth: clamp(
      record.rightWidth,
      300,
      760,
      DEFAULT_SHELL_STATE.rightWidth,
    ),
    leftCollapsed: record.leftCollapsed === true,
    rightOpen: record.rightOpen === true,
    panels,
    activePanelId,
  };
}

export interface ShellStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export function loadShellState(
  storage: ShellStorage,
  key = SHELL_STATE_KEY,
): ShellState {
  try {
    const raw = storage.getItem(key);
    if (!raw) return { ...DEFAULT_SHELL_STATE };
    return sanitizeShellState(JSON.parse(raw) as unknown);
  } catch {
    return { ...DEFAULT_SHELL_STATE };
  }
}

export function saveShellState(
  storage: ShellStorage,
  state: ShellState,
  key = SHELL_STATE_KEY,
): void {
  try {
    storage.setItem(key, JSON.stringify(sanitizeShellState(state)));
  } catch {
    // Storage can be unavailable in private mode; layout then stays in memory.
  }
}

export function applyLayoutToState(
  state: ShellState,
  layout: ShellLayoutResult,
): ShellState {
  return {
    ...state,
    leftWidth: layout.leftWidth,
    rightWidth: layout.rightWidth,
    leftCollapsed: layout.leftMode === "collapsed",
    rightOpen: layout.rightMode !== "closed",
  };
}

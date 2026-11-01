/**
 * Shell geometry is solved in one place so the frame, persisted resize state,
 * and tests agree on every boundary. Values mirror the Harness workspace:
 * a 280px default left rail (264–420), 56px collapsed, collapsing below 1024;
 * a right panel that starts at 45%, never below 300px, never above 70%, keeps
 * the center at least 400px, and becomes a full-screen overlay below 768.
 */
export const SHELL_LAYOUT = {
  leftDefault: 280,
  leftMin: 264,
  leftMax: 420,
  leftCollapsed: 56,
  autoCollapseBelow: 1024,
  rightMin: 300,
  rightInitialRatio: 0.45,
  rightMaxRatio: 0.7,
  centerMin: 400,
  overlayBelow: 768,
} as const;

export type ShellLeftMode = "expanded" | "collapsed";
export type ShellRightMode = "panel" | "overlay" | "closed";

export interface ShellLayoutInput {
  viewportWidth: number;
  requestedRightWidth?: number;
  leftWidth?: number;
  rightOpen?: boolean;
  leftCollapsed?: boolean;
}

export interface ShellLayoutResult {
  leftWidth: number;
  rightWidth: number;
  centerWidth: number;
  leftMode: ShellLeftMode;
  rightMode: ShellRightMode;
  /** Below the 768px overlay boundary the shell switches to drawer mode. */
  mobile: boolean;
}

function clamp(value: number, min: number, max: number): number {
  if (max < min) return max;
  return Math.min(Math.max(value, min), max);
}

export function solveShellLayout(input: ShellLayoutInput): ShellLayoutResult {
  const viewport = Math.max(0, Math.floor(input.viewportWidth));
  const mobile = viewport < SHELL_LAYOUT.overlayBelow;
  const collapsed =
    input.leftCollapsed ?? viewport < SHELL_LAYOUT.autoCollapseBelow;
  const leftMode: ShellLeftMode = collapsed ? "collapsed" : "expanded";

  let leftWidth = collapsed
    ? SHELL_LAYOUT.leftCollapsed
    : clamp(
        input.leftWidth ?? SHELL_LAYOUT.leftDefault,
        SHELL_LAYOUT.leftMin,
        SHELL_LAYOUT.leftMax,
      );
  if (!collapsed) {
    leftWidth = Math.min(
      leftWidth,
      Math.max(SHELL_LAYOUT.leftCollapsed, viewport - SHELL_LAYOUT.centerMin),
    );
  }
  leftWidth = clamp(leftWidth, 0, viewport);

  if (input.rightOpen === false) {
    return {
      leftWidth,
      rightWidth: 0,
      centerWidth: Math.max(0, viewport - leftWidth),
      leftMode,
      rightMode: "closed",
      mobile,
    };
  }

  if (mobile) {
    const requested = input.requestedRightWidth ?? viewport;
    const rightWidth = clamp(
      requested,
      Math.min(SHELL_LAYOUT.rightMin, viewport),
      viewport,
    );
    return {
      leftWidth,
      rightWidth,
      centerWidth: Math.max(0, viewport - leftWidth),
      leftMode,
      rightMode: "overlay",
      mobile,
    };
  }

  const maxByRatio = Math.round(viewport * SHELL_LAYOUT.rightMaxRatio);
  const maxByCenter = viewport - leftWidth - SHELL_LAYOUT.centerMin;
  const maxRight = Math.max(0, Math.min(maxByRatio, maxByCenter));
  const minRight = Math.min(SHELL_LAYOUT.rightMin, maxRight);
  const requested =
    input.requestedRightWidth ??
    Math.round(viewport * SHELL_LAYOUT.rightInitialRatio);
  const rightWidth = Math.round(clamp(requested, minRight, maxRight));

  return {
    leftWidth,
    rightWidth,
    centerWidth: Math.max(0, viewport - leftWidth - rightWidth),
    leftMode,
    rightMode: "panel",
    mobile,
  };
}

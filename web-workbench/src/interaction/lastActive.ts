/**
 * Per-device memory of the last conversation the user actually looked at.
 *
 * The voice entry uses this as its "recent" heuristic and the "当前会话"
 * entry uses it to return to the surface the user left. localStorage keeps the
 * window scoped to this browser/device, which is the intended privacy
 * boundary; it is never sent to the server.
 */

import type { Channel } from "../gpaApi";

const STORAGE_KEY = "gpa.lastActiveConversation";

export interface LastActiveConversation {
  conversationId: string;
  at: number;
  /** Surface the user was last on, when the route told us. */
  channel?: Channel;
}

function storage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

export function rememberActiveConversation(
  conversationId: string,
  channel?: Channel,
): void {
  const store = storage();
  if (!store || !conversationId) return;
  try {
    const previous = readLastActiveConversation();
    // Turn writes only name the conversation; keep the surface the user was
    // last on instead of dropping it on every message.
    const nextChannel =
      channel ??
      (previous?.conversationId === conversationId
        ? previous.channel
        : undefined);
    store.setItem(
      STORAGE_KEY,
      JSON.stringify({
        conversationId,
        at: Date.now(),
        ...(nextChannel ? { channel: nextChannel } : {}),
      } satisfies LastActiveConversation),
    );
  } catch {
    // Storage can be full/disabled; voice still works without the heuristic.
  }
}

export function readLastActiveConversation(): LastActiveConversation | null {
  const store = storage();
  if (!store) return null;
  try {
    const raw = store.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<LastActiveConversation>;
    if (
      typeof parsed.conversationId !== "string" ||
      !parsed.conversationId ||
      typeof parsed.at !== "number" ||
      !Number.isFinite(parsed.at)
    ) {
      return null;
    }
    return {
      conversationId: parsed.conversationId,
      at: parsed.at,
      ...(parsed.channel === "text" || parsed.channel === "voice"
        ? { channel: parsed.channel }
        : {}),
    };
  } catch {
    return null;
  }
}

export function clearLastActiveConversation(): void {
  const store = storage();
  if (!store) return;
  try {
    store.removeItem(STORAGE_KEY);
  } catch {
    // ignore
  }
}

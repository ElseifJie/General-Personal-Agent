/**
 * Voice entry resolver (ADR-019).
 *
 * The voice page must not make the user choose "new vs continue" before
 * speaking. It resolves a target in this order:
 *
 *   fresh (explicit new) > current conversation (from a conversation page)
 *   > running task > recent activity on this device > new draft
 *
 * The resolver is single-flight: React StrictMode runs effects twice in
 * development, and two parallel resolutions would create two drafts.
 */

import { gpaApi, isAuthError } from "../gpaApi";
import { readLastActiveConversation } from "./lastActive";

export const RECENT_WINDOW_MS = 15 * 60 * 1000;
export const RUNNING_TASK_WINDOW_MS = 24 * 60 * 60 * 1000;

export type VoiceEntryReason = "current" | "task" | "recent";

export type VoiceEntryTarget =
  | { kind: "resume"; conversationId: string; reason: VoiceEntryReason }
  | { kind: "new"; conversationId: string };

const inFlight = new Map<string, Promise<VoiceEntryTarget>>();

async function getConversationOrNull(conversationId: string) {
  try {
    return await gpaApi.getConversation(conversationId);
  } catch (error) {
    if (isAuthError(error)) throw error;
    return null;
  }
}

async function findRunningTaskConversation(): Promise<VoiceEntryTarget | null> {
  try {
    const response = await gpaApi.listConversations({
      hasRunningTask: true,
      limit: 5,
    });
    const target = response.items.find((item) => item.status !== "archived");
    if (!target) return null;
    const updatedAt = Date.parse(target.updatedAt);
    if (
      Number.isFinite(updatedAt) &&
      Date.now() - updatedAt > RUNNING_TASK_WINDOW_MS
    ) {
      return null;
    }
    return {
      kind: "resume",
      conversationId: target.conversationId,
      reason: "task",
    };
  } catch (error) {
    if (isAuthError(error)) throw error;
    return null;
  }
}

async function resolve({
  fresh,
  currentId,
}: {
  fresh: boolean;
  currentId?: string | null;
}): Promise<VoiceEntryTarget> {
  if (!fresh) {
    // D10: clicking the sidebar "voice" entry from a conversation page must
    // continue that conversation, not jump to another one with a running task.
    if (currentId) {
      const detail = await getConversationOrNull(currentId);
      if (detail && detail.conversation.status !== "archived") {
        return {
          kind: "resume",
          conversationId: currentId,
          reason: "current",
        };
      }
    }

    const running = await findRunningTaskConversation();
    if (running) return running;

    const last = readLastActiveConversation();
    if (last && Date.now() - last.at <= RECENT_WINDOW_MS) {
      const detail = await getConversationOrNull(last.conversationId);
      if (
        detail &&
        !detail.conversation.isDraft &&
        detail.conversation.status !== "archived"
      ) {
        return {
          kind: "resume",
          conversationId: last.conversationId,
          reason: "recent",
        };
      }
    }
  }

  const created = await gpaApi.createConversation({
    channel: "voice",
    draft: true,
  });
  return { kind: "new", conversationId: created.conversationId };
}

export function resolveVoiceEntry({
  fresh,
  currentId,
}: {
  fresh: boolean;
  currentId?: string | null;
}): Promise<VoiceEntryTarget> {
  const key = fresh ? "fresh" : `auto:${currentId ?? ""}`;
  const existing = inFlight.get(key);
  if (existing) return existing;
  const task = resolve({ fresh, currentId }).finally(() => {
    inFlight.delete(key);
  });
  inFlight.set(key, task);
  return task;
}

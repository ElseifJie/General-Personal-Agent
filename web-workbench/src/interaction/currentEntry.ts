/**
 * "当前会话" entry (sidebar).
 *
 * A conversation is one object that can carry both text and voice turns, so
 * this entry resumes the conversation itself instead of forcing a channel.
 * The surface the user was last on wins; when that was never recorded (e.g.
 * another device), the conversation's own channel decides: voice turns open
 * the voice page, text turns the text page.
 *
 * The resolver never creates anything. When there is no conversation to
 * resume it falls back to the new-conversation page.
 */

import {
  gpaApi,
  isAuthError,
  type Channel,
  type ConversationSummary,
} from "../gpaApi";
import { readLastActiveConversation } from "./lastActive";

const CONVERSATION_PATH = /^\/interact\/(?:text|voice)\/([^/]+)$/;

/** The conversation id of an open conversation route, else `null`. */
export function conversationIdFromPath(pathname: string): string | null {
  const match = pathname.match(CONVERSATION_PATH);
  return match ? decodeURIComponent(match[1]) : null;
}

function channelOf(
  conversation: Pick<ConversationSummary, "activeChannel" | "lastTurnChannel">,
): Channel {
  return conversation.lastTurnChannel ?? conversation.activeChannel;
}

function hrefFor(
  conversation: ConversationSummary,
  preferred?: Channel,
): string {
  const channel = preferred ?? channelOf(conversation);
  return `/interact/${channel}/${conversation.conversationId}`;
}

function isResumable(conversation: ConversationSummary): boolean {
  return conversation.status !== "archived" && !conversation.isDraft;
}

async function hrefForId(
  conversationId: string,
  preferred?: Channel,
): Promise<string | null> {
  try {
    const detail = await gpaApi.getConversation(conversationId);
    return isResumable(detail.conversation)
      ? hrefFor(detail.conversation, preferred)
      : null;
  } catch (error) {
    if (isAuthError(error)) throw error;
    return null;
  }
}

async function hrefForRunningTask(): Promise<string | null> {
  try {
    const response = await gpaApi.listConversations({
      hasRunningTask: true,
      limit: 5,
    });
    const target = response.items.find(isResumable);
    return target ? hrefFor(target) : null;
  } catch (error) {
    if (isAuthError(error)) throw error;
    return null;
  }
}

async function hrefForMostRecent(): Promise<string | null> {
  try {
    const response = await gpaApi.listConversations({ limit: 1 });
    const target = response.items.find(isResumable);
    return target ? hrefFor(target) : null;
  } catch (error) {
    if (isAuthError(error)) throw error;
    return null;
  }
}

/**
 * Resolve where "当前会话" should go: the conversation last active on this
 * device, then a conversation with a running task, then the most recent
 * conversation, then the new-conversation page.
 */
export async function resolveCurrentEntryHref(): Promise<string> {
  const last = readLastActiveConversation();
  if (last) {
    const href = await hrefForId(last.conversationId, last.channel);
    if (href) return href;
  }

  const running = await hrefForRunningTask();
  if (running) return running;

  const recent = await hrefForMostRecent();
  if (recent) return recent;

  return "/interact/text";
}

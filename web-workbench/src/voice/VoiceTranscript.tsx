import { Mic } from "lucide-react";

import type { TimelineItem } from "../gpaApi";
import { ConversationTimeline } from "../interaction/ConversationTimeline";
import type { VoiceStatus } from "./useVoiceSession";

export function VoiceTranscript({
  items,
  streaming,
  runningTurnId,
  partialText,
  liveUserText,
  liveAssistantText,
  status,
  emptyDescription = "按住下方按钮说话；这轮对话会写入同一个 conversation。",
  onArtifactSelect,
}: {
  items: TimelineItem[];
  streaming: Record<string, string>;
  runningTurnId: string | null;
  partialText: string;
  liveUserText: string;
  liveAssistantText: string;
  status: VoiceStatus;
  /** Empty-state copy; a new conversation says the first turn opens it. */
  emptyDescription?: string;
  onArtifactSelect?: (artifactId: string) => void;
}) {
  const normalized = liveUserText.trim();
  const alreadyInTimeline = Boolean(
    normalized &&
      items.some(
        (item) => item.kind === "turn" && (item.userText ?? "").trim() === normalized,
      ),
  );
  const showLive = Boolean(normalized) && !alreadyInTimeline;
  const displayItems: TimelineItem[] = showLive
    ? [
        ...items,
        {
          kind: "turn",
          seq: Number.MAX_SAFE_INTEGER,
          occurredAt: new Date().toISOString(),
          turnId: "live-voice-turn",
          channel: "voice",
          userText: liveUserText,
          assistantText: liveAssistantText,
          status:
            status === "thinking" ||
            status === "speaking" ||
            status === "listening" ||
            status === "working"
              ? "running"
              : "completed",
        },
      ]
    : items;
  const displayStreaming = showLive
    ? { ...streaming, "live-voice-turn": liveAssistantText }
    : streaming;
  const displayRunning = showLive
    ? status === "thinking" ||
      status === "speaking" ||
      status === "listening" ||
      status === "working"
      ? "live-voice-turn"
      : null
    : runningTurnId;
  return (
    <div className="gpa-voice-transcript">
      <ConversationTimeline
        items={displayItems}
        streaming={displayStreaming}
        runningTurnId={displayRunning}
        emptyTitle="还没有语音内容"
        emptyDescription={emptyDescription}
        onArtifactSelect={onArtifactSelect}
      />
      {partialText ? (
        <div className="gpa-user gpa-user--partial" aria-live="polite">
          <div className="gpa-user__bubble">{partialText}</div>
          <p className="gpa-user__meta">
            <Mic size={11} aria-hidden="true" />
            语音识别中
          </p>
        </div>
      ) : null}
    </div>
  );
}

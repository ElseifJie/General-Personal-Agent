import { Mic, Square } from "lucide-react";
import { useRef } from "react";

import { Button } from "../ui/index";
import { useHoldToTalk } from "./useHoldToTalk";
import type { VoiceStatus } from "./useVoiceSession";

export function VoiceComposer({
  status,
  connected,
  onStart,
  onStop,
  onInterrupt,
  isRecording,
}: {
  status: VoiceStatus;
  connected: boolean;
  onStart: () => void;
  onStop: () => void;
  onInterrupt: () => void;
  isRecording: boolean;
}) {
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const busy = status === "thinking" || status === "speaking";
  const disabled = !connected || status === "connecting" || status === "error";
  const { hold, release } = useHoldToTalk({
    enabled: !disabled && !isRecording,
    onStart,
    onStop,
    buttonRef,
  });

  return (
    <div className="gpa-voice-composer">
      <button
        ref={buttonRef}
        type="button"
        className={`gpa-talk${isRecording ? " is-recording" : ""}`}
        aria-pressed={isRecording}
        aria-label="按住说话，松开结束"
        aria-keyshortcuts="Space Enter"
        title={
          disabled
            ? status === "error"
              ? "语音链路异常：先点“重连语音”，或切换到文本交互"
              : !connected
                ? "语音连接已断开，正在重连…"
                : "语音正在连接，连上后按住即可说话"
            : "按住说话，松开结束（也可以按住空格或回车）"
        }
        disabled={disabled}
        onPointerDown={(event) => {
          event.preventDefault();
          event.currentTarget.setPointerCapture(event.pointerId);
          hold("pointer");
        }}
        onPointerUp={(event) => {
          event.preventDefault();
          release("pointer");
        }}
        onPointerCancel={() => release("pointer")}
        onLostPointerCapture={() => release("pointer")}
        onContextMenu={(event) => event.preventDefault()}
      >
        <span className="gpa-talk__orb" aria-hidden="true">
          <Mic size={20} />
        </span>
        <span className="gpa-talk__copy">
          <strong>{isRecording ? "松开结束" : "按住说话"}</strong>
          <span>{isRecording ? "正在采集麦克风音频" : "按住空格/回车说话"}</span>
        </span>
      </button>
      {busy ? (
        <Button
          variant="secondary"
          size="compact"
          onClick={onInterrupt}
          className="gpa-talk__interrupt"
        >
          <Square size={13} aria-hidden="true" />
          打断
        </Button>
      ) : null}
    </div>
  );
}

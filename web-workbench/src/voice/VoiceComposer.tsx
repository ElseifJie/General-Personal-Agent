import { Mic, Square } from "lucide-react";
import { useRef } from "react";

import { Button } from "../ui/index";
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
  const holdingRef = useRef(false);
  const busy = status === "thinking" || status === "speaking";
  const disabled = !connected || status === "connecting" || status === "error";

  function start() {
    if (disabled || isRecording) return;
    holdingRef.current = true;
    onStart();
  }

  function stop() {
    if (!holdingRef.current) return;
    holdingRef.current = false;
    onStop();
  }

  return (
    <div className="gpa-voice-composer">
      <button
        type="button"
        className={`gpa-talk${isRecording ? " is-recording" : ""}`}
        aria-pressed={isRecording}
        aria-label="按住说话，松开结束"
        disabled={disabled}
        onPointerDown={(event) => {
          event.preventDefault();
          event.currentTarget.setPointerCapture(event.pointerId);
          start();
        }}
        onPointerUp={(event) => {
          event.preventDefault();
          stop();
        }}
        onPointerCancel={stop}
        onContextMenu={(event) => event.preventDefault()}
        onKeyDown={(event) => {
          if (event.key !== " " && event.key !== "Enter") return;
          event.preventDefault();
          if (!event.repeat) start();
        }}
        onKeyUp={(event) => {
          if (event.key !== " " && event.key !== "Enter") return;
          event.preventDefault();
          stop();
        }}
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

import { useCallback, useEffect, useRef, useState } from "react";

import { useVoiceAudio } from "./useVoiceAudio";

export type VoiceStatus =
  | "idle"
  | "connecting"
  | "listening"
  | "thinking"
  | "working"
  | "speaking"
  | "offline"
  | "error";

export interface VoiceMetrics {
  asrMs: number;
  ttftMs: number;
  firstAudioMs: number;
  totalMs: number;
}

export interface VoiceTask {
  taskId: string;
  status: string;
  title: string;
  progress: number;
  summary: string;
}

export interface VoiceArtifact {
  artifactId: string;
  taskId: string;
  name: string;
  downloadUrl: string | null;
}

interface VoiceSessionOptions {
  onAuthRequired: () => void;
  /** Play one short server-side cue when the entry resumed a conversation. */
  resumeCue?: boolean;
  /** Fired when ASR produced a non-empty final and a turn is about to start. */
  onTurnAccepted?: () => void;
  onTurnSettled?: () => void;
}

const EMPTY_METRICS: VoiceMetrics = {
  asrMs: 0,
  ttftMs: 0,
  firstAudioMs: 0,
  totalMs: 0,
};

function isAuthClose(code: number) {
  return code === 4401 || code === 4403;
}

function notifyAuth(onAuthRequired: () => void) {
  window.dispatchEvent(new Event("wb:unauthorized"));
  onAuthRequired();
}

export function useVoiceSession(
  conversationId: string | null | undefined,
  { onAuthRequired, resumeCue, onTurnAccepted, onTurnSettled }: VoiceSessionOptions,
) {
  const [status, setStatus] = useState<VoiceStatus>(
    conversationId ? "connecting" : "idle",
  );
  const [connected, setConnected] = useState(false);
  const [partialText, setPartialText] = useState("");
  const [userText, setUserText] = useState("");
  const [assistantText, setAssistantText] = useState("");
  const [fillerText, setFillerText] = useState("");
  const [metrics, setMetrics] = useState<VoiceMetrics>(EMPTY_METRICS);
  const [error, setError] = useState<string | null>(null);
  const [tasks, setTasks] = useState<VoiceTask[]>([]);
  const [artifacts, setArtifacts] = useState<VoiceArtifact[]>([]);
  const [sampleRate, setSampleRate] = useState(24_000);
  const [reconnectNonce, setReconnectNonce] = useState(0);

  const audio = useVoiceAudio();
  const wsRef = useRef<WebSocket | null>(null);
  const intentionalCloseRef = useRef(false);
  const reconnectAttemptsRef = useRef(0);
  const conflictRetryRef = useRef(0);
  const resumeCueSentRef = useRef(false);
  const resumeCueTimerRef = useRef<number | null>(null);
  const reconnectTimerRef = useRef<number | null>(null);
  const recordingRef = useRef(false);
  const startingRef = useRef(false);
  const stopRequestedRef = useRef(false);
  const sentAudioRef = useRef(false);
  const turnStartedRef = useRef(0);
  const firstDeltaRef = useRef(0);
  const firstAudioRef = useRef(0);
  const audioStartedRef = useRef(false);
  const settledRef = useRef(onTurnSettled);
  const acceptedRef = useRef(onTurnAccepted);
  const authRef = useRef(onAuthRequired);
  acceptedRef.current = onTurnAccepted;
  settledRef.current = onTurnSettled;
  authRef.current = onAuthRequired;

  useEffect(() => {
    resumeCueSentRef.current = false;
  }, [conversationId]);

  const resetLiveTurn = useCallback(() => {
    setPartialText("");
    setUserText("");
    setAssistantText("");
    setFillerText("");
    setMetrics(EMPTY_METRICS);
    turnStartedRef.current = 0;
    firstDeltaRef.current = 0;
    firstAudioRef.current = 0;
  }, []);

  const send = useCallback((payload: Record<string, unknown>) => {
    const socket = wsRef.current;
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify(payload));
      return true;
    }
    return false;
  }, []);

  const connect = useCallback(() => {
    if (!conversationId) return;
    intentionalCloseRef.current = false;
    setStatus("connecting");
    setError(null);
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    const resumeQuery =
      resumeCue && !resumeCueSentRef.current ? "&resumed=1" : "";
    const socket = new WebSocket(
      `${protocol}://${window.location.host}/ws/session?conversationId=${encodeURIComponent(
        conversationId,
      )}${resumeQuery}`,
    );
    wsRef.current = socket;

    socket.onmessage = (event) => {
      let message: Record<string, unknown>;
      try {
        message = JSON.parse(String(event.data)) as Record<string, unknown>;
      } catch {
        return;
      }
      const kind = String(message.type ?? "");
      if (kind === "ready") {
        setConnected(true);
        setStatus("idle");
        reconnectAttemptsRef.current = 0;
        conflictRetryRef.current = 0;
        resumeCueSentRef.current = true;
        setSampleRate(Number(message.sampleRate ?? 24_000) || 24_000);
      } else if (kind === "partial") {
        setPartialText(String(message.text ?? ""));
        setStatus("listening");
      } else if (kind === "final") {
        const text = String(message.text ?? "");
        setPartialText("");
        setUserText(text);
        setAssistantText("");
        setMetrics((current) => ({
          ...current,
          asrMs: Number(message.asr_ms ?? 0) || current.asrMs,
        }));
        turnStartedRef.current = performance.now();
        firstDeltaRef.current = 0;
        firstAudioRef.current = 0;
        setStatus(text ? "thinking" : "idle");
        if (text.trim()) acceptedRef.current?.();
      } else if (kind === "text.delta") {
        const text = String(message.text ?? "");
        if (!text) return;
        if (firstDeltaRef.current === 0 && turnStartedRef.current) {
          firstDeltaRef.current = Math.max(
            1,
            Math.round(performance.now() - turnStartedRef.current),
          );
        }
        setFillerText("");
        setAssistantText((current) => current + text);
        if (!audioStartedRef.current) setStatus("thinking");
      } else if (kind === "filler") {
        // The filler is spoken by the server; show it so the user can read
        // what they just heard instead of guessing at a short phrase.
        setFillerText(String(message.text ?? ""));
        setStatus(audioStartedRef.current ? "speaking" : "working");
      } else if (kind === "tool_progress") {
        setStatus(audioStartedRef.current ? "speaking" : "working");
      } else if (kind === "resume_cue") {
        if (resumeCueTimerRef.current !== null) {
          window.clearTimeout(resumeCueTimerRef.current);
        }
        const durationMs = Number(message.duration_ms ?? 0) || 1500;
        setStatus("speaking");
        resumeCueTimerRef.current = window.setTimeout(() => {
          resumeCueTimerRef.current = null;
          if (turnStartedRef.current) return;
          audioStartedRef.current = false;
          setStatus((current) => (current === "speaking" ? "idle" : current));
        }, durationMs + 400);
      } else if (kind === "audio") {
        if (firstAudioRef.current === 0 && turnStartedRef.current) {
          firstAudioRef.current = Math.max(
            1,
            Math.round(performance.now() - turnStartedRef.current),
          );
        }
        if (!audioStartedRef.current) {
          audioStartedRef.current = true;
          firstAudioRef.current = firstAudioRef.current || 1;
        }
        audio.playPcm(
          String(message.data ?? ""),
          Number(message.sample_rate ?? 24_000) || 24_000,
        );
        setStatus("speaking");
      } else if (kind === "audio_stop") {
        audio.stopPlayback();
      } else if (kind === "interrupted") {
        audio.stopPlayback();
        if (resumeCueTimerRef.current !== null) {
          window.clearTimeout(resumeCueTimerRef.current);
          resumeCueTimerRef.current = null;
        }
        audioStartedRef.current = false;
        setPartialText("");
        setFillerText("");
        setStatus("idle");
      } else if (kind === "task") {
        const task = message.task as Record<string, unknown> | undefined;
        if (!task) return;
        const taskId = String(task.taskId ?? task.task_id ?? "");
        if (!taskId) return;
        setTasks((current) => {
          const next = current.filter((item) => item.taskId !== taskId);
          next.push({
            taskId,
            status: String(task.status ?? "queued"),
            title: String(task.goal ?? task.summary ?? task.taskType ?? "任务"),
            progress: Number(task.progress ?? 0),
            summary: String(task.summary ?? ""),
          });
          return next;
        });
      } else if (kind === "artifact") {
        const artifact = message.artifact as Record<string, unknown> | undefined;
        if (!artifact) return;
        const artifactId = String(artifact.artifactId ?? artifact.artifact_id ?? "");
        const taskId = String(artifact.taskId ?? artifact.task_id ?? "");
        if (!artifactId) return;
        setArtifacts((current) => {
          const next = current.filter((item) => item.artifactId !== artifactId);
          next.push({
            artifactId,
            taskId,
            name: String(artifact.name ?? artifactId),
            downloadUrl:
              taskId && artifactId
                ? `/api/tasks/${encodeURIComponent(taskId)}/artifacts/${encodeURIComponent(
                    artifactId,
                  )}/download`
                : null,
          });
          return next;
        });
      } else if (kind === "turn.metrics") {
        const raw = (message.metrics ?? {}) as Record<string, unknown>;
        setMetrics({
          asrMs: Number(raw.asr_ms ?? 0) || 0,
          ttftMs: Number(raw.ttft_ms ?? 0) || firstDeltaRef.current,
          firstAudioMs: Number(raw.first_audio_ms ?? 0) || firstAudioRef.current,
          totalMs: Number(raw.total_ms ?? 0) || 0,
        });
        setStatus("idle");
        if (message.text) setAssistantText(String(message.text));
        setFillerText("");
        if (resumeCueTimerRef.current !== null) {
          window.clearTimeout(resumeCueTimerRef.current);
          resumeCueTimerRef.current = null;
        }
        audioStartedRef.current = false;
        setStatus("idle");
        settledRef.current?.();
      } else if (kind === "error") {
        setError(String(message.message ?? "语音链路出错"));
        setStatus("error");
      }
    };

    socket.onopen = () => setConnected(true);
    socket.onerror = () => setConnected(false);
    socket.onclose = (event) => {
      setConnected(false);
      if (intentionalCloseRef.current) {
        setStatus("idle");
        return;
      }
      if (isAuthClose(event.code)) {
        notifyAuth(authRef.current);
        setStatus("error");
        setError("登录已失效，请重新登录");
        return;
      }
      if (event.code === 4400) {
        setError("语音连接必须带 conversationId");
        setStatus("error");
        return;
      }
      if (event.code === 4404) {
        setError("会话不存在或不属于当前用户");
        setStatus("error");
        return;
      }
      if (event.code === 4409) {
        // Another tab/window holds the only voice socket for this conversation.
        // One short retry absorbs the StrictMode double-connect in development.
        if (conflictRetryRef.current < 1) {
          conflictRetryRef.current += 1;
          reconnectTimerRef.current = window.setTimeout(() => {
            reconnectTimerRef.current = null;
            connect();
          }, 400);
          return;
        }
        setError("这个会话正在另一个窗口使用语音，请先关闭那个窗口。");
        setStatus("error");
        return;
      }
      if (event.code === 4429) {
        setError("语音连接过于频繁，请稍后再试或切换到文本");
        setStatus("error");
        return;
      }
      setStatus("offline");
      if (reconnectAttemptsRef.current < 3) {
        reconnectAttemptsRef.current += 1;
        reconnectTimerRef.current = window.setTimeout(() => {
          reconnectTimerRef.current = null;
          connect();
        }, 1200 * reconnectAttemptsRef.current);
      }
    };
  }, [audio, conversationId]);

  useEffect(() => {
    if (!conversationId) {
      setStatus("idle");
      return;
    }
    resetLiveTurn();
    setTasks([]);
    setArtifacts([]);
    connect();
    return () => {
      intentionalCloseRef.current = true;
      if (reconnectTimerRef.current !== null) {
        window.clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      if (resumeCueTimerRef.current !== null) {
        window.clearTimeout(resumeCueTimerRef.current);
        resumeCueTimerRef.current = null;
      }
      wsRef.current?.close();
      wsRef.current = null;
      audio.stopMic();
      audio.stopPlayback();
    };
    // reconnectNonce intentionally re-runs the effect for the manual reconnect.
  }, [conversationId, connect, resetLiveTurn, audio, reconnectNonce]);

  const startTalking = useCallback(async () => {
    if (!conversationId) return;
    setError(null);
    if (status === "speaking" || status === "thinking" || audio.isPlaying()) {
      send({ type: "interrupt" });
      audio.stopPlayback();
    }
    startingRef.current = true;
    stopRequestedRef.current = false;
    sentAudioRef.current = false;
    setPartialText("");
    setUserText("");
    setAssistantText("");
    setStatus("listening");
    try {
      await audio.startMic((chunk) => {
        sentAudioRef.current = true;
        send({ type: "audio", data: chunk });
      });
      startingRef.current = false;
      if (stopRequestedRef.current) {
        stopRequestedRef.current = false;
        audio.stopMic();
        // Close the ASR stream even for a very short tap; an empty final is
        // cheaper than leaving the upstream ASR socket open.
        send({ type: "audio_end" });
        setStatus("idle");
        return;
      }
      recordingRef.current = true;
      setStatus("listening");
    } catch (caught) {
      startingRef.current = false;
      stopRequestedRef.current = false;
      recordingRef.current = false;
      const name = caught instanceof DOMException ? caught.name : "";
      setStatus("error");
      setError(
        name === "NotAllowedError"
          ? "麦克风权限被拒绝。可以在浏览器设置中允许，或切换到文本交互。"
          : caught instanceof Error
            ? caught.message
            : "无法打开麦克风",
      );
    }
  }, [audio, conversationId, send, status]);

  const stopTalking = useCallback(() => {
    if (startingRef.current && !recordingRef.current) {
      // The user released while the AudioContext/worklet was still opening.
      stopRequestedRef.current = true;
      return;
    }
    if (!recordingRef.current) return;
    recordingRef.current = false;
    audio.stopMic();
    if (!send({ type: "audio_end" })) {
      setError("语音连接已断开");
      setStatus("offline");
      return;
    }
    setStatus("thinking");
  }, [audio, send]);

  const interrupt = useCallback(() => {
    send({ type: "interrupt" });
    audio.stopPlayback();
    setPartialText("");
    setStatus("idle");
  }, [audio, send]);

  const reconnect = useCallback(() => {
    intentionalCloseRef.current = true;
    wsRef.current?.close();
    wsRef.current = null;
    setConnected(false);
    setError(null);
    setStatus("connecting");
    setReconnectNonce((value) => value + 1);
  }, []);

  return {
    status,
    connected,
    partialText,
    userText,
    assistantText,
    fillerText,
    metrics,
    error,
    tasks,
    artifacts,
    sampleRate,
    isRecording: () => recordingRef.current,
    startTalking,
    stopTalking,
    interrupt,
    reconnect,
  };
}

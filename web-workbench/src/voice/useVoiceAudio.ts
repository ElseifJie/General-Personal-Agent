import { useCallback, useEffect, useMemo, useRef } from "react";

function base64ToPcm(value: string): Int16Array {
  const raw = atob(value);
  // s16le PCM is 2 bytes per sample; an odd-length frame would throw in the
  // Int16Array constructor and misalign every sample after it.
  const usable = raw.length - (raw.length % 2);
  const bytes = new Uint8Array(usable);
  for (let i = 0; i < usable; i += 1) bytes[i] = raw.charCodeAt(i);
  return new Int16Array(bytes.buffer);
}

/**
 * Schedule each playback segment this far in the future. Streaming TTS frames
 * can arrive a little late at the start of a turn (cold WS session, network
 * jitter); a small playout cushion keeps them from opening an audible gap on
 * the first one or two characters. Applied once per segment, not per frame.
 */
const PLAYOUT_DELAY_S = 0.1;

function pcmToBase64(bytes: Uint8Array): string {
  let binary = "";
  const step = 0x8000;
  for (let i = 0; i < bytes.length; i += step) {
    binary += String.fromCharCode(...bytes.subarray(i, i + step));
  }
  return btoa(binary);
}

type AudioContextCtor = typeof AudioContext;

function contextCtor(): AudioContextCtor | null {
  if (typeof window === "undefined") return null;
  return (
    window.AudioContext ||
    (window as unknown as { webkitAudioContext?: AudioContextCtor }).webkitAudioContext ||
    null
  );
}

export function useVoiceAudio() {
  const contextRef = useRef<AudioContext | null>(null);
  const sourcesRef = useRef<Set<AudioBufferSourceNode>>(new Set());
  const nextTimeRef = useRef(0);
  const streamRef = useRef<MediaStream | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const workletRef = useRef<AudioWorkletNode | null>(null);
  const gainRef = useRef<GainNode | null>(null);
  const recordingRef = useRef(false);
  /** Invalidates an in-flight startMic when a newer start/stop/unmount wins. */
  const micGenerationRef = useRef(0);

  const ensureAudio = useCallback(async (): Promise<AudioContext> => {
    const Ctor = contextCtor();
    if (!Ctor) throw new Error("当前浏览器不支持音频采集");
    if (!contextRef.current) {
      const context = new Ctor();
      await context.audioWorklet.addModule("/static/worklets/mic.js");
      contextRef.current = context;
    }
    if (contextRef.current.state === "suspended") await contextRef.current.resume();
    return contextRef.current;
  }, []);

  const stopPlayback = useCallback(() => {
    for (const source of sourcesRef.current) {
      try {
        source.stop();
      } catch {
        // already stopped
      }
    }
    sourcesRef.current.clear();
    nextTimeRef.current = 0;
  }, []);

  const playPcm = useCallback((base64: string, sampleRate = 24_000) => {
    const context = contextRef.current;
    if (!context || !base64) return;
    if (context.state === "suspended") void context.resume().catch(() => undefined);
    const pcm = base64ToPcm(base64);
    if (!pcm.length) return;
    const buffer = context.createBuffer(1, pcm.length, sampleRate);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < pcm.length; i += 1) channel[i] = pcm[i] / 0x8000;
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(context.destination);
    const startAt = Math.max(context.currentTime + PLAYOUT_DELAY_S, nextTimeRef.current);
    source.start(startAt);
    nextTimeRef.current = startAt + buffer.duration;
    sourcesRef.current.add(source);
    source.onended = () => sourcesRef.current.delete(source);
  }, []);

  const stopMic = useCallback(() => {
    micGenerationRef.current += 1;
    recordingRef.current = false;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    sourceRef.current?.disconnect();
    sourceRef.current = null;
    workletRef.current?.disconnect();
    workletRef.current = null;
    gainRef.current?.disconnect();
    gainRef.current = null;
  }, []);

  const startMic = useCallback(
    async (onChunk: (base64: string) => void) => {
      if (recordingRef.current) return;
      const generation = micGenerationRef.current + 1;
      micGenerationRef.current = generation;
      const context = await ensureAudio();
      if (generation !== micGenerationRef.current) return;
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new Error("当前浏览器不支持麦克风");
      }
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      if (generation !== micGenerationRef.current) {
        // A newer start/stop (or unmount) happened while permission was
        // pending: release the stream we just acquired instead of leaking it.
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      const source = context.createMediaStreamSource(stream);
      const worklet = new AudioWorkletNode(context, "mic-processor");
      worklet.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
        if (generation !== micGenerationRef.current) return;
        const bytes = new Uint8Array(event.data);
        if (bytes.length) onChunk(pcmToBase64(bytes));
      };
      const gain = context.createGain();
      gain.gain.value = 0;
      source.connect(worklet);
      worklet.connect(gain);
      gain.connect(context.destination);
      streamRef.current = stream;
      sourceRef.current = source;
      workletRef.current = worklet;
      gainRef.current = gain;
      recordingRef.current = true;
    },
    [ensureAudio],
  );

  const isPlaying = useCallback(() => {
    const context = contextRef.current;
    if (!context) return false;
    return sourcesRef.current.size > 0 || nextTimeRef.current > context.currentTime + 0.02;
  }, []);

  useEffect(
    () => () => {
      // Invalidate a startMic that is still waiting on permission/audio setup
      // so it cannot assign a stream this cleanup will never see.
      micGenerationRef.current += 1;
      stopMic();
      stopPlayback();
      void contextRef.current?.close().catch(() => undefined);
      contextRef.current = null;
    },
    [stopMic, stopPlayback],
  );

  return useMemo(
    () => ({
      ensureAudio,
      startMic,
      stopMic,
      playPcm,
      stopPlayback,
      isPlaying,
      isRecording: () => recordingRef.current,
    }),
    [ensureAudio, startMic, stopMic, playPcm, stopPlayback, isPlaying],
  );
}

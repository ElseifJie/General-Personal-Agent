import type { ConversationSummary } from "../gpaApi";
import { formatTime } from "../gpaApi";
import type { VoiceMetrics, VoiceStatus } from "./useVoiceSession";

const STATUS_LABELS: Record<string, string> = {
  idle: "空闲",
  connecting: "连接中",
  listening: "聆听中",
  thinking: "思考中",
  working: "检索中",
  speaking: "播放中",
  offline: "已断开",
  error: "异常",
};

export function VoiceInspector({
  conversation,
  status,
  connected,
  metrics,
  error,
}: {
  conversation: ConversationSummary | null;
  status: VoiceStatus;
  connected: boolean;
  metrics: VoiceMetrics;
  error: string | null;
}) {
  return (
    <>
      <dl className="gpa-inspector">
        <div>
          <dt>状态</dt>
          <dd>{STATUS_LABELS[status] ?? status}</dd>
        </div>
        <div>
          <dt>连接</dt>
          <dd>
            <span
              className={`gpa-inspector__dot${connected ? " is-live" : ""}`}
              aria-hidden="true"
            />
            {connected ? "WebSocket 已连接" : "未连接"}
          </dd>
        </div>
        <div>
          <dt>模态</dt>
          <dd>{conversation ? conversation.channels.join(" / ") || "voice" : "voice"}</dd>
        </div>
        <div>
          <dt>ASR</dt>
          <dd>{metrics.asrMs ? `${metrics.asrMs}ms` : "—"}</dd>
        </div>
        <div>
          <dt>首字</dt>
          <dd>{metrics.ttftMs ? `${metrics.ttftMs}ms` : "—"}</dd>
        </div>
        <div>
          <dt>首响</dt>
          <dd>{metrics.firstAudioMs ? `${metrics.firstAudioMs}ms` : "—"}</dd>
        </div>
        <div>
          <dt>总时延</dt>
          <dd>{metrics.totalMs ? `${metrics.totalMs}ms` : "—"}</dd>
        </div>
        <div>
          <dt>更新</dt>
          <dd>{conversation ? formatTime(conversation.updatedAt) : "—"}</dd>
        </div>
      </dl>
      {error ? <p className="gpa-inspector__error">{error}</p> : null}
    </>
  );
}

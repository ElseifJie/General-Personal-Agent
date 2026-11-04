import { AudioLines, LayoutList } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { NewMessages } from "../components/NewMessages";
import {
  emitConversationsChanged,
  formatTime,
  isAuthError,
} from "../gpaApi";
import { Alert, Button, Spinner } from "../ui/index";
import { VoiceComposer } from "../voice/VoiceComposer";
import { VoiceInspector } from "../voice/VoiceInspector";
import { VoiceTranscript } from "../voice/VoiceTranscript";
import { useVoiceSession } from "../voice/useVoiceSession";
import { ConversationActions } from "./ConversationActions";
import { ConversationRail } from "./ConversationRail";
import { deriveTasksAndArtifacts } from "./derive";
import { InteractionHeader } from "./InteractionHeader";
import { rememberActiveConversation } from "./lastActive";
import { useConversation } from "./useConversation";
import { resolveVoiceEntry } from "./voiceEntry";

export function VoiceInteractionPage({
  onAuthRequired,
}: {
  onAuthRequired: () => void;
}) {
  const { conversationId } = useParams();
  if (conversationId) {
    return (
      // Remount per conversation so the rail state and the selected artifact
      // never carry over to another voice session.
      <VoiceConversation
        key={conversationId}
        conversationId={conversationId}
        onAuthRequired={onAuthRequired}
      />
    );
  }
  return <VoiceEntryPage onAuthRequired={onAuthRequired} />;
}

/**
 * Voice entry: resolve first, then navigate. There is deliberately
 * no "start" button: the user should not have to choose new-vs-continue
 * before speaking. `?fresh=1` is the explicit "new session" escape hatch.
 */
function VoiceEntryPage({ onAuthRequired }: { onAuthRequired: () => void }) {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const fresh = searchParams.get("fresh") === "1";
  const currentId = searchParams.get("current")?.trim() || null;
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let active = true;
    setError(null);
    resolveVoiceEntry({ fresh, currentId })
      .then((target) => {
        if (!active) return;
        const suffix =
          target.kind === "resume" ? `?resumed=${target.reason}` : "";
        navigate(`/interact/voice/${target.conversationId}${suffix}`, {
          replace: true,
        });
      })
      .catch((caught: unknown) => {
        if (!active) return;
        if (isAuthError(caught)) {
          onAuthRequired();
          return;
        }
        setError(
          caught instanceof Error ? caught.message : "无法开始语音会话",
        );
      });
    return () => {
      active = false;
    };
  }, [attempt, currentId, fresh, navigate, onAuthRequired]);

  return (
    <div className="gpa-new-page gpa-page--voice">
      <InteractionHeader mode="voice" title="语音交互" />
      <div className="gpa-new-page__body">
        <div className="gpa-new-page__hero">
          <h2>{error ? "无法开始语音会话" : "正在准备语音会话"}</h2>
          <p>
            {error
              ? "请重试，或先切换到文本交互。"
              : fresh
                ? "正在新建语音会话…"
                : "正在恢复最近的会话或创建新会话…"}
          </p>
        </div>
        {error ? (
          <>
            <Alert tone="danger">{error}</Alert>
            <div className="gpa-voice-start">
              <Button onClick={() => setAttempt((value) => value + 1)}>
                重试
              </Button>
              <Button
                variant="secondary"
                onClick={() => navigate("/interact/text")}
              >
                改用文本
              </Button>
            </div>
          </>
        ) : (
          <div className="gpa-loading">
            <Spinner label="准备语音会话" />
            正在准备语音会话…
          </div>
        )}
      </div>
    </div>
  );
}

function VoiceConversation({
  conversationId,
  onAuthRequired,
}: {
  conversationId: string;
  onAuthRequired: () => void;
}) {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const resumedParam = searchParams.get("resumed");
  const resumed =
    resumedParam === "current" ||
    resumedParam === "task" ||
    resumedParam === "recent"
      ? resumedParam
      : null;
  const conversation = useConversation(conversationId, onAuthRequired, "voice");
  const voice = useVoiceSession(conversationId, {
    onAuthRequired,
    resumeCue: Boolean(resumed),
    onTurnAccepted: () => rememberActiveConversation(conversationId),
    onTurnSettled: () => {
      rememberActiveConversation(conversationId);
      emitConversationsChanged();
      void conversation.refresh();
    },
  });
  // Closed by default, exactly like the text page: the detail rail (voice
  // pipeline + artifacts) only comes up when there is something to show.
  const [railOpen, setRailOpen] = useState(false);
  const railTouched = useRef(false);
  const [selectedArtifactId, setSelectedArtifactId] = useState<string | null>(
    null,
  );
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionNotice, setActionNotice] = useState<string | null>(null);

  const derived = useMemo(
    () => deriveTasksAndArtifacts(conversation.items),
    [conversation.items],
  );

  const artifacts = useMemo(() => {
    const map = new Map(derived.artifacts.map((item) => [item.artifactId, item]));
    for (const item of voice.artifacts) {
      map.set(item.artifactId, item);
    }
    return [...map.values()];
  }, [derived.artifacts, voice.artifacts]);

  const hasArtifacts = artifacts.length > 0;
  const closeRail = () => {
    railTouched.current = true;
    setRailOpen(false);
  };
  useEffect(() => {
    // Auto-open once the conversation turns out to produce artifacts; a manual
    // dismissal wins, and narrow screens keep it as a drawer.
    if (railTouched.current || !hasArtifacts) return;
    if (typeof window === "undefined" || window.innerWidth < 1360) return;
    setRailOpen(true);
  }, [hasArtifacts]);

  const latestArtifact = artifacts[artifacts.length - 1] ?? null;
  const activeArtifact =
    artifacts.find((artifact) => artifact.artifactId === selectedArtifactId) ??
    latestArtifact;
  const title = conversation.conversation?.title || "语音交互";
  // A draft voice conversation only becomes visible once its first turn lands;
  // its empty state must not claim the turn joins an earlier conversation.
  const isNewConversation = Boolean(conversation.conversation?.isDraft);
  const updatedLabel = conversation.conversation
    ? formatTime(conversation.conversation.updatedAt)
    : "";
  const resumeText =
    resumed === "current"
      ? `正在继续当前会话「${title}」`
      : resumed === "task"
        ? `「${title}」有任务进行中，已接着这个会话`
        : updatedLabel
          ? `正在继续 ${updatedLabel} 的「${title}」`
          : `正在继续「${title}」`;
  const voiceActive =
    voice.status === "listening" ||
    voice.status === "thinking" ||
    voice.status === "speaking";
  const running = conversation.runningTurnId !== null || voiceActive;
  const revision = `${conversation.items.length}:${Object.keys(conversation.streaming).length}:${voice.partialText.length}:${voice.assistantText.length}`;

  return (
    <div className="session-workspace gpa-workbench gpa-page--voice">
      <div className="session-page gpa-chat gpa-voice-page">
        <InteractionHeader
          mode="voice"
          conversationId={conversationId}
          title={title}
          modeSwitchFirst
          actions={
            <ConversationActions
              conversationId={conversationId}
              title={title}
              turns={conversation.turns}
              rail={{
                open: railOpen,
                onToggle: () => setRailOpen((open) => !open),
                label: "详情",
                icon: <LayoutList size={14} aria-hidden="true" />,
                count: artifacts.length,
                id: "gpa-detail-rail",
              }}
              newHref="/interact/voice?fresh=1"
              refreshLabel="重连语音"
              onRefresh={() => {
                voice.reconnect();
                void conversation.refresh();
              }}
              onRenamed={() => {
                emitConversationsChanged();
                return conversation.refresh();
              }}
              onArchived={() => {
                emitConversationsChanged();
                navigate("/history");
              }}
              onDeleted={() => {
                emitConversationsChanged();
                navigate("/history");
              }}
              onNotice={(message) => {
                setActionError(null);
                setActionNotice(message);
              }}
              onError={(message) => {
                setActionNotice(null);
                setActionError(message);
              }}
              onAuthRequired={onAuthRequired}
              disabled={conversation.loading || running}
            />
          }
        />

        <div className="session-body gpa-chat__body">
          {resumed ? (
            <div className="gpa-resume-bar" role="status" aria-live="polite">
              <AudioLines size={15} aria-hidden="true" />
              <span className="gpa-resume-bar__text">{resumeText}</span>
              <Button
                variant="text"
                size="compact"
                onClick={() =>
                  navigate("/interact/voice?fresh=1", { replace: true })
                }
              >
                新会话
              </Button>
            </div>
          ) : null}
          {voice.error ? (
            <Alert tone="danger">
              {voice.error}{" "}
              <Link className="gpa-link" to={`/interact/text/${conversationId}`}>
                切换到文本交互
              </Link>
            </Alert>
          ) : null}
          {conversation.error ? <Alert tone="danger">{conversation.error}</Alert> : null}
          {actionError ? <Alert tone="danger">{actionError}</Alert> : null}
          {actionNotice ? <Alert tone="success">{actionNotice}</Alert> : null}
          <NewMessages revision={revision}>
            <div className="gpa-thread">
              <div className="gpa-thread__inner">
                {conversation.loading ? (
                  <div className="gpa-loading">
                    <Spinner label="加载会话" />
                    正在加载会话…
                  </div>
                ) : (
                  <VoiceTranscript
                    items={conversation.items}
                    streaming={conversation.streaming}
                    runningTurnId={conversation.runningTurnId}
                    partialText={voice.partialText}
                    liveUserText={voice.userText}
                    liveAssistantText={voice.assistantText || voice.fillerText}
                    status={voice.status}
                    emptyDescription={
                      isNewConversation
                        ? "按住下方按钮说话，第一句话会开启一段新的 conversation。"
                        : undefined
                    }
                    onArtifactSelect={(artifactId) => {
                      railTouched.current = true;
                      setSelectedArtifactId(artifactId);
                      setRailOpen(true);
                    }}
                  />
                )}
              </div>
            </div>
          </NewMessages>
        </div>

        <div className="gpa-voice-dock">
          {!voice.connected && !voice.error ? (
            <p className="gpa-stream-status" role="status">
              语音连接已断开，正在重连…
            </p>
          ) : null}
          <VoiceComposer
            status={voice.status}
            connected={voice.connected}
            onStart={() => void voice.startTalking()}
            onStop={voice.stopTalking}
            onInterrupt={voice.interrupt}
            isRecording={voice.status === "listening"}
          />
        </div>
      </div>

      {railOpen ? (
        <ConversationRail
          artifacts={artifacts}
          selectedArtifactId={activeArtifact?.artifactId ?? null}
          onSelectArtifact={setSelectedArtifactId}
          leading={
            <section className="gpa-rail-section" aria-labelledby="gpa-rail-voice">
              <h3 className="gpa-rail-section__title" id="gpa-rail-voice">
                <AudioLines size={14} aria-hidden="true" />
                语音链路
              </h3>
              <VoiceInspector
                conversation={conversation.conversation}
                status={voice.status}
                connected={voice.connected}
                metrics={voice.metrics}
                error={voice.error}
              />
            </section>
          }
          onClose={closeRail}
        />
      ) : null}
      {railOpen ? (
        <button
          type="button"
          className="gpa-rail-backdrop"
          aria-label="关闭详情侧栏"
          onClick={closeRail}
        />
      ) : null}
    </div>
  );
}

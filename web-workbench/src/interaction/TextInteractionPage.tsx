import { Package } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";

import { NewMessages } from "../components/NewMessages";
import { emitConversationsChanged, gpaApi, isAuthError } from "../gpaApi";
import { Alert, Spinner } from "../ui/index";
import { ArtifactRail } from "./ArtifactRail";
import { ConversationActions } from "./ConversationActions";
import { ConversationComposer } from "./ConversationComposer";
import { ConversationTimeline } from "./ConversationTimeline";
import { deriveTasksAndArtifacts } from "./derive";
import { InteractionHeader } from "./InteractionHeader";
import { useConversation } from "./useConversation";

export function TextInteractionPage({
  onAuthRequired,
}: {
  onAuthRequired: () => void;
}) {
  const { conversationId } = useParams();
  if (conversationId) {
    return (
      // Remount per conversation so the rail, the artifact selection and the
      // "user already chose" flags never carry over to another transcript.
      <ConversationPage
        key={conversationId}
        conversationId={conversationId}
        onAuthRequired={onAuthRequired}
      />
    );
  }
  return <NewTextPage onAuthRequired={onAuthRequired} />;
}

function NewTextPage({ onAuthRequired }: { onAuthRequired: () => void }) {
  const navigate = useNavigate();
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function uploadFiles(files: File[]): Promise<string[]> {
    const uploaded: string[] = [];
    for (const file of files) {
      const result = await gpaApi.uploadFile(file, "conversation");
      uploaded.push(result.fileId);
    }
    return uploaded;
  }

  async function start(
    content: string,
    attachmentIds: string[] = [],
  ): Promise<boolean> {
    setSending(true);
    setError(null);
    let created: Awaited<ReturnType<typeof gpaApi.createConversation>> | null = null;
    try {
      created = await gpaApi.createConversation({
        title: content.split(/\r?\n/)[0].slice(0, 28) || "新交互",
        channel: "text",
        draft: true,
      });
      let conversationFileIds: string[] = [];
      if (attachmentIds.length > 0) {
        const bound = await gpaApi.bindConversationFiles(created.conversationId, attachmentIds);
        conversationFileIds = bound.items.map((item) => item.conversationFileId);
      }
      await gpaApi.submitMessage(
        created.conversationId,
        content,
        `${created.conversationId}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        "text",
        conversationFileIds,
      );
      emitConversationsChanged();
      navigate(`/interact/text/${created.conversationId}`);
      return true;
    } catch (caught) {
      if (isAuthError(caught)) {
        onAuthRequired();
        return false;
      }
      if (created) {
        emitConversationsChanged();
        navigate(`/interact/text/${created.conversationId}`);
        return false;
      }
      setError(caught instanceof Error ? caught.message : "创建交互失败");
      return false;
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="gpa-new-page">
      <InteractionHeader mode="text" title="文本交互" />
      <div className="gpa-new-page__body">
        <div className="gpa-new-page__hero">
          <h2 id="gpa-new-title">今天想一起完成什么？</h2>
        </div>
        {error ? <Alert tone="danger">{error}</Alert> : null}
        <ConversationComposer
          conversationId={null}
          running={false}
          sending={sending}
          onSend={start}
          onUploadFiles={uploadFiles}
          onStop={async () => undefined}
          fileTools
          autoFocus
          variant="hero"
          placeholder="输入第一句话…"
        />
      </div>
    </div>
  );
}

function ConversationPage({
  conversationId,
  onAuthRequired,
}: {
  conversationId: string;
  onAuthRequired: () => void;
}) {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const linkedArtifactId = searchParams.get("artifactId");
  const conversation = useConversation(conversationId, onAuthRequired, "text");
  async function uploadConversationFiles(files: File[]): Promise<string[]> {
    const ids: string[] = [];
    for (const file of files) {
      const result = await gpaApi.uploadFile(file, "conversation", { conversationId });
      if (result.conversationFileId) ids.push(result.conversationFileId);
    }
    return ids;
  }
  const { artifacts } = useMemo(
    () => deriveTasksAndArtifacts(conversation.items),
    [conversation.items],
  );
  // Closed by default: the empty artifacts panel used to shove the transcript
  // around on every wide screen. A linked artifact still opens it directly.
  const [railOpen, setRailOpen] = useState(() => linkedArtifactId !== null);
  const railTouched = useRef(false);
  const [selectedArtifactId, setSelectedArtifactId] = useState<string | null>(
    linkedArtifactId,
  );
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionNotice, setActionNotice] = useState<string | null>(null);

  useEffect(() => {
    if (!linkedArtifactId) return;
    setSelectedArtifactId(linkedArtifactId);
    setRailOpen(true);
  }, [linkedArtifactId]);

  const title = conversation.conversation?.title || "文本交互";
  const artifactCount = Math.max(
    conversation.conversation?.artifactCount ?? 0,
    artifacts.length,
  );
  const closeRail = () => {
    railTouched.current = true;
    setRailOpen(false);
  };
  useEffect(() => {
    // Once the conversation is known to have artifacts, show the panel the
    // way the old always-open default did — unless the reader already
    // dismissed it or the viewport is too narrow for a docked panel.
    if (railTouched.current || artifactCount === 0) return;
    if (typeof window === "undefined" || window.innerWidth < 1360) return;
    setRailOpen(true);
  }, [artifactCount]);
  const latestArtifact = artifacts[artifacts.length - 1] ?? null;
  const activeArtifact =
    artifacts.find((artifact) => artifact.artifactId === selectedArtifactId) ??
    latestArtifact;
  const running = conversation.runningTurnId !== null;
  const revision = `${conversation.items.length}:${
    Object.keys(conversation.streaming).length
  }`;

  return (
    <>
      <div className="session-workspace gpa-workbench">
        <div className="session-page gpa-chat">
          <InteractionHeader
            mode="text"
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
                  onToggle: () => {
                    railTouched.current = true;
                    setRailOpen((open) => !open);
                  },
                  label: "产物",
                  icon: <Package size={14} aria-hidden="true" />,
                  count: artifactCount,
                  id: "gpa-artifact-rail",
                }}
                newHref="/interact/text"
                onRefresh={() => conversation.refresh()}
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
            {conversation.error ? (
              <Alert tone="danger">{conversation.error}</Alert>
            ) : null}
            {actionError ? (
              <Alert tone="danger" className="gpa-chat-alert">
                {actionError}
              </Alert>
            ) : null}
            {actionNotice ? (
              <Alert tone="success" className="gpa-chat-alert">
                {actionNotice}
              </Alert>
            ) : null}
            <NewMessages revision={revision}>
              <div className="gpa-thread">
                <div className="gpa-thread__inner">
                  {conversation.loading ? (
                    <div className="gpa-loading">
                      <Spinner label="加载会话" />
                      正在加载会话…
                    </div>
                  ) : (
                    <ConversationTimeline
                      items={conversation.items}
                      streaming={conversation.streaming}
                      runningTurnId={conversation.runningTurnId}
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

          {!conversation.connected ? (
            <p className="gpa-stream-status" role="status">
              与服务器的连接已断开，正在自动重连…
            </p>
          ) : null}

          <ConversationComposer
            conversationId={conversationId}
            running={running}
            sending={conversation.sending}
            onSend={async (text, attachmentIds) =>
              (await conversation.send(text, attachmentIds)) !== null
            }
            onUploadFiles={uploadConversationFiles}
            fileTools
            onStop={conversation.stop}
            placeholder="继续补充任务要求…"
          />
        </div>

        {railOpen ? (
          <ArtifactRail
            artifacts={artifacts}
            selectedArtifactId={activeArtifact?.artifactId ?? null}
            onSelectArtifact={setSelectedArtifactId}
            onClose={closeRail}
          />
        ) : null}
        {railOpen ? (
          <button
            type="button"
            className="gpa-rail-backdrop"
            aria-label="关闭产物侧栏"
            onClick={closeRail}
          />
        ) : null}
      </div>

    </>
  );
}

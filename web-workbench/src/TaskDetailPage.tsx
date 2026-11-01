import { ArrowLeft, Download, RefreshCw, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import {
  formatTime,
  gpaApi,
  isAuthError,
  taskStatusLabel,
  type TaskView,
} from "./gpaApi";
import { Alert, Badge, Button, EmptyState, PageHeader, Spinner } from "./ui/index";

const GOAL_TITLE_MAX = 36;

/** Long task goals are paragraphs, not headings: keep the h1 to one phrase. */
function goalTitle(goal: string): string {
  const text = (goal || "").trim();
  if (!text) return "未命名任务";
  const head = text.split(/\r?\n/)[0].trim();
  if (head.length <= GOAL_TITLE_MAX) return head;
  const window = head.slice(0, GOAL_TITLE_MAX + 1);
  const cut = Math.max(
    window.lastIndexOf("，"),
    window.lastIndexOf("、"),
    window.lastIndexOf("；"),
    window.lastIndexOf("："),
    window.lastIndexOf(" "),
    window.lastIndexOf("。"),
  );
  const body = cut >= 12 ? window.slice(0, cut) : window.slice(0, GOAL_TITLE_MAX);
  return `${body.trim()}…`;
}

function tone(status: TaskView["status"]) {
  if (status === "completed") return "success" as const;
  if (status === "failed") return "danger" as const;
  if (status === "cancelled") return "warning" as const;
  return "info" as const;
}

export function TaskDetailPage({ onAuthRequired }: { onAuthRequired: () => void }) {
  const { taskId } = useParams();
  const navigate = useNavigate();
  const [task, setTask] = useState<TaskView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [cancelling, setCancelling] = useState(false);

  useEffect(() => {
    if (!taskId) return;
    let active = true;
    setLoading(true);
    setError(null);
    void gpaApi
      .getTask(taskId)
      .then((value) => {
        if (active) setTask(value);
      })
      .catch((caught: unknown) => {
        if (!active) return;
        if (isAuthError(caught)) {
          onAuthRequired();
          return;
        }
        setError(caught instanceof Error ? caught.message : "加载任务失败");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [taskId, onAuthRequired, reloadKey]);

  if (loading) {
    return (
      <div className="page">
        <div className="gpa-loading">
          <Spinner label="加载任务" />
          正在加载任务…
        </div>
      </div>
    );
  }

  if (error || !task) {
    return (
      <div className="page">
        <Alert tone="danger">{error || "任务不存在"}</Alert>
        <Button variant="secondary" onClick={() => navigate(-1)}>
          <ArrowLeft size={14} aria-hidden="true" />
          返回
        </Button>
      </div>
    );
  }

  const cancellable = task.status === "queued" || task.status === "running";

  async function cancelTask() {
    if (!cancellable || !taskId) return;
    if (!window.confirm("确定取消这个任务？已产生的副作用不会回滚。")) return;
    setCancelling(true);
    setError(null);
    try {
      const updated = await gpaApi.cancelTask(taskId, "用户在任务详情页取消");
      setTask(updated);
    } catch (caught) {
      if (isAuthError(caught)) {
        onAuthRequired();
        return;
      }
      setError(caught instanceof Error ? caught.message : "取消任务失败");
    } finally {
      setCancelling(false);
    }
  }

  return (
    <div className="page gpa-task-page">
      <PageHeader
        eyebrow="任务详情"
        title={goalTitle(task.goal)}
        description={`${task.taskType || "任务"} · ${task.executor} · 更新于 ${formatTime(
          task.updatedAt,
        )}`}
        actions={
          <>
            <Button
              variant="secondary"
              className="gpa-task-action"
              onClick={() => setReloadKey((key) => key + 1)}
            >
              <RefreshCw size={14} aria-hidden="true" />
              刷新
            </Button>
            {cancellable ? (
              <Button
                variant="danger"
                className="gpa-task-action"
                onClick={() => void cancelTask()}
                loading={cancelling}
              >
                <X size={14} aria-hidden="true" />
                取消任务
              </Button>
            ) : null}
            <Link
              className="ui-button ui-button--primary ui-button--default gpa-task-action"
              to={`/interact/text/${task.conversationId}`}
            >
              <ArrowLeft size={14} aria-hidden="true" />
              返回会话
            </Link>
          </>
        }
      />
      <section className="gpa-task-card">
        <div className="gpa-task-card__row gpa-task-card__row--stack">
          <span className="gpa-task-card__label">目标</span>
          <p className="gpa-task-goal">{task.goal}</p>
        </div>
        <div className="gpa-task-card__row">
          <span className="gpa-task-card__label">状态</span>
          <Badge tone={tone(task.status)}>{taskStatusLabel(task.status)}</Badge>
        </div>
        <div className="gpa-task-card__row">
          <span className="gpa-task-card__label">进度</span>
          <div className="gpa-progress gpa-progress--wide" aria-label={`进度 ${Math.round(task.progress * 100)}%`}>
            <span style={{ width: `${Math.max(0, Math.min(100, task.progress * 100))}%` }} />
          </div>
          <span>{Math.round(task.progress * 100)}%</span>
        </div>
        <div className="gpa-task-card__row">
          <span className="gpa-task-card__label">任务 ID</span>
          <code>{task.taskId}</code>
        </div>
        <div className="gpa-task-card__row">
          <span className="gpa-task-card__label">会话</span>
          <Link className="gpa-link" to={`/interact/text/${task.conversationId}`}>
            {task.conversationId}
          </Link>
        </div>
        {task.summary ? (
          <div className="gpa-task-card__row gpa-task-card__row--stack">
            <span className="gpa-task-card__label">摘要</span>
            <p>{task.summary}</p>
          </div>
        ) : null}
        {task.error && task.status === "failed" ? (
          <Alert tone="danger">
            {String(task.error.message ?? task.error.code ?? "任务失败")}
          </Alert>
        ) : null}
      </section>
      <section className="gpa-task-artifacts">
        <h2>产物</h2>
        {task.artifacts.length === 0 ? (
          <EmptyState title="暂无产物" description="任务完成后，产物会出现在这里。" />
        ) : (
          <ul className="gpa-artifact-list">
            {task.artifacts.map((artifact) => (
              <li key={artifact.artifactId}>
                <div>
                  <strong>{artifact.name}</strong>
                  <span>
                    {artifact.mime || "未知类型"}
                    {artifact.bytes ? ` · ${Math.max(1, Math.round(artifact.bytes / 1024))} KB` : ""}
                  </span>
                </div>
                {artifact.downloadUrl ? (
                  <a className="gpa-link" href={artifact.downloadUrl}>
                    <Download size={14} aria-hidden="true" />
                    下载
                  </a>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

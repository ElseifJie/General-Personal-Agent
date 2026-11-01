import type { TimelineItem } from "../gpaApi";

export interface DerivedTask {
  taskId: string;
  title: string;
  status: string;
  progress: number;
  summary: string;
}

export interface DerivedArtifact {
  artifactId: string;
  taskId: string;
  name: string;
  downloadUrl: string | null;
  createdAt?: string;
  mime?: string | null;
  bytes?: number | null;
}

export function deriveTasksAndArtifacts(items: TimelineItem[]): {
  tasks: DerivedTask[];
  artifacts: DerivedArtifact[];
} {
  const tasks = new Map<string, DerivedTask>();
  const artifacts = new Map<string, DerivedArtifact>();
  for (const item of items) {
    if (item.kind !== "event") continue;
    const payload = item.payload ?? {};
    if (item.type === "task.created" || item.type === "task.status") {
      const taskId = item.taskId || String(payload.taskId ?? "");
      if (!taskId) continue;
      tasks.set(taskId, {
        taskId,
        title: String(payload.goal ?? payload.taskType ?? "任务") || "任务",
        status: String(payload.status ?? "queued"),
        progress: Number(payload.progress ?? 0),
        summary: String(payload.summary ?? ""),
      });
    }
    if (item.type === "artifact") {
      const artifactId = String(payload.artifactId ?? payload.artifact_id ?? "");
      if (!artifactId) continue;
      const taskId = item.taskId || String(payload.taskId ?? payload.task_id ?? "");
      const bytes = Number(payload.bytes ?? payload.size);
      artifacts.set(artifactId, {
        artifactId,
        taskId,
        name: String(payload.name ?? artifactId),
        downloadUrl: taskId
          ? `/api/tasks/${encodeURIComponent(taskId)}/artifacts/${encodeURIComponent(
              artifactId,
            )}/download`
          : null,
        createdAt: String(payload.createdAt ?? item.occurredAt),
        mime: String(payload.mime ?? "") || null,
        bytes: Number.isFinite(bytes) && bytes > 0 ? bytes : null,
      });
    }
  }
  return {
    tasks: [...tasks.values()],
    artifacts: [...artifacts.values()].sort((a, b) =>
      (a.createdAt ?? "").localeCompare(b.createdAt ?? ""),
    ),
  };
}

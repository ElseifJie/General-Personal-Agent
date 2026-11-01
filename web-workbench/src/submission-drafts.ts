const prefix = "vwork:submission-draft:";
export interface AttemptedTask {
  agentId: string;
  uploadIds: string[];
  title: string;
  content: string;
  submissionId: string;
  temporary: boolean;
}
export function readDraft(key: string): {
  content: string;
  submissionId: string;
  temporary?: boolean;
  attempt?: AttemptedTask;
} {
  try {
    const value = JSON.parse(
      sessionStorage.getItem(prefix + key) ?? "null",
    ) as {
      content?: unknown;
      submissionId?: unknown;
      temporary?: unknown;
      attempt?: AttemptedTask;
    } | null;
    if (
      typeof value?.content === "string" &&
      typeof value.submissionId === "string"
    )
      return {
        content: value.content,
        submissionId: value.submissionId,
        ...(typeof value.temporary === "boolean"
          ? { temporary: value.temporary }
          : {}),
        ...(value.attempt &&
        typeof value.attempt.agentId === "string" &&
        Array.isArray(value.attempt.uploadIds) &&
        value.attempt.uploadIds.every((id) => typeof id === "string")
          ? { attempt: value.attempt }
          : {}),
      };
  } catch {
    /* Storage is optional for unaccepted drafts. */
  }
  return { content: "", submissionId: crypto.randomUUID() };
}
export function writeDraft(
  key: string,
  content: string,
  submissionId: string,
  temporary?: boolean,
  attempt?: AttemptedTask,
): void {
  try {
    sessionStorage.setItem(
      prefix + key,
      JSON.stringify({
        content,
        submissionId,
        ...(temporary === undefined ? {} : { temporary }),
        ...(attempt ? { attempt } : {}),
      }),
    );
  } catch {
    /* Draft can remain in memory. */
  }
}
export function clearDraft(key: string): void {
  try {
    sessionStorage.removeItem(prefix + key);
  } catch {
    /* Optional storage. */
  }
}
export function clearSubmissionDrafts(): void {
  try {
    for (let index = sessionStorage.length - 1; index >= 0; index--) {
      const key = sessionStorage.key(index);
      if (key?.startsWith(prefix)) sessionStorage.removeItem(key);
    }
  } catch {
    /* Optional storage. */
  }
}

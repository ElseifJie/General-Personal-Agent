const prefix = "vwork:submission-draft:";

/** Drop draft keys left behind by the pre-GPA workbench. */
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

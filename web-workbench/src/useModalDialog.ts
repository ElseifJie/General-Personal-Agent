import { type RefObject, useEffect, useRef } from "react";

const focusableSelector = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  'iframe:not([tabindex="-1"])',
  "summary",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

function focusableElements(container: HTMLElement): HTMLElement[] {
  return Array.from(
    container.querySelectorAll<HTMLElement>(focusableSelector),
  ).filter((element) => {
    if (
      (element.hasAttribute("tabindex") &&
        Number(element.getAttribute("tabindex")) < 0) ||
      element.closest('[hidden], [inert], [aria-hidden="true"]')
    )
      return false;
    if (element.tagName === "SUMMARY" && !element.hasAttribute("tabindex")) {
      const parent = element.parentElement;
      if (
        parent?.tagName !== "DETAILS" ||
        Array.from(parent.children).find(
          (child) => child.tagName === "SUMMARY",
        ) !== element
      )
        return false;
    }
    for (
      let parent = element.parentElement;
      parent && parent !== container;
      parent = parent.parentElement
    ) {
      if (
        parent.tagName === "DETAILS" &&
        !parent.hasAttribute("open") &&
        !Array.from(parent.children)
          .find((child) => child.tagName === "SUMMARY")
          ?.contains(element)
      )
        return false;
    }
    return true;
  });
}

export function useModalDialog({
  open,
  onClose,
  closeDisabled = false,
  initialFocusRef,
  returnFocusRef,
  fallbackFocusRef,
}: {
  open: boolean;
  onClose: () => void;
  closeDisabled?: boolean;
  initialFocusRef?: RefObject<HTMLElement | null>;
  returnFocusRef?: RefObject<HTMLElement | null>;
  fallbackFocusRef?: RefObject<HTMLElement | null>;
}) {
  const dialogRef = useRef<HTMLElement>(null);
  const onCloseRef = useRef(onClose);
  const closeDisabledRef = useRef(closeDisabled);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    closeDisabledRef.current = closeDisabled;
  }, [closeDisabled]);

  useEffect(() => {
    if (!open) return;
    const previousFocus =
      returnFocusRef?.current ??
      (document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null);
    const dialog = dialogRef.current;
    const initialFocus =
      initialFocusRef?.current ??
      (dialog ? (focusableElements(dialog)[0] ?? dialog) : null);
    initialFocus?.focus();

    const isActiveDialog = () => {
      if (!dialog?.isConnected) return false;
      const modals = document.querySelectorAll<HTMLElement>(
        '[role="dialog"][aria-modal="true"]',
      );
      const top = modals.item(modals.length - 1);
      return !top || top === dialog;
    };

    const focusBesideFrame = (frame: HTMLIFrameElement, backwards: boolean) => {
      if (!dialog) return;
      const controls = focusableElements(dialog);
      const index = controls.indexOf(frame);
      const next =
        controls[
          (index + (backwards ? -1 : 1) + controls.length) % controls.length
        ];
      (next ?? dialog).focus();
    };

    const handleKeyDown = (
      event: KeyboardEvent,
      embedded?: { frame: HTMLIFrameElement; document: Document },
    ) => {
      if (event.defaultPrevented || !isActiveDialog()) return;
      if (
        embedded &&
        (!embedded.frame.isConnected || !dialog?.contains(embedded.frame))
      )
        return;
      if (event.key === "Escape") {
        if (!closeDisabledRef.current) {
          event.preventDefault();
          onCloseRef.current();
        }
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current) return;

      if (embedded) {
        const body = embedded.document.body;
        const controls = body ? focusableElements(body) : [];
        const active = embedded.document.activeElement;
        const atStart =
          controls.length === 0 ||
          active === controls[0] ||
          active === body ||
          active === embedded.document.documentElement;
        const atEnd = controls.length === 0 || active === controls.at(-1);
        if ((event.shiftKey && atStart) || (!event.shiftKey && atEnd)) {
          event.preventDefault();
          focusBesideFrame(embedded.frame, event.shiftKey);
        }
        return;
      }

      const focusable = focusableElements(dialogRef.current);
      if (focusable.length === 0) {
        event.preventDefault();
        dialogRef.current.focus();
        return;
      }
      const first = focusable[0]!;
      const last = focusable[focusable.length - 1]!;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      } else if (!dialogRef.current.contains(document.activeElement)) {
        event.preventDefault();
        first.focus();
      }
    };

    // Keyboard events do not bubble between browsing contexts. Attach only
    // parent-owned listeners to readable documents; never insert frame scripts.
    const bindings = new Map<
      HTMLIFrameElement,
      {
        detachDocument: () => void;
        dispose: () => void;
      }
    >();
    const synchronizeFrames = () => {
      const frames = new Set(
        dialog?.querySelectorAll<HTMLIFrameElement>("iframe") ?? [],
      );
      for (const [frame, binding] of bindings) {
        if (!frames.has(frame)) {
          binding.dispose();
          bindings.delete(frame);
        }
      }
      for (const frame of frames) {
        if (bindings.has(frame)) continue;
        const binding = { detachDocument: () => {}, dispose: () => {} };
        const onLoad = () => {
          binding.detachDocument();
          binding.detachDocument = () => {};
          let frameDocument: Document | null;
          try {
            frameDocument = frame.contentDocument;
          } catch {
            return;
          } // Opaque/native PDF viewers keep host exit controls.
          if (!frameDocument) return;
          const listener = (event: KeyboardEvent) =>
            handleKeyDown(event, { frame, document: frameDocument });
          frameDocument.addEventListener("keydown", listener);
          binding.detachDocument = () =>
            frameDocument.removeEventListener("keydown", listener);
        };
        frame.addEventListener("load", onLoad);
        binding.dispose = () => {
          binding.detachDocument();
          frame.removeEventListener("load", onLoad);
        };
        bindings.set(frame, binding);
        onLoad();
      }
    };
    const observer = new MutationObserver((changes) => {
      for (const change of changes) {
        if (change.type === "attributes") {
          bindings.get(change.target as HTMLIFrameElement)?.detachDocument();
        }
      }
      synchronizeFrames();
    });
    if (dialog)
      observer.observe(dialog, {
        childList: true,
        subtree: true,
        attributes: true,
        attributeFilter: ["src"],
      });
    synchronizeFrames();
    const onDocumentKeyDown = (event: KeyboardEvent) => handleKeyDown(event);
    document.addEventListener("keydown", onDocumentKeyDown);
    return () => {
      observer.disconnect();
      for (const binding of bindings.values()) binding.dispose();
      bindings.clear();
      document.removeEventListener("keydown", onDocumentKeyDown);
      const returnFocus = previousFocus?.isConnected
        ? previousFocus
        : fallbackFocusRef?.current;
      if (returnFocus?.isConnected) returnFocus.focus();
    };
  }, [fallbackFocusRef, initialFocusRef, open, returnFocusRef]);

  return dialogRef;
}

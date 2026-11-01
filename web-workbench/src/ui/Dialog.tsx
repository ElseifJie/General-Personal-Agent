import { X } from "lucide-react";
import {
  useId,
  useLayoutEffect,
  useRef,
  type ReactNode,
  type RefObject,
} from "react";
import { useModalDialog } from "../useModalDialog";
import { IconButton } from "./Button";

export interface DialogProps {
  open: boolean;
  title: string;
  eyebrow?: string;
  className?: string;
  onClose: () => void;
  closeLabel?: string;
  closeDisabled?: boolean;
  hideCloseButton?: boolean;
  initialFocusRef?: RefObject<HTMLElement | null>;
  returnFocusRef?: RefObject<HTMLElement | null>;
  fallbackFocusRef?: RefObject<HTMLElement | null>;
  children: ReactNode;
  footer?: ReactNode;
}

export function Dialog({
  children,
  className,
  closeLabel = "Close dialog",
  closeDisabled = false,
  eyebrow,
  fallbackFocusRef,
  footer,
  hideCloseButton = false,
  initialFocusRef,
  onClose,
  open,
  returnFocusRef,
  title,
}: DialogProps) {
  const titleId = useId();
  const markedInitialFocusRef = useRef<HTMLElement | null>(null);
  const dialogRef = useModalDialog({
    open,
    onClose,
    closeDisabled,
    initialFocusRef: initialFocusRef ?? markedInitialFocusRef,
    ...(returnFocusRef ? { returnFocusRef } : {}),
    ...(fallbackFocusRef ? { fallbackFocusRef } : {}),
  });

  useLayoutEffect(() => {
    markedInitialFocusRef.current =
      dialogRef.current?.querySelector<HTMLElement>(
        "[data-dialog-initial-focus]",
      ) ?? null;
  }, [dialogRef, open]);

  if (!open) return null;

  return (
    <div className="ui-dialog-backdrop">
      <section
        ref={dialogRef}
        className={className ? `ui-dialog ${className}` : "ui-dialog"}
        role="dialog"
        aria-labelledby={titleId}
        aria-modal="true"
        tabIndex={-1}
      >
        <header className="ui-dialog__header">
          <div className="ui-dialog__heading">
            {eyebrow ? <p className="ui-dialog__eyebrow">{eyebrow}</p> : null}
            <h2 className="ui-dialog__title" id={titleId}>
              {title}
            </h2>
          </div>
          {hideCloseButton ? null : (
            <IconButton
              label={closeLabel}
              size="small"
              onClick={onClose}
              disabled={closeDisabled}
            >
              <X size={17} aria-hidden="true" />
            </IconButton>
          )}
        </header>
        <div className="ui-dialog__body">{children}</div>
        {footer ? (
          <footer className="ui-dialog__footer">{footer}</footer>
        ) : null}
      </section>
    </div>
  );
}

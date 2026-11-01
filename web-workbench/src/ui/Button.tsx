import type { ButtonHTMLAttributes, Ref } from "react";

export type ButtonVariant = "primary" | "secondary" | "text" | "danger";
export type ButtonSize = "default" | "compact";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
}

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  label: string;
  size?: "default" | "small";
  ref?: Ref<HTMLButtonElement>;
}

function classNames(...values: Array<string | undefined>) {
  return values.filter(Boolean).join(" ");
}

export function Button({
  "aria-busy": ariaBusy,
  children,
  className,
  disabled,
  loading = false,
  size = "default",
  type = "button",
  variant = "primary",
  ...props
}: ButtonProps) {
  return (
    <button
      {...props}
      aria-busy={loading ? true : ariaBusy}
      className={classNames(
        "ui-button",
        `ui-button--${variant}`,
        `ui-button--${size}`,
        className,
      )}
      disabled={disabled || loading}
      type={type}
    >
      {children}
    </button>
  );
}

export function IconButton({
  children,
  className,
  label,
  size = "default",
  type = "button",
  ...props
}: IconButtonProps) {
  return (
    <button
      {...props}
      aria-label={label}
      className={classNames(
        "ui-icon-button",
        `ui-icon-button--${size}`,
        className,
      )}
      title={label}
      type={type}
    >
      {children}
    </button>
  );
}

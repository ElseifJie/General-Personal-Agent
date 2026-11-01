import {
  Circle,
  CircleAlert,
  CircleCheck,
  Inbox,
  Info,
  LoaderCircle,
  TriangleAlert,
  type LucideIcon,
  type LucideProps,
} from "lucide-react";
import type { HTMLAttributes, ReactNode } from "react";

export type BadgeTone = "neutral" | "info" | "success" | "warning" | "danger";
export type AlertTone = "info" | "success" | "warning" | "danger";

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone;
}

export interface AlertProps extends HTMLAttributes<HTMLDivElement> {
  tone?: AlertTone;
}

export interface SpinnerProps extends Omit<LucideProps, "children"> {
  label?: string;
}

export interface EmptyStateProps {
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}

const badgeIcons: Record<BadgeTone, LucideIcon> = {
  neutral: Circle,
  info: Info,
  success: CircleCheck,
  warning: TriangleAlert,
  danger: CircleAlert,
};

const alertIcons: Record<AlertTone, LucideIcon> = {
  info: Info,
  success: CircleCheck,
  warning: TriangleAlert,
  danger: CircleAlert,
};

function classNames(...values: Array<string | undefined>) {
  return values.filter(Boolean).join(" ");
}

export function Badge({
  children,
  className,
  tone = "neutral",
  ...props
}: BadgeProps) {
  const Icon = badgeIcons[tone];

  return (
    <span
      {...props}
      className={classNames("ui-badge", `ui-badge--${tone}`, className)}
    >
      <Icon className="ui-badge__icon" size={12} aria-hidden="true" />
      <span>{children}</span>
    </span>
  );
}

export function Alert({
  children,
  className,
  role,
  tone = "info",
  ...props
}: AlertProps) {
  const Icon = alertIcons[tone];
  const defaultRole =
    tone === "warning" || tone === "danger" ? "alert" : "status";

  return (
    <div
      {...props}
      className={classNames("ui-alert", `ui-alert--${tone}`, className)}
      role={role ?? defaultRole}
    >
      <Icon className="ui-alert__icon" size={16} aria-hidden="true" />
      <div className="ui-alert__content">{children}</div>
    </div>
  );
}

export function Spinner({
  className,
  label = "加载中",
  size = 18,
  ...props
}: SpinnerProps) {
  return (
    <LoaderCircle
      {...props}
      className={classNames("ui-spinner", className)}
      size={size}
      role="status"
      aria-label={label}
    />
  );
}

export function EmptyState({
  action,
  className,
  description,
  title,
}: EmptyStateProps) {
  return (
    <div className={classNames("ui-empty-state", className)}>
      <Inbox className="ui-empty-state__icon" size={22} aria-hidden="true" />
      <div className="ui-empty-state__copy">
        <p className="ui-empty-state__title">{title}</p>
        {description ? (
          <p className="ui-empty-state__description">{description}</p>
        ) : null}
      </div>
      {action ? <div className="ui-empty-state__action">{action}</div> : null}
    </div>
  );
}

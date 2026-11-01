import { Eye, EyeOff } from "lucide-react";
import {
  cloneElement,
  forwardRef,
  useId,
  useState,
  type AriaAttributes,
  type InputHTMLAttributes,
  type ReactElement,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";

interface FieldControlProps {
  id?: string | undefined;
  "aria-describedby"?: string | undefined;
  "aria-invalid"?: AriaAttributes["aria-invalid"] | undefined;
}

export interface FieldProps {
  label: string;
  hint?: string;
  error?: string | undefined;
  children: ReactElement<FieldControlProps>;
}

export type InputProps = InputHTMLAttributes<HTMLInputElement>;

export type SelectProps = SelectHTMLAttributes<HTMLSelectElement>;

export type TextareaProps = TextareaHTMLAttributes<HTMLTextAreaElement>;

function classNames(...values: Array<string | undefined>) {
  return values.filter(Boolean).join(" ");
}

export function Field({ children, error, hint, label }: FieldProps) {
  const generatedId = useId();
  const controlId = children.props.id ?? `${generatedId}-control`;
  const hintId = hint ? `${generatedId}-hint` : undefined;
  const errorId = error ? `${generatedId}-error` : undefined;
  const describedBy = [children.props["aria-describedby"], hintId, errorId]
    .filter(Boolean)
    .join(" ");

  const control = cloneElement(children, {
    id: controlId,
    "aria-describedby": describedBy || undefined,
    "aria-invalid": error ? true : children.props["aria-invalid"],
  });

  return (
    <div className="ui-field">
      <label className="ui-field__label" htmlFor={controlId}>
        {label}
      </label>
      {control}
      {hint ? (
        <p className="ui-field__hint" id={hintId}>
          {hint}
        </p>
      ) : null}
      {error ? (
        <p className="ui-field__error" id={errorId} role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { className, ...props },
  ref,
) {
  return (
    <input {...props} ref={ref} className={classNames("ui-input", className)} />
  );
});

export type PasswordInputProps = Omit<
  InputHTMLAttributes<HTMLInputElement>,
  "type"
>;

export const PasswordInput = forwardRef<HTMLInputElement, PasswordInputProps>(
  function PasswordInput({ className, ...props }, ref) {
    const [visible, setVisible] = useState(false);
    return (
      <span className={classNames("ui-password", className)}>
        <input
          {...props}
          ref={ref}
          type={visible ? "text" : "password"}
          className="ui-input ui-password__input"
        />
        <button
          type="button"
          className="ui-password__toggle"
          aria-label={visible ? "隐藏密码" : "显示密码"}
          aria-pressed={visible}
          onClick={() => setVisible((current) => !current)}
        >
          {visible ? (
            <EyeOff size={16} aria-hidden="true" />
          ) : (
            <Eye size={16} aria-hidden="true" />
          )}
        </button>
      </span>
    );
  },
);

export const Select = forwardRef<HTMLSelectElement, SelectProps>(
  function Select({ className, ...props }, ref) {
    return (
      <select
        {...props}
        ref={ref}
        className={classNames("ui-select", className)}
      />
    );
  },
);

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(
  function Textarea({ className, ...props }, ref) {
    return (
      <textarea
        {...props}
        ref={ref}
        className={classNames("ui-textarea", className)}
      />
    );
  },
);

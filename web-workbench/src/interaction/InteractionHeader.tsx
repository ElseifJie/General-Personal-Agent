import { Mic, MessageCircle, Package } from "lucide-react";
import type { ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import type { Channel } from "../gpaApi";
import { Button } from "../ui/index";

export function InteractionModeSwitch({
  mode,
  conversationId,
  size = "default",
}: {
  mode: Channel;
  conversationId?: string | null;
  /** `compact` fits the conversation header; `default` is the new-chat size. */
  size?: "default" | "compact";
}) {
  const navigate = useNavigate();
  const location = useLocation();

  function go(next: Channel) {
    if (next === mode && location.pathname.startsWith(`/interact/${next}`)) return;
    if (next === "voice" && !conversationId) {
      // The new-conversation page's voice tab starts a fresh voice
      // conversation instead of resuming the current one.
      navigate("/interact/voice?fresh=1");
      return;
    }
    navigate(`/interact/${next}${conversationId ? `/${conversationId}` : ""}`);
  }

  return (
    <div
      className={`gpa-mode-switch${
        size === "compact" ? " gpa-mode-switch--compact" : ""
      }`}
      data-mode={mode}
      role="group"
      aria-label="交互模式"
    >
      <span className="gpa-mode-switch__thumb" aria-hidden="true" />
      <button
        type="button"
        className={`gpa-mode-switch__item${mode === "text" ? " is-active" : ""}`}
        aria-pressed={mode === "text"}
        aria-label="文本"
        onClick={() => go("text")}
      >
        <MessageCircle size={14} aria-hidden="true" />
        <span>文本</span>
      </button>
      <button
        type="button"
        className={`gpa-mode-switch__item${mode === "voice" ? " is-active" : ""}`}
        aria-pressed={mode === "voice"}
        aria-label="语音"
        onClick={() => go("voice")}
      >
        <Mic size={14} aria-hidden="true" />
        <span>语音</span>
      </button>
    </div>
  );
}

export function InteractionHeader({
  mode,
  conversationId,
  title,
  actions,
  rail,
  modeSwitchFirst = false,
}: {
  mode: Channel;
  conversationId?: string | null;
  title: string;
  actions?: ReactNode;
  rail?: { open: boolean; count: number; onToggle: () => void } | null;
  /** Put the mode switch before the page actions instead of after them. */
  modeSwitchFirst?: boolean;
}) {
  return (
    <header className="gpa-header session-header">
      <div className="gpa-header__copy session-heading">
        <h1 className="gpa-header__title" title={title}>
          {title}
        </h1>
      </div>
      <div className="gpa-header__actions session-header-actions">
        {modeSwitchFirst ? (
          <InteractionModeSwitch
            mode={mode}
            conversationId={conversationId}
            size="compact"
          />
        ) : null}
        {actions}
        {modeSwitchFirst ? null : (
          <InteractionModeSwitch
            mode={mode}
            conversationId={conversationId}
            size="compact"
          />
        )}
        {rail ? (
          <Button
            variant="secondary"
            size="compact"
            className="gpa-rail-toggle"
            aria-label={rail.open ? "收起侧栏" : "打开详情侧栏"}
            aria-expanded={rail.open}
            onClick={rail.onToggle}
          >
            <Package size={14} aria-hidden="true" />
            <span>详情</span>
            {rail.count > 0 ? (
              <span className="gpa-rail-toggle__count">{rail.count}</span>
            ) : null}
          </Button>
        ) : null}
      </div>
    </header>
  );
}

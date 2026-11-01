import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { Button } from "../ui/index";

export function NewMessages({
  revision,
  children,
}: {
  revision: string;
  children: ReactNode;
}) {
  const viewport = useRef<HTMLElement>(null);
  const following = useRef(true);
  const previous = useRef<string | undefined>(undefined);
  const [unread, setUnread] = useState(false);
  function latest() {
    if (viewport.current)
      viewport.current.scrollTop = viewport.current.scrollHeight;
    following.current = true;
    setUnread(false);
  }
  useLayoutEffect(() => {
    if (previous.current === revision) return;
    const initial = previous.current === undefined;
    previous.current = revision;
    if (following.current) {
      if (viewport.current)
        viewport.current.scrollTop = viewport.current.scrollHeight;
    } else if (!initial) {
      setUnread(true);
    }
  }, [revision]);
  return (
    <>
      <section
        ref={viewport}
        className="timeline-shell"
        aria-label="任务消息"
        onScroll={() => {
          const element = viewport.current;
          if (!element) return;
          following.current =
            element.scrollHeight - element.clientHeight - element.scrollTop <=
            48;
          if (following.current) setUnread(false);
        }}
      >
        {children}
      </section>
      {unread ? (
        <div className="session-header-actions">
          <span role="status">有新消息</span>
          <Button variant="secondary" onClick={latest}>
            查看最新消息
          </Button>
        </div>
      ) : null}
    </>
  );
}

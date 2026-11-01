import { Bot, ShieldCheck } from "lucide-react";
import { useEffect, useState, type SyntheticEvent } from "react";
import { ApiClientError, apiClient } from "./api";
import {
  Alert,
  Button,
  Field,
  PasswordInput,
  Select,
  Spinner,
} from "./ui/index";

interface DemoUser {
  app_user_id: string;
  display_name: string;
  role: string;
}

export function LoginPage({ onAuthenticated }: { onAuthenticated: () => void }) {
  const [users, setUsers] = useState<DemoUser[]>([]);
  const [userId, setUserId] = useState("");
  const [pin, setPin] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    void apiClient
      .listDemoUsers()
      .then((rows) => {
        if (!active) return;
        setUsers(rows);
        setUserId((current) => current || rows[0]?.app_user_id || "");
      })
      .catch(() => {
        if (active) setError("无法获取用户列表，请检查服务是否启动。");
      });
    return () => {
      active = false;
    };
  }, []);

  async function signIn(event: SyntheticEvent<HTMLFormElement, SubmitEvent>) {
    event.preventDefault();
    if (!userId || !pin || pending) return;
    setPending(true);
    setError(null);
    try {
      await apiClient.login(userId, pin);
      onAuthenticated();
    } catch (signInError) {
      if (signInError instanceof ApiClientError && signInError.status === 401) {
        setError("PIN 不正确，请重试。");
      } else {
        setError("登录服务暂不可用，请稍后重试。");
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="login-page">
      <section className="login-panel" aria-labelledby="login-heading">
        <div className="brand-mark" aria-hidden="true">
          <Bot size={22} />
        </div>
        <p className="eyebrow">General Personal Agent</p>
        <h1 id="login-heading">登录交互工作台</h1>
        <p className="login-panel__lede">
          文本与语音共用同一个账号和 conversation。选择用户并输入共享 PIN 即可继续。
        </p>
        <form onSubmit={signIn}>
          <Field label="用户">
            <Select
              value={userId}
              onChange={(event) => setUserId(event.target.value)}
              autoFocus
            >
              {users.length === 0 ? <option value="">正在加载用户…</option> : null}
              {users.map((user) => (
                <option key={user.app_user_id} value={user.app_user_id}>
                  {user.display_name}（{user.app_user_id}）
                </option>
              ))}
            </Select>
          </Field>
          <Field label="PIN">
            <PasswordInput
              value={pin}
              onChange={(event) => setPin(event.target.value)}
              placeholder="共享 PIN"
              inputMode="numeric"
              autoComplete="current-password"
              required
              disabled={pending}
              aria-describedby={error ? "login-feedback" : undefined}
              aria-invalid={error ? true : undefined}
            />
          </Field>
          <Button type="submit" className="full-width" loading={pending}>
            {pending ? <Spinner size={17} aria-hidden="true" /> : null}
            登录
          </Button>
        </form>
        {error ? (
          <Alert id="login-feedback" className="login-feedback" tone="danger">
            {error}
          </Alert>
        ) : null}
        <p className="login-panel__hint">
          <ShieldCheck size={14} aria-hidden="true" />
          登录后写入 HttpOnly 签名 Cookie；文本与语音共用。
        </p>
      </section>
    </main>
  );
}

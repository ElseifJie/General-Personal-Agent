import { Bot, Mic, Palette, ShieldCheck } from "lucide-react";
import type { ThemePreference, ThemeState } from "./theme";
import { THEME_STORAGE_KEY } from "./theme";
import { Field, PageHeader, Select } from "./ui/index";

export function SettingsPage({
  userId,
  role,
  theme,
}: {
  userId: string;
  role: string;
  theme: ThemeState;
}) {
  return (
    <div className="page settings-page">
      <PageHeader
        title="设置"
        description="账号、外观与交互入口；文本/语音共享同一个 conversation。"
      />

      <section className="settings-section settings-section--account" aria-labelledby="account-heading">
        <h2 id="account-heading">账号</h2>
        <dl className="settings-list">
          <div>
            <dt>当前账号</dt>
            <dd>{userId}</dd>
          </div>
          <div>
            <dt>角色</dt>
            <dd>{role === "admin" ? "管理员" : "普通用户"}</dd>
          </div>
          <div>
            <dt>登录方式</dt>
            <dd>用户 + 共享 PIN（与交互工作台同一 Cookie）</dd>
          </div>
        </dl>
      </section>

      <section className="settings-section" aria-labelledby="appearance-heading">
        <h2 id="appearance-heading">
          <Palette size={17} aria-hidden="true" />
          外观
        </h2>
        <div className="settings-form">
          <Field label="主题" hint={`偏好保存在浏览器本地（${THEME_STORAGE_KEY}）。`}>
            <Select
              value={theme.preference}
              onChange={(event) =>
                theme.setPreference(event.target.value as ThemePreference)
              }
            >
              <option value="system">跟随系统</option>
              <option value="light">浅色</option>
              <option value="dark">深色</option>
            </Select>
          </Field>
        </div>
      </section>

      <section className="settings-section" aria-labelledby="voice-heading">
        <h2 id="voice-heading">
          <Mic size={17} aria-hidden="true" />
          语音交互
        </h2>
        <p>
          语音采集、实时字幕、流式 TTS 与打断已接入；文本与语音严格共享同一
          conversation，切换模态不会新建会话。
        </p>
        <a className="ui-button ui-button--secondary ui-button--default" href="/interact/voice">
          打开语音交互 →
        </a>
      </section>

      <section className="settings-section" aria-labelledby="about-heading">
        <h2 id="about-heading">
          <Bot size={17} aria-hidden="true" />
          关于工作台
        </h2>
        <dl className="settings-list">
          <div>
            <dt>执行路径</dt>
            <dd>ConversationService → Personal Agent（VeADK）；任务经 Task Gateway 回传</dd>
          </div>
          <div>
            <dt>数据边界</dt>
            <dd>会话/材料/产物按登录用户隔离；产物下载走同源流式代理</dd>
          </div>
        </dl>
        <p className="settings-note">
          <ShieldCheck size={15} aria-hidden="true" />
          密码、Token 与对象存储密钥不会出现在浏览器端。
        </p>
      </section>
    </div>
  );
}

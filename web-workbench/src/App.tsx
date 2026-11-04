import {
  BookOpen,
  FolderOpen,
  FolderTree,
  History,
  MessageCircle,
  MessagesSquare,
  Settings as SettingsIcon,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";

import { apiClient, type CurrentUser } from "./api";
import { CommandPalette, type Command } from "./components/CommandPalette";
import { FilesPage } from "./FilesPage";
import { isAuthError } from "./gpaApi";
import { ConversationHistoryPage } from "./interaction/ConversationHistoryPage";
import {
  conversationIdFromPath,
  resolveCurrentEntryHref,
} from "./interaction/currentEntry";
import { clearLastActiveConversation } from "./interaction/lastActive";
import { ConversationRailList } from "./interaction/ConversationRailList";
import { TextInteractionPage } from "./interaction/TextInteractionPage";
import { VoiceInteractionPage } from "./interaction/VoiceInteractionPage";
import { useConversations } from "./interaction/useConversations";
import { LoginPage } from "./LoginPage";
import { ContextPage } from "./ContextPage";
import { KnowledgePage } from "./KnowledgePage";
import { SettingsPage } from "./SettingsPage";
import { TaskDetailPage } from "./TaskDetailPage";
import { WorkspaceFrame, WorkspaceNav, useShellLayout } from "./shell/index";
import { useThemePreference, type ThemeState } from "./theme";
import { Spinner, type NavigationItem } from "./ui/index";

type AuthState =
  | { status: "loading" }
  | { status: "unauthenticated" }
  | { status: "authenticated"; user: CurrentUser };

function LoadingScreen({ label = "加载工作区" }: { label?: string }) {
  return (
    <main className="centered-page" aria-busy="true">
      <Spinner label={label} />
      <p>正在{label}…</p>
    </main>
  );
}

export function App() {
  const [auth, setAuth] = useState<AuthState>({ status: "loading" });
  const theme = useThemePreference();

  const refreshAuth = useCallback(async () => {
    try {
      const user = await apiClient.getMe();
      setAuth({ status: "authenticated", user });
    } catch {
      setAuth({ status: "unauthenticated" });
    }
  }, []);

  const requireAuth = useCallback(
    () => setAuth({ status: "unauthenticated" }),
    [],
  );

  useEffect(() => {
    void refreshAuth();
  }, [refreshAuth]);

  useEffect(() => {
    const onUnauthorized = () => setAuth({ status: "unauthenticated" });
    window.addEventListener("wb:unauthorized", onUnauthorized);
    return () => window.removeEventListener("wb:unauthorized", onUnauthorized);
  }, []);

  if (auth.status === "loading") return <LoadingScreen label="验证登录" />;
  if (auth.status === "unauthenticated") {
    return <LoginPage onAuthenticated={() => void refreshAuth()} />;
  }
  return (
    <Workspace
      user={auth.user}
      theme={theme}
      onSignedOut={requireAuth}
      onAuthRequired={requireAuth}
    />
  );
}

function Workspace({
  user,
  theme,
  onSignedOut,
  onAuthRequired,
}: {
  user: CurrentUser;
  theme: ThemeState;
  onSignedOut: () => void;
  onAuthRequired: () => void;
}) {
  const navigate = useNavigate();
  const location = useLocation();
  const shell = useShellLayout();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [openingCurrent, setOpeningCurrent] = useState(false);
  const mobile = shell.layout.mobile;
  const conversations = useConversations(onAuthRequired, { limit: 30 });
  const activeConversationId = conversationIdFromPath(location.pathname);

  // "当前会话" resumes the conversation instead of picking a channel up
  // front: the surface the user was last on (or the conversation's own
  // channel) decides whether text or voice opens. When the user is already
  // inside it, the entry is a no-op, so a live voice session is never torn
  // down by a sidebar click.
  const openCurrentConversation = useCallback(async () => {
    if (activeConversationId) {
      setDrawerOpen(false);
      return;
    }
    if (openingCurrent) return;
    setOpeningCurrent(true);
    try {
      navigate(await resolveCurrentEntryHref());
    } catch (error) {
      if (isAuthError(error)) {
        onAuthRequired();
        return;
      }
      navigate("/interact/text");
    } finally {
      setOpeningCurrent(false);
    }
  }, [activeConversationId, navigate, onAuthRequired, openingCurrent]);

  useEffect(() => {
    const path = location.pathname;
    const label = path.startsWith("/interact/voice")
      ? "语音交互"
      : path.startsWith("/interact/text")
        ? "文本交互"
        : path === "/history"
          ? "历史会话"
          : path.startsWith("/tasks/")
            ? "任务详情"
            : path === "/files"
              ? "我的文件"
              : path === "/knowledge"
                ? "知识库管理"
                : path === "/context"
                  ? "上下文管理"
                : path === "/settings"
                      ? "设置"
                      : "交互工作台";
    document.title = `${label} · General Personal Agent`;
  }, [location.pathname]);

  useEffect(() => {
    if (!mobile) setDrawerOpen(false);
  }, [mobile]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((open) => !open);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const commands = useMemo<Command[]>(() => {
    const next = theme.resolved === "dark" ? "light" : "dark";
    const destinations: Command[] = [
      {
        id: "nav-text",
        label: "新建会话 · 文本",
        group: "前往",
        run: () => navigate("/interact/text"),
      },
      {
        id: "nav-current",
        label: "当前会话",
        group: "前往",
        run: () => void openCurrentConversation(),
      },
      {
        id: "nav-history",
        label: "历史会话",
        group: "前往",
        run: () => navigate("/history"),
      },
      {
        id: "nav-files",
        label: "我的文件",
        group: "前往",
        run: () => navigate("/files"),
      },
      {
        id: "nav-knowledge",
        label: "知识库管理",
        group: "前往",
        run: () => navigate("/knowledge"),
      },
      {
        id: "nav-context",
        label: "上下文管理",
        group: "前往",
        run: () => navigate("/context"),
      },
      {
        id: "nav-settings",
        label: "设置",
        group: "前往",
        run: () => navigate("/settings"),
      },
      {
        id: "theme-toggle",
        label: next === "dark" ? "切换深色主题" : "切换浅色主题",
        group: "外观",
        run: () => theme.setPreference(next),
      },
    ];
    const conversationCommands: Command[] = conversations.items
      .slice(0, 8)
      .map((conversation) => ({
        id: `conversation-${conversation.conversationId}`,
        label: conversation.title || "新交互",
        group: "打开会话",
        run: () =>
          navigate(`/interact/text/${conversation.conversationId}`),
      }));
    return [...destinations, ...conversationCommands];
  }, [conversations.items, navigate, openCurrentConversation, theme]);

  const navigation: NavigationItem[] = [
    { to: "/interact/text", label: "新建会话", icon: MessageCircle, end: true },
    {
      to: "/interact/current",
      label: "当前会话",
      icon: MessagesSquare,
      onSelect: () => void openCurrentConversation(),
      active: activeConversationId !== null,
    },
    { to: "/history", label: "历史会话", icon: History },
    { to: "/files", label: "我的文件", icon: FolderOpen },
    { to: "/knowledge", label: "知识库管理", icon: BookOpen },
    { to: "/context", label: "上下文管理", icon: FolderTree },
    { to: "/settings", label: "设置", icon: SettingsIcon },
  ];

  async function signOut() {
    try {
      await apiClient.logout();
    } finally {
      clearLastActiveConversation();
      onSignedOut();
    }
  }

  return (
    <WorkspaceFrame
      shell={shell}
      leftLabel="交互工作台导航"
      left={
        <WorkspaceNav
          brand="General Personal Agent"
          brandSubtitle="交互工作台"
          navigationLabel="工作区"
          navigation={navigation}
          navigationExtra={
            <ConversationRailList
              conversations={conversations.items}
              loading={conversations.loading}
              onAuthRequired={onAuthRequired}
            />
          }
          user={{ handle: user.userId }}
          onSignOut={() => void signOut()}
          collapsed={shell.layout.leftMode === "collapsed" && !(mobile && drawerOpen)}
          {...(mobile ? { onOpenDrawer: () => setDrawerOpen(true) } : {})}
          {...(mobile && drawerOpen
            ? { onCloseDrawer: () => setDrawerOpen(false) }
            : {})}
        />
      }
      drawerOpen={mobile && drawerOpen}
      drawerLabel="关闭导航"
      onCloseDrawer={() => setDrawerOpen(false)}
      onLeftWidthChange={shell.setLeftWidth}
    >
      <div className="workspace-main">
        <Routes>
          <Route path="/" element={<Navigate to="/interact/text" replace />} />
          <Route
            path="/interact/text"
            element={<TextInteractionPage onAuthRequired={onAuthRequired} />}
          />
          <Route
            path="/interact/text/:conversationId"
            element={<TextInteractionPage onAuthRequired={onAuthRequired} />}
          />
          <Route
            path="/interact/voice"
            element={<VoiceInteractionPage onAuthRequired={onAuthRequired} />}
          />
          <Route
            path="/interact/voice/:conversationId"
            element={<VoiceInteractionPage onAuthRequired={onAuthRequired} />}
          />
          <Route
            path="/history"
            element={<ConversationHistoryPage onAuthRequired={onAuthRequired} />}
          />
          <Route
            path="/tasks/:taskId"
            element={<TaskDetailPage onAuthRequired={onAuthRequired} />}
          />
          <Route
            path="/files"
            element={
              <FilesPage onAuthRequired={onAuthRequired} />
            }
          />
          <Route
            path="/knowledge"
            element={
              <KnowledgePage
                onAuthRequired={onAuthRequired}
                isAdmin={user.role === "admin"}
              />
            }
          />
          <Route
            path="/context"
            element={<ContextPage onAuthRequired={onAuthRequired} />}
          />
          <Route path="/memory" element={<Navigate to="/context" replace />} />
          <Route
            path="/settings"
            element={
              <SettingsPage userId={user.userId} role={user.role} theme={theme} />
            }
          />
          <Route path="*" element={<Navigate to="/interact/text" replace />} />
        </Routes>
      </div>
      <CommandPalette
        open={paletteOpen}
        commands={commands}
        onClose={() => setPaletteOpen(false)}
      />
    </WorkspaceFrame>
  );
}

import { LogOut, Menu, MoreHorizontal, X, type LucideIcon } from "lucide-react";
import {
  useEffect,
  useId,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import { NavLink, useLocation } from "react-router-dom";
import { IconButton } from "./Button";

export interface NavigationItem {
  to: string;
  label: string;
  icon: LucideIcon;
  /** Match the path exactly instead of by prefix (e.g. new vs. open chats). */
  end?: boolean;
  /** Present in the navigation but not usable; `hint` explains why. */
  disabled?: boolean;
  hint?: string;
  /**
   * Turns the entry into a shell action instead of a route. `to` still supplies
   * the key, and no navigation happens when this is set.
   */
  onSelect?: () => void;
  /**
   * Marks an `onSelect` action as the current page; buttons have no route for
   * `NavLink` to match against.
   */
  active?: boolean;
}

export interface AppShellProps {
  brand: string;
  navigationLabel: string;
  navigation: NavigationItem[];
  navigationExtra?: ReactNode;
  brandActions?: ReactNode;
  openNavigationLabel?: string;
  closeNavigationLabel?: string;
  backdropLabel?: string;
  user: AppShellUser;
  onSignOut: () => void;
  children: ReactNode;
}

export interface AppShellUser {
  /** Full display name; falls back to `handle`, then the initial. */
  name?: string;
  /** The account handle (usually the sign-in email or subject). */
  handle: string;
}

const mobileNavigationQuery = "(max-width: 760px)";
const drawerFocusSelector =
  'a[href]:not([tabindex="-1"]), button:not(:disabled):not([tabindex="-1"]), input:not(:disabled):not([tabindex="-1"]), select:not(:disabled):not([tabindex="-1"]), textarea:not(:disabled):not([tabindex="-1"]), [tabindex]:not([tabindex="-1"])';

function useMediaQuery(query: string) {
  const [matches, setMatches] = useState(
    () => typeof window !== "undefined" && window.matchMedia(query).matches,
  );

  useEffect(() => {
    const mediaQuery = window.matchMedia(query);
    const updateMatch = (event: MediaQueryListEvent) => {
      setMatches(event.matches);
    };

    setMatches(mediaQuery.matches);
    mediaQuery.addEventListener("change", updateMatch);
    return () => mediaQuery.removeEventListener("change", updateMatch);
  }, [query]);

  return matches;
}

function initialFor(user: AppShellUser): string {
  const source = user.name?.trim() || user.handle.trim();
  return source.charAt(0).toUpperCase() || "?";
}

export interface UserMenuProps {
  user: AppShellUser;
  tabIndex: number | undefined;
  onSignOut: () => void;
}

/**
 * The sidebar footer: who is signed in, plus a menu that owns the sign-out
 * action. The trigger is the whole footer so the account identity, not just a
 * trailing icon, is the affordance.
 */
export function UserMenu({ user, tabIndex, onSignOut }: UserMenuProps) {
  const [open, setOpen] = useState(false);
  const [panelStyle, setPanelStyle] = useState<CSSProperties>();
  const containerRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const trigger = containerRef.current?.querySelector<HTMLElement>("button");
    if (trigger) {
      const rect = trigger.getBoundingClientRect();
      const menuHeight = 48;
      const opensUp = rect.bottom + menuHeight > window.innerHeight;
      setPanelStyle({
        top: opensUp ? undefined : rect.bottom + 4,
        bottom: opensUp ? window.innerHeight - rect.top + 4 : undefined,
        left: Math.max(8, rect.left),
      });
    }
    panelRef.current?.querySelector<HTMLButtonElement>("button")?.focus();
    const onPointerDown = (event: PointerEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      setOpen(false);
      containerRef.current?.querySelector<HTMLButtonElement>("button")?.focus();
    };
    const close = () => setOpen(false);
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    window.addEventListener("resize", close);
    window.addEventListener("scroll", close, true);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("resize", close);
      window.removeEventListener("scroll", close, true);
    };
  }, [open]);

  const name = user.name?.trim();
  const label = name || user.handle;

  return (
    <div className="ui-app-shell__user" ref={containerRef}>
      <button
        type="button"
        className="ui-app-shell__user-trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`账号：${user.handle}`}
        tabIndex={tabIndex}
        onClick={() => setOpen((current) => !current)}
      >
        <span className="ui-app-shell__user-avatar" aria-hidden="true">
          {initialFor(user)}
        </span>
        <span className="ui-app-shell__user-identity">
          <span className="ui-app-shell__user-name">{label}</span>
          {name ? (
            <span className="ui-app-shell__user-handle">{user.handle}</span>
          ) : null}
        </span>
        <MoreHorizontal
          className="ui-app-shell__user-more"
          size={16}
          aria-hidden="true"
        />
      </button>
      {open ? (
        <div
          className="ui-app-shell__user-menu"
          role="menu"
          ref={panelRef}
          style={panelStyle}
        >
          <button
            type="button"
            role="menuitem"
            className="ui-app-shell__user-menu-item"
            onClick={() => {
              setOpen(false);
              onSignOut();
            }}
          >
            <LogOut size={15} aria-hidden="true" />
            退出登录
          </button>
        </div>
      ) : null}
    </div>
  );
}

export function AppShell({
  backdropLabel = "Close navigation drawer",
  brand,
  brandActions,
  children,
  closeNavigationLabel = "Close navigation",
  navigation,
  navigationExtra,
  navigationLabel,
  openNavigationLabel = "Open navigation",
  onSignOut,
  user,
}: AppShellProps) {
  const sidebarId = useId();
  const location = useLocation();
  const mobileNavigation = useMediaQuery(mobileNavigationQuery);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const navigationHidden = mobileNavigation && !drawerOpen;
  const sidebarRef = useRef<HTMLElement>(null);
  const mobileHeaderRef = useRef<HTMLElement>(null);
  const mainRef = useRef<HTMLElement>(null);
  const previousLocationKeyRef = useRef(location.key);
  const pendingCloseFocusRef = useRef<"route" | "toggle" | null>(null);

  const closeDrawer = (focusTarget: "route" | "toggle" = "toggle") => {
    pendingCloseFocusRef.current = focusTarget;
    setDrawerOpen(false);
  };

  useEffect(() => {
    if (previousLocationKeyRef.current === location.key) return;
    previousLocationKeyRef.current = location.key;
    if (!drawerOpen) return;
    pendingCloseFocusRef.current = "route";
    setDrawerOpen(false);
  }, [drawerOpen, location.key]);

  useEffect(() => {
    if (drawerOpen) return;
    const focusTarget = pendingCloseFocusRef.current;
    if (!focusTarget) return;
    pendingCloseFocusRef.current = null;

    if (focusTarget === "route") {
      const routedTarget =
        mainRef.current?.querySelector<HTMLElement>("h1") ?? mainRef.current;
      routedTarget?.focus();
      return;
    }
    mobileHeaderRef.current
      ?.querySelector<HTMLButtonElement>("button")
      ?.focus();
  }, [drawerOpen, location.key]);

  useEffect(() => {
    if (!drawerOpen) return;
    const sidebar = sidebarRef.current;
    const focusable = () =>
      Array.from(
        sidebar?.querySelectorAll<HTMLElement>(drawerFocusSelector) ?? [],
      );
    focusable()[0]?.focus();

    const containFocus = (event: KeyboardEvent) => {
      if (
        event.defaultPrevented ||
        document.activeElement?.closest('[aria-modal="true"]')
      )
        return;
      if (event.key === "Escape") {
        event.preventDefault();
        closeDrawer("toggle");
        return;
      }
      if (event.key !== "Tab") return;

      const controls = focusable();
      const first = controls[0];
      const last = controls.at(-1);
      if (!first || !last) {
        event.preventDefault();
        sidebar?.focus();
        return;
      }
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      } else if (!sidebar?.contains(document.activeElement)) {
        event.preventDefault();
        first.focus();
      }
    };

    window.addEventListener("keydown", containFocus);
    return () => window.removeEventListener("keydown", containFocus);
  }, [drawerOpen]);

  return (
    <div className="ui-app-shell">
      <aside
        ref={sidebarRef}
        id={sidebarId}
        className={`ui-app-shell__sidebar${drawerOpen ? " is-open" : ""}`}
        aria-hidden={navigationHidden || undefined}
        hidden={navigationHidden}
        inert={navigationHidden || undefined}
      >
        <div className="ui-app-shell__brand">
          <span className="ui-app-shell__brand-mark" aria-hidden="true">
            {brand.charAt(0)}
          </span>
          <span className="ui-app-shell__brand-name">{brand}</span>
          {(!mobileNavigation || drawerOpen) && brandActions ? (
            <div className="ui-app-shell__brand-actions">{brandActions}</div>
          ) : null}
        </div>
        <nav aria-label={navigationLabel} className="ui-app-shell__navigation">
          <div className="ui-app-shell__navigation-primary">
            {navigation.map(
              ({ active, disabled, hint, icon: Icon, label, to, onSelect }) =>
                disabled ? (
                  <span
                    key={to}
                    className="ui-app-shell__navigation-link is-disabled"
                    aria-disabled="true"
                    {...(hint ? { title: hint } : {})}
                  >
                    <Icon size={17} aria-hidden={true} />
                    <span>{label}</span>
                    {hint ? <span className="sr-only">{hint}</span> : null}
                  </span>
                ) : onSelect ? (
                  <button
                    key={to}
                    type="button"
                    className={`ui-app-shell__navigation-link${
                      active ? " active" : ""
                    }`}
                    aria-current={active ? "page" : undefined}
                    onClick={onSelect}
                    tabIndex={navigationHidden ? -1 : undefined}
                  >
                    <Icon size={17} aria-hidden={true} />
                    <span>{label}</span>
                  </button>
                ) : (
                  <NavLink
                    key={to}
                    to={to}
                    end={to === "/"}
                    className={({ isActive }) =>
                      `ui-app-shell__navigation-link${isActive ? " active" : ""}`
                    }
                    onClick={() => {
                      if (location.pathname === to) closeDrawer("route");
                    }}
                    tabIndex={navigationHidden ? -1 : undefined}
                  >
                    <Icon size={17} aria-hidden={true} />
                    <span>{label}</span>
                  </NavLink>
                ),
            )}
          </div>
          {navigationExtra ? (
            <div className="ui-app-shell__navigation-extra">
              {navigationExtra}
            </div>
          ) : null}
        </nav>
        <footer className="ui-app-shell__footer">
          <UserMenu
            user={user}
            tabIndex={navigationHidden ? -1 : undefined}
            onSignOut={onSignOut}
          />
        </footer>
      </aside>

      {mobileNavigation && drawerOpen ? (
        <button
          className="ui-app-shell__backdrop"
          type="button"
          aria-label={backdropLabel}
          title={backdropLabel}
          onClick={() => closeDrawer("toggle")}
        />
      ) : null}

      <div className="ui-app-shell__content">
        <header ref={mobileHeaderRef} className="ui-app-shell__mobile-header">
          <IconButton
            label={drawerOpen ? closeNavigationLabel : openNavigationLabel}
            aria-controls={sidebarId}
            aria-expanded={drawerOpen}
            onClick={() => {
              if (drawerOpen) closeDrawer("toggle");
              else setDrawerOpen(true);
            }}
          >
            {drawerOpen ? (
              <X size={18} aria-hidden="true" />
            ) : (
              <Menu size={18} aria-hidden="true" />
            )}
          </IconButton>
          <span className="ui-app-shell__brand-name">{brand}</span>
          {mobileNavigation && !drawerOpen && brandActions ? (
            <div className="ui-app-shell__brand-actions">{brandActions}</div>
          ) : null}
        </header>
        <main
          ref={mainRef}
          className="ui-app-shell__main"
          inert={(mobileNavigation && drawerOpen) || undefined}
          tabIndex={-1}
        >
          {children}
        </main>
      </div>
    </div>
  );
}

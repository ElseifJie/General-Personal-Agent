import { Menu, X } from "lucide-react";
import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";
import {
  IconButton,
  UserMenu,
  type AppShellUser,
  type NavigationItem,
} from "../ui/index";

export interface WorkspaceNavProps {
  brand: string;
  brandSubtitle?: string;
  navigationLabel: string;
  navigation: readonly NavigationItem[];
  brandActions?: ReactNode;
  navigationExtra?: ReactNode;
  user: AppShellUser;
  /** Rendered above the account menu, at the bottom of the rail. */
  bottomAction?: ReactNode;
  onSignOut(): void;
  collapsed?: boolean;
  /** Rendered on the collapsed mobile rail to open the navigation drawer. */
  onOpenDrawer?(): void;
  /** Replaces the collapsed trigger while the drawer is open. */
  onCloseDrawer?(): void;
}

/**
 * The workspace's left navigation. It reuses the app-shell navigation and user
 * menu primitives so the frame swap keeps the same focus order and wording.
 * When the frame collapses the rail, labels give way to icons while staying in
 * the accessibility tree through explicit labels.
 */
export function WorkspaceNav({
  brand,
  brandSubtitle,
  navigationLabel,
  navigation,
  brandActions,
  navigationExtra,
  user,
  bottomAction,
  onSignOut,
  collapsed = false,
  onOpenDrawer,
  onCloseDrawer,
}: WorkspaceNavProps) {
  return (
    <div
      className="workspace-nav"
      data-collapsed={collapsed ? "true" : undefined}
    >
      <div className="ui-app-shell__brand">
        <span className="ui-app-shell__brand-mark" aria-hidden="true">
          {brand.charAt(0)}
        </span>
        {collapsed ? null : (
          <span className="ui-app-shell__brand-copy">
            <span className="ui-app-shell__brand-name">{brand}</span>
            {brandSubtitle ? (
              <span className="ui-app-shell__brand-subtitle">
                {brandSubtitle}
              </span>
            ) : null}
          </span>
        )}
        {!collapsed && brandActions ? (
          <div className="ui-app-shell__brand-actions">{brandActions}</div>
        ) : null}
        {onCloseDrawer ? (
          <IconButton
            label="关闭导航"
            size="small"
            data-drawer-trigger="true"
            onClick={onCloseDrawer}
          >
            <X size={16} aria-hidden="true" />
          </IconButton>
        ) : null}
      </div>
      {collapsed && (brandActions || onOpenDrawer) ? (
        <div className="workspace-nav__compact-actions">
          {onOpenDrawer ? (
            <IconButton
              label="打开导航"
              size="small"
              data-drawer-trigger="true"
              onClick={onOpenDrawer}
            >
              <Menu size={16} aria-hidden="true" />
            </IconButton>
          ) : null}
          {brandActions}
        </div>
      ) : null}
      <nav aria-label={navigationLabel} className="ui-app-shell__navigation">
        <div className="ui-app-shell__navigation-primary">
          {navigation.map(
            ({ active, disabled, end, hint, icon: Icon, label, to, onSelect }) =>
              disabled ? (
                <span
                  key={to}
                  className="ui-app-shell__navigation-link is-disabled"
                  aria-disabled="true"
                  aria-label={collapsed ? label : undefined}
                  title={hint ?? label}
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
                  aria-label={collapsed ? label : undefined}
                  title={collapsed ? label : undefined}
                  onClick={onSelect}
                >
                  <Icon size={17} aria-hidden={true} />
                  <span>{label}</span>
                </button>
              ) : (
                <NavLink
                  key={to}
                  to={to}
                  end={end ?? to === "/"}
                  className={({ isActive }) =>
                    `ui-app-shell__navigation-link${isActive ? " active" : ""}`
                  }
                  aria-label={collapsed ? label : undefined}
                  title={collapsed ? label : undefined}
                >
                  <Icon size={17} aria-hidden={true} />
                  <span>{label}</span>
                </NavLink>
              ),
          )}
        </div>
        {!collapsed && navigationExtra ? (
          <div className="ui-app-shell__navigation-extra">
            {navigationExtra}
          </div>
        ) : null}
      </nav>
      <footer className="ui-app-shell__footer">
        {bottomAction ? (
          <div className="workspace-nav__bottom-action">{bottomAction}</div>
        ) : null}
        <UserMenu user={user} tabIndex={undefined} onSignOut={onSignOut} />
      </footer>
    </div>
  );
}

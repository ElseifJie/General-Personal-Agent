import "react";

// `inert` is a DOM attribute the React 18 typings do not declare yet. The
// ported shell uses it to take the drawer/modal region out of the tab order.
declare module "react" {
  interface HTMLAttributes<T> {
    inert?: boolean | "" | undefined;
  }
}

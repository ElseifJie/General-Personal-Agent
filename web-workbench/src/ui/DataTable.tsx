import type { TableHTMLAttributes } from "react";

export interface DataTableProps extends TableHTMLAttributes<HTMLTableElement> {
  caption: string;
  minWidth?: "standard" | "wide";
}

function classNames(...values: Array<string | undefined>) {
  return values.filter(Boolean).join(" ");
}

export function DataTable({
  caption,
  children,
  className,
  minWidth = "standard",
  ...props
}: DataTableProps) {
  return (
    <div className="ui-data-table-scroll">
      <table
        {...props}
        className={classNames(
          "ui-data-table",
          `ui-data-table--${minWidth}`,
          className,
        )}
      >
        <caption className="sr-only">{caption}</caption>
        {children}
      </table>
    </div>
  );
}

import type { ReactNode, Ref } from "react";

export interface PageHeaderProps {
  title: string;
  eyebrow?: string;
  description?: string;
  actions?: ReactNode;
  headingRef?: Ref<HTMLHeadingElement>;
}

export interface SectionHeaderProps {
  title: string;
  description?: string;
  actions?: ReactNode;
}

export function PageHeader({
  actions,
  description,
  eyebrow,
  headingRef,
  title,
}: PageHeaderProps) {
  return (
    <header className="ui-page-header">
      <div className="ui-page-header__copy">
        {eyebrow ? <p className="ui-page-header__eyebrow">{eyebrow}</p> : null}
        <h1 ref={headingRef} className="ui-page-header__title" tabIndex={-1}>
          {title}
        </h1>
        {description ? (
          <p className="ui-page-header__description">{description}</p>
        ) : null}
      </div>
      {actions ? (
        <div className="ui-page-header__actions">{actions}</div>
      ) : null}
    </header>
  );
}

export function SectionHeader({
  actions,
  description,
  title,
}: SectionHeaderProps) {
  return (
    <header className="ui-section-header">
      <div className="ui-section-header__copy">
        <h2 className="ui-section-header__title">{title}</h2>
        {description ? (
          <p className="ui-section-header__description">{description}</p>
        ) : null}
      </div>
      {actions ? (
        <div className="ui-section-header__actions">{actions}</div>
      ) : null}
    </header>
  );
}

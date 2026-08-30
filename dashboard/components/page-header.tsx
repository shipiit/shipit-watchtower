/** The heading every page starts with. */
export function PageHeader({ eyebrow, title, description, actions }: {
  eyebrow?: string;
  title: string;
  description?: string;
  actions?: React.ReactNode;
}) {
  return (
    <header className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        {eyebrow ? (
          <p className="text-xs font-bold text-foreground-tertiary">{eyebrow}</p>
        ) : null}
        <h1 className="text-xl font-bold">{title}</h1>
        {description ? (
          <p className="mt-0.5 max-w-2xl text-xs text-muted-foreground">{description}</p>
        ) : null}
      </div>
      {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
    </header>
  );
}

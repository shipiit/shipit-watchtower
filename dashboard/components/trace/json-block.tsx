const EMPTY = 'No value recorded.';

/** Truncation ceiling: past this, a payload stops being readable and starts
 *  being a scroll hazard. The full value is still one click away in `raw`. */
const MAX_CHARS = 4000;

export function JsonBlock({ value, empty = EMPTY }: { value: unknown; empty?: string }) {
  if (value == null || value === '') {
    return <p className="text-xs text-foreground-tertiary">{empty}</p>;
  }

  const text = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  const clipped = text.length > MAX_CHARS;

  return (
    <div className="overflow-hidden rounded-md border border-border">
      <pre className="max-h-80 overflow-auto bg-surface-code p-2.5 font-mono text-xs leading-relaxed">
        {clipped ? `${text.slice(0, MAX_CHARS)}\n…` : text}
      </pre>
      {clipped ? (
        <p className="border-t border-border px-2.5 py-1 text-xs text-foreground-tertiary">
          Truncated at {MAX_CHARS.toLocaleString()} characters of {text.length.toLocaleString()}.
        </p>
      ) : null}
    </div>
  );
}

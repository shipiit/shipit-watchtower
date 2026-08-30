'use client';

import { Check, Copy } from 'lucide-react';
import { useState } from 'react';
import { Button } from '@/components/ui/button';

/** Copy, with a confirmation that fades rather than a toast that interrupts. */
export function CopyButton({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      // Clipboard access can be refused outright. Silently failing is wrong,
      // but so is a dialog — the unchanged icon is the honest signal.
    }
  }

  return (
    <Button variant="ghost" size="icon-sm" onClick={copy} aria-label={label}>
      {copied ? <Check size={13} className="text-dark-green" /> : <Copy size={13} />}
    </Button>
  );
}

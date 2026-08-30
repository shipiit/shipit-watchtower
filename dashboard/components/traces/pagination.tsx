import { ArrowLeft, ArrowRight } from 'lucide-react';
import Link from 'next/link';
import { Button } from '@/components/ui/button';
import { count } from '@/lib/utils';
import { href } from './columns';

interface Props {
  total: number;
  page: number;
  pages: number;
  label: string;
  active: Record<string, string>;
}

export function TracePagination({ total, page, pages, label, active }: Props) {
  return (
    <div className="flex items-center justify-between text-xs text-muted-foreground">
      <span>{count(total)} {label} · page {page} of {pages}</span>
      <div className="flex gap-1.5">
        <Step
          to={page > 1 ? href(active, { page: page - 1 }) : null}
          icon={<ArrowLeft size={13} />}
          label="Previous"
        />
        <Step
          to={page < pages ? href(active, { page: page + 1 }) : null}
          label="Next"
          icon={<ArrowRight size={13} />}
          trailing
        />
      </div>
    </div>
  );
}

/** Disabled renders as a real disabled button, not a link that goes nowhere. */
function Step({ to, icon, label, trailing = false }: {
  to: string | null;
  icon: React.ReactNode;
  label: string;
  trailing?: boolean;
}) {
  const content = trailing ? <>{label}{icon}</> : <>{icon}{label}</>;
  if (!to) return <Button variant="outline" size="sm" disabled>{content}</Button>;
  return (
    <Button variant="outline" size="sm" asChild>
      <Link href={to}>{content}</Link>
    </Button>
  );
}

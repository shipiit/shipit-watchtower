import {
  Activity, BarChart3, BookOpen, Boxes, BrainCircuit, CircleDollarSign,
  Database, Gauge, MessageSquareText, ShieldCheck, Users, type LucideIcon,
} from 'lucide-react';

export interface NavLink {
  href: string;
  label: string;
  icon: LucideIcon;
}

/** The nav, as data — so the sidebar and the ⌘K palette cannot disagree. */
export const NAV_GROUPS: Array<{ label: string; links: NavLink[] }> = [
  { label: 'Observe', links: [
    { href: '/', label: 'Overview', icon: Gauge },
    { href: '/traces', label: 'Traces', icon: Activity },
    { href: '/sessions', label: 'Sessions', icon: MessageSquareText },
    { href: '/users', label: 'Users', icon: Users },
    { href: '/models', label: 'Models', icon: BrainCircuit },
  ] },
  { label: 'Improve', links: [
    { href: '/prompts', label: 'Prompts', icon: BookOpen },
    { href: '/datasets', label: 'Datasets', icon: Database },
    { href: '/evaluations', label: 'Evaluations', icon: BarChart3 },
  ] },
  { label: 'Govern', links: [
    { href: '/policies', label: 'Policies', icon: ShieldCheck },
    { href: '/costs', label: 'Costs & budgets', icon: CircleDollarSign },
    { href: '/backends', label: 'Backends', icon: Boxes },
  ] },
];

export const isActive = (pathname: string, href: string) =>
  href === '/' ? pathname === '/' : pathname.startsWith(href);

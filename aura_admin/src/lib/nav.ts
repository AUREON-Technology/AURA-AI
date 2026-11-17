import {
  Boxes,
  LayoutDashboard,
  Library,
  MessageSquareText,
  ScrollText,
  Server,
  Settings,
  ShieldCheck,
  TrendingUp,
  Users,
  type LucideIcon,
} from "lucide-react";

export type NavItem = { href: string; label: string; icon: LucideIcon };
export type NavGroup = { label: string; items: NavItem[] };

/** The one list of console destinations: the sidebar and the command palette both read it. */
export const NAV_GROUPS: NavGroup[] = [
  {
    label: "Operate",
    items: [
      { href: "/", label: "Overview", icon: LayoutDashboard },
      { href: "/providers", label: "Providers", icon: Server },
      { href: "/models", label: "Models", icon: Boxes },
      { href: "/knowledge", label: "Knowledge", icon: Library },
    ],
  },
  {
    label: "Intelligence",
    items: [
      { href: "/prompts", label: "Prompts", icon: MessageSquareText },
      { href: "/learning", label: "Learning", icon: TrendingUp },
    ],
  },
  {
    label: "Governance",
    items: [
      { href: "/approvals", label: "Approvals", icon: ShieldCheck },
      { href: "/audit", label: "Audit", icon: ScrollText },
      { href: "/access", label: "Access", icon: Users },
      { href: "/settings", label: "Settings", icon: Settings },
    ],
  },
];

export const NAV_ITEMS: NavItem[] = NAV_GROUPS.flatMap((group) => group.items);

export function groupFor(pathname: string): { group: string; item: NavItem } | null {
  for (const group of NAV_GROUPS) {
    const item = group.items.find((entry) =>
      entry.href === "/" ? pathname === "/" : pathname === entry.href || pathname.startsWith(`${entry.href}/`),
    );
    if (item) {
      return { group: group.label, item };
    }
  }
  return null;
}

/** Fired by the top bar's search button; the palette listens for it. */
export const OPEN_PALETTE_EVENT = "aura:open-palette";

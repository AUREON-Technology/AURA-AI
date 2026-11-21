import {
  Boxes,
  CreditCard,
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

import type { MessageKey } from "@/lib/i18n";

/** Labels are dictionary keys (src/lib/i18n), translated where they are rendered. */
export type NavItem = {
  href: string;
  label: MessageKey;
  icon: LucideIcon;
  /** Shown only to a Super Admin. The gateway enforces it; hiding it is convenience. */
  superAdminOnly?: boolean;
};
export type NavGroup = { label: MessageKey; items: NavItem[] };

/** The one list of console destinations: the sidebar and the command palette both read it. */
export const NAV_GROUPS: NavGroup[] = [
  {
    label: "nav.group.operate",
    items: [
      { href: "/", label: "nav.overview", icon: LayoutDashboard },
      { href: "/providers", label: "nav.providers", icon: Server },
      { href: "/models", label: "nav.models", icon: Boxes },
      { href: "/knowledge", label: "nav.knowledge", icon: Library },
    ],
  },
  {
    label: "nav.group.intelligence",
    items: [
      { href: "/prompts", label: "nav.prompts", icon: MessageSquareText },
      { href: "/learning", label: "nav.learning", icon: TrendingUp },
    ],
  },
  {
    label: "nav.group.governance",
    items: [
      { href: "/approvals", label: "nav.approvals", icon: ShieldCheck },
      { href: "/audit", label: "nav.audit", icon: ScrollText },
      { href: "/access", label: "nav.access", icon: Users },
      { href: "/customers", label: "nav.customers", icon: CreditCard },
      { href: "/settings", label: "nav.settings", icon: Settings, superAdminOnly: true },
    ],
  },
];

export const NAV_ITEMS: NavItem[] = NAV_GROUPS.flatMap((group) => group.items);

/** The destinations this operator may open: Settings (the control center) is Super Admin only. */
export function navGroupsFor(me: { is_super_admin: boolean } | null): NavGroup[] {
  return NAV_GROUPS.map((group) => ({
    ...group,
    items: group.items.filter((item) => !item.superAdminOnly || Boolean(me?.is_super_admin)),
  })).filter((group) => group.items.length > 0);
}

export function groupFor(pathname: string): { group: MessageKey; item: NavItem } | null {
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

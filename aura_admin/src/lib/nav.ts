import type { IconName } from "@/components/icons";

export type NavItem = { href: string; label: string; icon: IconName };
export type NavGroup = { label: string; items: NavItem[] };

/** The one list of console destinations: the sidebar and the command palette both read it. */
export const NAV_GROUPS: NavGroup[] = [
  {
    label: "Operate",
    items: [
      { href: "/", label: "Overview", icon: "overview" },
      { href: "/providers", label: "Providers", icon: "providers" },
      { href: "/models", label: "Models", icon: "models" },
      { href: "/knowledge", label: "Knowledge", icon: "knowledge" },
    ],
  },
  {
    label: "Intelligence",
    items: [
      { href: "/prompts", label: "Prompts", icon: "prompts" },
      { href: "/learning", label: "Learning", icon: "learning" },
    ],
  },
  {
    label: "Governance",
    items: [
      { href: "/approvals", label: "Approvals", icon: "approvals" },
      { href: "/audit", label: "Audit", icon: "audit" },
      { href: "/access", label: "Access", icon: "access" },
      { href: "/settings", label: "Settings", icon: "settings" },
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

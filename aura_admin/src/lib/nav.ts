import type { IconName } from "@/components/icons";
import type { MessageKey } from "@/lib/i18n";

/** Labels are dictionary keys (src/lib/i18n), translated where they are rendered. */
export type NavItem = { href: string; label: MessageKey; icon: IconName };
export type NavGroup = { label: MessageKey; items: NavItem[] };

/** The one list of console destinations: the sidebar and the command palette both read it. */
export const NAV_GROUPS: NavGroup[] = [
  {
    label: "nav.group.operate",
    items: [
      { href: "/", label: "nav.overview", icon: "overview" },
      { href: "/providers", label: "nav.providers", icon: "providers" },
      { href: "/models", label: "nav.models", icon: "models" },
      { href: "/knowledge", label: "nav.knowledge", icon: "knowledge" },
    ],
  },
  {
    label: "nav.group.intelligence",
    items: [
      { href: "/prompts", label: "nav.prompts", icon: "prompts" },
      { href: "/learning", label: "nav.learning", icon: "learning" },
    ],
  },
  {
    label: "nav.group.governance",
    items: [
      { href: "/approvals", label: "nav.approvals", icon: "approvals" },
      { href: "/audit", label: "nav.audit", icon: "audit" },
      { href: "/access", label: "nav.access", icon: "access" },
      { href: "/settings", label: "nav.settings", icon: "settings" },
    ],
  },
];

export const NAV_ITEMS: NavItem[] = NAV_GROUPS.flatMap((group) => group.items);

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

"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { CornerDownLeft, Search } from "lucide-react";

import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Kbd } from "@/components/ui/kbd";
import { useI18n } from "@/lib/i18n/context";
import { NAV_GROUPS, OPEN_PALETTE_EVENT, type NavItem } from "@/lib/nav";
import { cn } from "@/lib/utils";

export function CommandPalette() {
  const router = useRouter();
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen((value) => !value);
      }
    }
    function onOpen() {
      setOpen(true);
    }
    window.addEventListener("keydown", onKey);
    window.addEventListener(OPEN_PALETTE_EVENT, onOpen);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener(OPEN_PALETTE_EVENT, onOpen);
    };
  }, []);

  const needle = query.toLowerCase();
  const groups = NAV_GROUPS.map((group) => ({
    label: group.label,
    items: group.items.filter((item) => t(item.label).toLowerCase().includes(needle)),
  })).filter((group) => group.items.length > 0);
  const matches: NavItem[] = groups.flatMap((group) => group.items);
  const current = Math.min(active, Math.max(matches.length - 1, 0));

  function onOpenChange(next: boolean) {
    setOpen(next);
    if (!next) {
      setQuery("");
      setActive(0);
    }
  }

  function go(href: string) {
    router.push(href);
    onOpenChange(false);
  }

  function move(delta: number) {
    if (matches.length === 0) {
      return;
    }
    const next = (current + delta + matches.length) % matches.length;
    setActive(next);
    listRef.current?.querySelector<HTMLElement>(`[data-index="${next}"]`)?.scrollIntoView({ block: "nearest" });
  }

  let index = -1;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent hideClose className="w-[min(540px,calc(100vw-32px))] overflow-hidden p-0" aria-label={t("palette.aria")}>
        <DialogTitle className="sr-only">{t("palette.aria")}</DialogTitle>
        <DialogDescription className="sr-only">{t("palette.hint")}</DialogDescription>
        <div className="flex items-center gap-2.5 border-b border-line px-4">
          <Search aria-hidden="true" className="size-4 shrink-0 text-faint" />
          <input
            autoFocus
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setActive(0);
            }}
            onKeyDown={(event) => {
              if (event.key === "ArrowDown") {
                event.preventDefault();
                move(1);
              } else if (event.key === "ArrowUp") {
                event.preventDefault();
                move(-1);
              } else if (event.key === "Enter" && matches[current]) {
                go(matches[current].href);
              }
            }}
            placeholder={t("palette.placeholder")}
            aria-label={t("palette.inputAria")}
            className="h-13 w-full bg-transparent text-[15px] text-fg placeholder:text-faint focus:outline-none"
          />
          <Kbd>Esc</Kbd>
        </div>
        <div ref={listRef} className="max-h-[min(380px,60vh)] overflow-y-auto p-2">
          {groups.map((group) => (
            <div key={group.label} className="mb-1 last:mb-0">
              <p className="px-2.5 pt-2 pb-1 text-[11px] font-semibold tracking-[0.08em] text-faint uppercase">
                {t(group.label)}
              </p>
              {group.items.map((item) => {
                index += 1;
                const mine = index;
                const selected = mine === current;
                const ItemIcon = item.icon;
                return (
                  <button
                    key={item.href}
                    type="button"
                    data-index={mine}
                    onMouseMove={() => setActive(mine)}
                    onClick={() => go(item.href)}
                    className={cn(
                      "flex w-full items-center gap-3 rounded-sm px-2.5 py-2 text-start text-sm transition-colors duration-100",
                      selected ? "bg-brand-soft text-fg" : "text-muted",
                    )}
                  >
                    <span
                      aria-hidden="true"
                      className={cn(
                        "grid size-7 place-items-center rounded-sm border border-line transition-colors",
                        selected ? "border-transparent bg-linear-135 from-brand to-brand-2 text-white" : "bg-surface-variant",
                      )}
                    >
                      <ItemIcon className="size-4" />
                    </span>
                    <span className="flex-1">{t(item.label)}</span>
                    {selected ? <CornerDownLeft aria-hidden="true" className="size-3.5 text-faint rtl:-scale-x-100" /> : null}
                  </button>
                );
              })}
            </div>
          ))}
          {matches.length === 0 ? (
            <p className="px-3 py-6 text-center text-[13px] text-muted">{t("palette.noMatch", { query })}</p>
          ) : null}
        </div>
        <div className="flex items-center gap-4 border-t border-line bg-surface-variant/50 px-4 py-2 text-[11.5px] text-faint">
          <span className="flex items-center gap-1.5">
            <Kbd>↑</Kbd>
            <Kbd>↓</Kbd> {t("palette.navigate")}
          </span>
          <span className="flex items-center gap-1.5">
            <Kbd>↵</Kbd> {t("palette.open")}
          </span>
        </div>
      </DialogContent>
    </Dialog>
  );
}

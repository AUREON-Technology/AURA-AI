"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { Icon } from "@/components/icons";
import { useI18n } from "@/lib/i18n/context";
import { NAV_ITEMS, OPEN_PALETTE_EVENT } from "@/lib/nav";

export function CommandPalette() {
  const { t } = useI18n();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen((value) => !value);
      }
      if (event.key === "Escape") {
        setOpen(false);
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

  if (!open) {
    return null;
  }

  const matches = NAV_ITEMS.filter((item) => t(item.label).toLowerCase().includes(query.toLowerCase()));

  function go(href: string) {
    router.push(href);
    setOpen(false);
    setQuery("");
  }

  return (
    <>
      <div className="palette-backdrop" aria-hidden="true" onClick={() => setOpen(false)} />
      <div className="palette" role="dialog" aria-label={t("palette.aria")}>
        <input
          autoFocus
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && matches[0]) {
              go(matches[0].href);
            }
          }}
          placeholder={t("palette.placeholder")}
          aria-label={t("palette.inputAria")}
        />
        {matches.map((item) => (
          <button key={item.href} className="btn" type="button" onClick={() => go(item.href)}>
            <Icon name={item.icon} size={16} />
            {t(item.label)}
          </button>
        ))}
        {matches.length === 0 ? <p className="empty">{t("palette.noMatch", { query })}</p> : null}
      </div>
    </>
  );
}

"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { Icon } from "@/components/icons";
import { NAV_ITEMS, OPEN_PALETTE_EVENT } from "@/lib/nav";

export function CommandPalette() {
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

  const matches = NAV_ITEMS.filter((item) => item.label.toLowerCase().includes(query.toLowerCase()));

  function go(href: string) {
    router.push(href);
    setOpen(false);
    setQuery("");
  }

  return (
    <>
      <div className="palette-backdrop" aria-hidden="true" onClick={() => setOpen(false)} />
      <div className="palette" role="dialog" aria-label="Command palette">
        <input
          autoFocus
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && matches[0]) {
              go(matches[0].href);
            }
          }}
          placeholder="Go to…"
          aria-label="Go to page"
        />
        {matches.map((item) => (
          <button key={item.href} className="btn" type="button" onClick={() => go(item.href)}>
            <Icon name={item.icon} size={16} />
            {item.label}
          </button>
        ))}
        {matches.length === 0 ? <p className="empty">No page matches “{query}”.</p> : null}
      </div>
    </>
  );
}

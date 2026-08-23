"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

const DESTINATIONS = [
  ["/", "Overview"],
  ["/providers", "Providers"],
  ["/knowledge", "Knowledge"],
  ["/prompts", "Prompts"],
  ["/models", "Models"],
  ["/approvals", "Approvals"],
  ["/learning", "Learning"],
  ["/audit", "Audit"],
  ["/access", "Access"],
  ["/settings", "Settings"],
] as const;

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
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (!open) {
    return null;
  }

  const matches = DESTINATIONS.filter(([, label]) =>
    label.toLowerCase().includes(query.toLowerCase()),
  );

  return (
    <div className="palette" role="dialog" aria-label="Command palette">
      <input
        autoFocus
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        placeholder="Go to…"
      />
      {matches.map(([href, label]) => (
        <button
          key={href}
          className="btn"
          type="button"
          onClick={() => {
            router.push(href);
            setOpen(false);
            setQuery("");
          }}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

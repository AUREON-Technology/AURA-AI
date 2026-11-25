"use client";

import { useState } from "react";

import type { CloudflareOverview } from "@/lib/control";
import { useI18n } from "@/lib/i18n/context";
import type { MessageKey } from "@/lib/i18n";

import { ModelsSection } from "./models-section";
import { ProviderDialog, type ProviderDraft } from "./provider-dialog";
import { ProvidersSection } from "./providers-section";

/**
 * The Cloudflare tab's Providers and Models sections and the Add / Replace dialog they share (a
 * model's Add button opens the dialog with that model chosen). Needs the connection and its account
 * id; without them the page already says what is missing and nothing here renders. ``onDone`` reports
 * a finished change as a sentence, with ``true`` as its second argument so the page links the
 * Changes tab, where the watched change can be followed.
 */
export function CloudflareProviders({
  overview,
  onDone,
}: {
  overview: CloudflareOverview;
  onDone: (message: string, watched?: boolean) => void;
}) {
  const { t } = useI18n();
  const [draft, setDraft] = useState<ProviderDraft | null>(null);

  if (!overview.connected || !overview.account_id) return null;

  function finished(kind: "added" | "replaced" | "removed", name: string) {
    setDraft(null);
    const done = t(`settings.cloudflare.providers.${kind}` as MessageKey, { name });
    onDone(`${done} ${t("settings.cloudflare.providers.watched")}`, true);
  }

  return (
    <>
      <ProvidersSection
        overview={overview}
        onAdd={() => setDraft({ mode: "add", model: "", contextWindow: null, vision: false })}
        onReplace={(row) => setDraft({ mode: "replace", row })}
        onRemoved={(name) => finished("removed", name)}
      />
      <ModelsSection
        overview={overview}
        onAdd={(row) => setDraft({ mode: "add", model: row.model, contextWindow: row.context_window, vision: row.vision })}
      />
      {draft ? (
        <ProviderDialog
          key={draft.mode === "add" ? `add:${draft.model}` : `replace:${draft.row.name}`}
          draft={draft}
          overview={overview}
          onClose={() => setDraft(null)}
          onDone={finished}
        />
      ) : null}
    </>
  );
}

"use client";

import { Button } from "@/components/ui/button";
import { LANG_COOKIE } from "@/lib/i18n";
import { useI18n } from "@/lib/i18n/context";

const ONE_YEAR_S = 60 * 60 * 24 * 365;

/**
 * EN / ع: writes the language cookie and reloads, so the root layout renders `<html lang dir>`
 * and every string from the cookie on the server. A full reload rather than `router.refresh()`:
 * a soft refresh left `<html dir>` as it was in `next dev`, and a language switch losing
 * in-page state (an open dialog, a typed filter) is acceptable.
 */
export function LanguageToggle() {
  const { lang, t } = useI18n();
  const next = lang === "ar" ? "en" : "ar";
  return (
    <Button
      variant="outline"
      size="sm"
      className="min-w-9 px-2.5 font-semibold"
      aria-label={t("lang.switch")}
      title={t("lang.switch")}
      data-testid="lang-toggle"
      onClick={() => {
        document.cookie = `${LANG_COOKIE}=${next}; path=/; max-age=${ONE_YEAR_S}; samesite=lax`;
        window.location.reload();
      }}
    >
      <span lang={next}>{next === "ar" ? "ع" : "EN"}</span>
    </Button>
  );
}

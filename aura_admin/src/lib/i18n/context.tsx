"use client";

import { createContext, useCallback, useContext, useMemo, type ReactNode } from "react";

import type { MessageKey } from "./en";
import { describeError } from "./errors";
import { translate, type Lang, type Vars } from "./index";

type I18n = {
  lang: Lang;
  t: (key: MessageKey, vars?: Vars) => string;
  /** Banner text for a caught error (see errors.ts). */
  errorText: (err: unknown) => string;
};

const I18nContext = createContext<Lang>("en");

/** Fed by the root layout, which reads the language cookie on the server. */
export function I18nProvider({ lang, children }: { lang: Lang; children: ReactNode }) {
  return <I18nContext.Provider value={lang}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18n {
  const lang = useContext(I18nContext);
  const t = useCallback((key: MessageKey, vars?: Vars) => translate(lang, key, vars), [lang]);
  const errorText = useCallback((err: unknown) => describeError(err, lang, t), [lang, t]);
  return useMemo(() => ({ lang, t, errorText }), [lang, t, errorText]);
}

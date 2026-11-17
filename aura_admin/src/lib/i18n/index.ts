import { ar } from "./ar";
import { en, type Dictionary, type MessageKey } from "./en";

export type { MessageKey } from "./en";

export type Lang = "en" | "ar";
export type Vars = Record<string, string | number>;

/** Set by the shell's language toggle; read by the root layout to render `<html lang dir>`. */
export const LANG_COOKIE = "aura_console_lang";

const DICTIONARIES: Record<Lang, Dictionary> = { en, ar };

/** Anything but "ar" is English, the default (and what the e2e suite runs in). */
export function parseLang(value: string | undefined | null): Lang {
  return value === "ar" ? "ar" : "en";
}

export function dirFor(lang: Lang): "ltr" | "rtl" {
  return lang === "ar" ? "rtl" : "ltr";
}

export function translate(lang: Lang, key: MessageKey, vars?: Vars): string {
  const message = DICTIONARIES[lang][key];
  if (!vars) {
    return message;
  }
  return message.replace(/\{(\w+)\}/g, (match, name: string) =>
    name in vars ? String(vars[name]) : match,
  );
}

import type { Metadata } from "next";
import localFont from "next/font/local";
import { cookies } from "next/headers";

import { I18nProvider } from "@/lib/i18n/context";
import { LANG_COOKIE, dirFor, parseLang } from "@/lib/i18n";

import "./globals.css";

// Cairo for Arabic (plan decision 13), served from committed files: no build-time network.
// unicode-range keeps it to Arabic script, so English text renders exactly as before and an
// English page never downloads it; no preload and no size-adjusted Arial fallback for the same
// reason. It sits after Inter in `--font` (globals.css).
const cairo = localFont({
  src: [
    { path: "./fonts/cairo/Cairo-Regular.ttf", weight: "400", style: "normal" },
    { path: "./fonts/cairo/Cairo-Medium.ttf", weight: "500", style: "normal" },
    { path: "./fonts/cairo/Cairo-SemiBold.ttf", weight: "600", style: "normal" },
    { path: "./fonts/cairo/Cairo-Bold.ttf", weight: "700", style: "normal" },
  ],
  variable: "--font-cairo",
  display: "swap",
  preload: false,
  adjustFontFallback: false,
  declarations: [
    {
      prop: "unicode-range",
      value: "U+0600-06FF, U+0750-077F, U+0870-08FF, U+FB50-FDFF, U+FE70-FEFF, U+200C-200F",
    },
  ],
});

export const metadata: Metadata = {
  title: "AURA Console",
  description: "Operator console for the AURA AI gateway",
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  // Read on the server so the first paint already has the right direction (no LTR flash).
  const lang = parseLang((await cookies()).get(LANG_COOKIE)?.value);
  return (
    <html lang={lang} dir={dirFor(lang)} className={cairo.variable}>
      <body>
        <I18nProvider lang={lang}>{children}</I18nProvider>
      </body>
    </html>
  );
}

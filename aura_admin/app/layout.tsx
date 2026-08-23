import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "AURA Console",
  description: "Operator console for the AURA AI gateway",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

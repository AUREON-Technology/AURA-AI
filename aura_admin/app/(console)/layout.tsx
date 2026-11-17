import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { Shell } from "@/components/shell";
import { ACCESS_COOKIE, apiOrigin } from "@/lib/session";
import type { Me } from "@/lib/types";

async function loadMe(): Promise<Me | null> {
  const token = (await cookies()).get(ACCESS_COOKIE)?.value;
  if (!token) {
    return null;
  }
  const response = await fetch(`${apiOrigin()}/api/v3/admin/me`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  if (!response.ok) {
    return null;
  }
  return (await response.json()) as Me;
}

export default async function ConsoleLayout({ children }: { children: React.ReactNode }) {
  const me = await loadMe();
  if (!me) {
    redirect("/login");
  }
  if (!me.is_admin) {
    // /admin/me admits Developer, but every page's data needs Admin (audit INT-042).
    redirect("/login?reason=role");
  }
  return <Shell me={me}>{children}</Shell>;
}

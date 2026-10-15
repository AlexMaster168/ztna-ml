import Link from "next/link";

import { LanguageSwitch } from "@/components/language-switch";
import { getT } from "@/i18n/server";
import { api, can, type Me } from "@/lib/orchestrator";


export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const [me, t] = await Promise.all([api<Me>("/api/me"), getT()]);
  const nav = [
    { href: "/", label: t("nav.overview") },
    { href: "/nodes", label: t("nav.nodes") },
    { href: "/incidents", label: t("nav.incidents") },
    { href: "/access", label: t("nav.access") },
    { href: "/gateways", label: t("nav.gateways") },
    { href: "/models", label: t("nav.models") },
    { href: "/settings", label: t("nav.enforcement") },
    ...(can(me, "analyst") ? [{ href: "/notifications", label: t("nav.notifications") }] : []),
    ...(can(me, "analyst") ? [{ href: "/audit", label: t("nav.audit") }] : []),
  ];
  return (
    <div className="min-h-screen">
      <header className="border-b border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
          <Link href="/" className="font-semibold">
            ZTNA
          </Link>
          <nav className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
            {nav.map((n) => (
              <Link
                key={n.href}
                href={n.href}
                className="text-zinc-600 hover:text-zinc-950 dark:text-zinc-400 dark:hover:text-white"
              >
                {n.label}
              </Link>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3 text-sm">
            <LanguageSwitch current={t.locale} />
            <span className="text-zinc-500">
              {me.username} · {t.v("role", me.role)}
              {me.sso && " · SSO"}
            </span>
            <a href="/api/auth/logout" className="btn btn-secondary">
              {t("nav.signOut")}
            </a>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
    </div>
  );
}

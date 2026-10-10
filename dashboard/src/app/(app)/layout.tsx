import Link from "next/link";

import { api, can, type Me } from "@/lib/orchestrator";

import { logout } from "../login/actions";

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const me = await api<Me>("/api/me");
  const nav = [
    { href: "/", label: "Overview" },
    { href: "/nodes", label: "Nodes" },
    { href: "/incidents", label: "Incidents" },
    { href: "/models", label: "Models" },
    { href: "/settings", label: "Enforcement" },
    ...(can(me, "analyst") ? [{ href: "/audit", label: "Audit" }] : []),
  ];
  return (
    <div className="min-h-screen">
      <header className="border-b border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
          <Link href="/" className="font-semibold">
            ZTNA
          </Link>
          <nav className="flex gap-4 text-sm">
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
            <span className="text-zinc-500">
              {me.username} · {me.role}
            </span>
            <form action={logout}>
              <button className="btn btn-secondary">Sign out</button>
            </form>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
    </div>
  );
}

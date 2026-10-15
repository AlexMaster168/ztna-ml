import { LanguageSwitch } from "@/components/language-switch";
import { getT } from "@/i18n/server";
import { ssoEnabled } from "@/lib/oidc";

import { LoginForm } from "./login-form";

const SSO_ERRORS = ["denied", "state", "exchange", "forbidden", "unreachable"];

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string; sso_error?: string }>;
}) {
  const [{ next, sso_error: ssoError }, t] = await Promise.all([searchParams, getT()]);
  const target = next ?? "/";
  return (
    <main className="flex min-h-screen items-center justify-center p-4">
      <div className="w-full max-w-sm rounded-xl border border-zinc-200 bg-white p-6 shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
        <div className="flex items-start justify-between gap-2">
          <div>
            <h1 className="text-xl font-semibold">{t("login.title")}</h1>
            <p className="mt-1 text-sm text-zinc-500">{t("login.subtitle")}</p>
          </div>
          <LanguageSwitch current={t.locale} />
        </div>
        {ssoEnabled() && (
          <>
            <a
              href={`/api/auth/sso/login?next=${encodeURIComponent(target)}`}
              className="btn btn-primary mt-6 w-full"
            >
              {t("login.sso")}
            </a>
            {ssoError && SSO_ERRORS.includes(ssoError) && (
              <p role="alert" className="mt-2 text-sm text-red-600 dark:text-red-400">
                {t.v("ssoError", ssoError)}
              </p>
            )}
            <div className="mt-6 flex items-center gap-3 text-xs text-zinc-500">
              <span className="h-px flex-1 bg-zinc-200 dark:bg-zinc-800" />
              {t("login.or")}
              <span className="h-px flex-1 bg-zinc-200 dark:bg-zinc-800" />
            </div>
          </>
        )}
        <LoginForm next={target} />
      </div>
    </main>
  );
}

import { LoginForm } from "./login-form";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string }>;
}) {
  const { next } = await searchParams;
  return (
    <main className="flex min-h-screen items-center justify-center p-4">
      <div className="w-full max-w-sm rounded-xl border border-zinc-200 bg-white p-6 shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
        <h1 className="text-xl font-semibold">ZTNA Control Panel</h1>
        <p className="mt-1 text-sm text-zinc-500">Sign in to manage zero-trust access</p>
        <LoginForm next={next ?? "/"} />
      </div>
    </main>
  );
}

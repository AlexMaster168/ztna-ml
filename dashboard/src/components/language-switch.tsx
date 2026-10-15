import { LOCALES, type Locale } from "@/i18n";
import { setLocale } from "@/i18n/actions";

const LABEL: Record<Locale, string> = { uk: "УКР", en: "ENG" };

export function LanguageSwitch({ current }: { current: Locale }) {
  return (
    <form action={setLocale} className="flex overflow-hidden rounded-md border border-zinc-300 text-xs dark:border-zinc-700">
      {LOCALES.map((l) => (
        <button
          key={l}
          name="locale"
          value={l}
          aria-pressed={l === current}
          className={`px-2 py-1 ${l === current ? "bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900" : "hover:bg-zinc-100 dark:hover:bg-zinc-800"}`}
        >
          {LABEL[l]}
        </button>
      ))}
    </form>
  );
}

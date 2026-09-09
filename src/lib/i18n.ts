import en from "@/locales/en.json";
import hi from "@/locales/hi.json";
import ta from "@/locales/ta.json";
import bn from "@/locales/bn.json";
import ml from "@/locales/ml.json";
import te from "@/locales/te.json";
import type { Lang } from "./types";

export type Dict = typeof en;

export const DICTS: Record<Lang, Dict> = {
  en, hi: hi as Dict, ta: ta as Dict, bn: bn as Dict, ml: ml as Dict, te: te as Dict,
};

export function dict(lang: Lang): Dict {
  return DICTS[lang] ?? DICTS.en;
}

/** Resolve a dotted key against a locale, falling back to English then to the key itself. */
export function t(lang: Lang, key: string, vars: Record<string, string | number> = {}): string {
  const read = (d: Dict): unknown =>
    key.split(".").reduce<unknown>((acc, k) => (acc && typeof acc === "object" ? (acc as Record<string, unknown>)[k] : undefined), d);
  const raw = read(dict(lang)) ?? read(DICTS.en);
  if (typeof raw !== "string") return key;
  return raw.replace(/\{(\w+)\}/g, (_, k: string) => (k in vars ? String(vars[k]) : `{${k}}`));
}

/** Resolve a dotted key that holds an array of strings (e.g. the suggested-question chips). */
export function tList(lang: Lang, key: string): string[] {
  const read = (d: Dict): unknown =>
    key.split(".").reduce<unknown>((acc, k) => (acc && typeof acc === "object" ? (acc as Record<string, unknown>)[k] : undefined), d);
  const raw = read(dict(lang)) ?? read(DICTS.en);
  return Array.isArray(raw) ? (raw as string[]) : [];
}

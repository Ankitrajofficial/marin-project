import type { Lang } from "./types";

export const LANGS: Array<{ code: Lang; label: string; native: string; speech: string }> = [
  { code: "en", label: "English", native: "English", speech: "en-IN" },
  { code: "hi", label: "Hindi", native: "हिन्दी", speech: "hi-IN" },
  { code: "ta", label: "Tamil", native: "தமிழ்", speech: "ta-IN" },
  { code: "bn", label: "Bengali", native: "বাংলা", speech: "bn-IN" },
  { code: "ml", label: "Malayalam", native: "മലയാളം", speech: "ml-IN" },
  { code: "te", label: "Telugu", native: "తెలుగు", speech: "te-IN" },
];

/**
 * Script-range language detection. Deliberately simple and dependency-free: for the six
 * supported languages the writing systems do not overlap, so a script check is exact.
 * Production equivalent: Bhashini's language identification endpoint.
 */
export function detectLanguage(text: string): Lang {
  if (/[஀-௿]/.test(text)) return "ta";
  if (/[ঀ-৿]/.test(text)) return "bn";
  if (/[ഀ-ൿ]/.test(text)) return "ml";
  if (/[ఀ-౿]/.test(text)) return "te";
  if (/[ऀ-ॿ]/.test(text)) return "hi";
  return "en";
}

export function speechCode(l: Lang): string {
  return LANGS.find((x) => x.code === l)?.speech ?? "en-IN";
}

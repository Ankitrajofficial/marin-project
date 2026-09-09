/**
 * All ORCA reasoning happens in IST wall-clock, because Open-Meteo is queried with
 * timezone=Asia/Kolkata and returns naive local ISO strings like "2026-09-10T06:00".
 * Representing time as those same naive strings makes window matching a string compare
 * and removes a whole class of timezone bugs from the demo.
 */

const IST_FMT = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Kolkata",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

/** Current IST wall clock as "YYYY-MM-DDTHH:mm". */
export function istNow(): string {
  const p = Object.fromEntries(IST_FMT.formatToParts(new Date()).map((x) => [x.type, x.value]));
  const hour = p.hour === "24" ? "00" : p.hour;
  return `${p.year}-${p.month}-${p.day}T${hour}:${p.minute}`;
}

/** Truncate a naive IST stamp to the top of the hour. */
export function istHour(stamp: string = istNow()): string {
  return stamp.slice(0, 13) + ":00";
}

/** Add hours to a naive IST stamp, returning a naive IST stamp. */
export function addHours(stamp: string, hours: number): string {
  const ms = Date.parse(stamp.length === 16 ? `${stamp}:00Z` : `${stamp}Z`);
  const d = new Date(ms + hours * 3_600_000);
  return d.toISOString().slice(0, 16);
}

/** Whole hours between two naive IST stamps (b - a). */
export function hoursBetween(a: string, b: string): number {
  const pa = Date.parse(`${a.slice(0, 16)}:00Z`);
  const pb = Date.parse(`${b.slice(0, 16)}:00Z`);
  return Math.round((pb - pa) / 3_600_000);
}

export function formatIST(stamp: string): string {
  const [date, time] = stamp.split("T");
  const [y, m, d] = date.split("-");
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${d} ${months[Number(m) - 1]} ${y}, ${time} IST`;
}

/** Human data-age string used on every provenance chip. */
export function ageLabel(seconds: number): string {
  if (seconds < 45) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)} min old`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h old`;
  return `${Math.round(seconds / 86400)} d old`;
}

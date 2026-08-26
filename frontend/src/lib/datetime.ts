/**
 * Display formatting for backend timestamps (#418).
 *
 * The backend emits naive ISO 8601 strings (no timezone designator) whose
 * wall-clock value is actually UTC — the app containers run with no `TZ`
 * override, so every `datetime.now()` / DB `now()` on that side returns UTC.
 * A naive string has no timezone of its own; treating it as JST and just
 * slicing out the date/time substrings (the previous approach) silently
 * displays the UTC value as if it were already JST, off by exactly the UTC
 * offset (+9h) from the real send time. This appends the missing `Z` before
 * parsing so `Date` interprets it as UTC, then formats the result in JST.
 */

const JST_DATE_TIME = new Intl.DateTimeFormat("ja-JP", {
  timeZone: "Asia/Tokyo",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

const HAS_TIMEZONE = /[Zz]|[+-]\d{2}:?\d{2}$/;

function parseAsUtcIfNaive(iso: string): Date {
  const withZone = HAS_TIMEZONE.test(iso) ? iso : `${iso}Z`;
  return new Date(withZone);
}

function partsToMap(date: Date): Record<string, string> {
  const parts: Record<string, string> = {};
  for (const part of JST_DATE_TIME.formatToParts(date)) {
    parts[part.type] = part.value;
  }
  return parts;
}

/** `iso` (naive-UTC or timezone-qualified) -> "YYYY-MM-DD HH:mm" in JST, or `null`. */
export function formatDateTimeJst(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const date = parseAsUtcIfNaive(iso);
  if (Number.isNaN(date.getTime())) return null;
  const p = partsToMap(date);
  return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}`;
}

/** `iso` (naive-UTC or timezone-qualified) -> "YYYY-MM-DD" in JST, or `null`. */
export function formatDateJst(iso: string | null | undefined): string | null {
  const full = formatDateTimeJst(iso);
  return full ? full.slice(0, 10) : null;
}

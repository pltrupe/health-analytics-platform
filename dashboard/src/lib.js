import { useEffect, useState } from "react";

const DATASETS = ["meta", "daily", "weekly", "workouts", "hr_zones", "step_profile"];

export function useDashboardData() {
  const [state, setState] = useState({ data: null, error: null });
  useEffect(() => {
    Promise.all(
      DATASETS.map((name) =>
        fetch(`/data/${name}.json`).then((r) => {
          if (!r.ok) throw new Error(`${name}.json: HTTP ${r.status}`);
          return r.json();
        })
      )
    )
      .then((parts) => setState({ data: Object.fromEntries(DATASETS.map((n, i) => [n, parts[i]])), error: null }))
      .catch((error) => setState({ data: null, error }));
  }, []);
  return state;
}

const TOKENS = [
  "ink", "ink-2", "muted", "grid", "axis", "surface", "page",
  "series-1", "series-2", "series-3", "series-4",
  "seq-0", "seq-1", "seq-2", "seq-3", "seq-4", "seq-5",
];

function readTokens() {
  const cs = getComputedStyle(document.documentElement);
  return Object.fromEntries(TOKENS.map((t) => [t, cs.getPropertyValue(`--${t}`).trim()]));
}

/** Resolved CSS color tokens (Recharts needs literal colors), refreshed on light/dark switch. */
export function useTheme() {
  const [theme, setTheme] = useState(readTokens);
  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const update = () => setTheme(readTokens());
    mq.addEventListener("change", update);
    return () => mq.removeEventListener("change", update);
  }, []);
  return theme;
}

export const fmt = {
  int: (v) => (v == null ? "–" : Math.round(v).toLocaleString("en-US")),
  dec: (v, d = 1) => (v == null ? "–" : Number(v).toFixed(d)),
  compact: (v) =>
    v == null ? "–" : Math.abs(v) >= 1000 ? `${(v / 1000).toFixed(v >= 10000 ? 0 : 1)}K` : `${Math.round(v)}`,
  hours: (h) => {
    if (h == null) return "–";
    const hh = Math.floor(h);
    const mm = Math.round((h - hh) * 60);
    return `${hh}h ${String(mm).padStart(2, "0")}m`;
  },
  pace: (p) => {
    if (p == null) return "–";
    const m = Math.floor(p);
    return `${m}:${String(Math.round((p - m) * 60)).padStart(2, "0")}/mi`;
  },
};

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export const parseDate = (s) => new Date(`${s.slice(0, 10)}T00:00:00`);
export const dayLabel = (s) => {
  const d = parseDate(s);
  return `${MONTHS[d.getMonth()]} ${d.getDate()}`;
};
export const monthLabel = (s) => {
  const d = parseDate(s);
  return `${MONTHS[d.getMonth()]} ’${String(d.getFullYear()).slice(2)}`;
};
export const longDate = (s) => {
  const d = parseDate(s);
  return `${MONTHS[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`;
};

export function weeklyAverage(rows, fields) {
  const weeks = new Map();
  for (const r of rows) {
    const d = parseDate(r.date);
    const monday = new Date(d);
    monday.setDate(d.getDate() - ((d.getDay() + 6) % 7));
    const key = monday.toISOString().slice(0, 10);
    if (!weeks.has(key)) weeks.set(key, { date: key, n: 0, ...Object.fromEntries(fields.map((f) => [f, 0])) });
    const w = weeks.get(key);
    if (r[fields[0]] == null) continue;
    w.n += 1;
    for (const f of fields) w[f] += r[f] ?? 0;
  }
  return [...weeks.values()]
    .filter((w) => w.n > 0)
    .map((w) => ({ ...w, ...Object.fromEntries(fields.map((f) => [f, w[f] / w.n])) }));
}

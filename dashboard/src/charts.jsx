import { useMemo, useState } from "react";
import {
  Area, Bar, BarChart, CartesianGrid, ComposedChart, Line,
  ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { dayLabel, fmt, longDate, monthLabel, weeklyAverage } from "./lib.js";

/* ---------- shared pieces ---------- */

function axisProps(theme) {
  return {
    tick: { fill: theme.muted, fontSize: 11 },
    tickLine: false,
    axisLine: { stroke: theme.axis },
  };
}

function Grid({ theme }) {
  return <CartesianGrid stroke={theme.grid} vertical={false} />;
}

export function Legend({ items }) {
  return (
    <ul className="legend">
      {items.map((it) => (
        <li key={it.label}>
          <span className={it.line ? "ln" : "sw"} style={{ background: it.color }} />
          {it.label}
        </li>
      ))}
    </ul>
  );
}

function Tip({ active, payload, title, rows }) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <div className="tip">
      <div className="t">{title(p)}</div>
      {rows(p).map(([label, value, color]) => (
        <div className="row" key={label}>
          <span className="k">
            {color && <span className="legend"><span className="sw" style={{ background: color }} /></span>}
            {label}
          </span>
          <b>{value}</b>
        </div>
      ))}
    </div>
  );
}

function xTicks(rows, key, maxTicks = 7) {
  if (rows.length <= maxTicks) return rows.map((r) => r[key]);
  const step = Math.ceil(rows.length / maxTicks);
  return rows.filter((_, i) => i % step === 0).map((r) => r[key]);
}

const longRange = (rows) => rows.length > 200;
const tickFor = (rows) => (longRange(rows) ? monthLabel : dayLabel);

/* ---------- Steps: daily bars + 7-day average ---------- */

export function StepsChart({ rows, theme }) {
  const thin = rows.length > 100;
  return (
    <div className="chart">
      <h3>Daily steps</h3>
      <p className="sub">De-duplicated across iPhone and Apple Watch, with the 7-day average.</p>
      <Legend items={[
        { label: "Daily steps", color: theme["seq-2"] },
        { label: "7-day average", color: theme["series-1"], line: true },
      ]} />
      <div className="plot">
        <ResponsiveContainer>
          <ComposedChart data={rows} margin={{ top: 6, right: 8, left: 0, bottom: 0 }} barCategoryGap={thin ? 0 : 2}>
            <Grid theme={theme} />
            <XAxis dataKey="date" ticks={xTicks(rows, "date")} tickFormatter={tickFor(rows)} {...axisProps(theme)} />
            <YAxis tickFormatter={fmt.compact} width={44} {...axisProps(theme)} axisLine={false} />
            <Tooltip
              cursor={{ fill: theme.grid }}
              content={<Tip title={(p) => longDate(p.date)} rows={(p) => [
                ["Steps", fmt.int(p.steps), theme["seq-2"]],
                ["7-day average", fmt.int(p.steps_7d), theme["series-1"]],
                ["Distance", `${fmt.dec(p.distance_mi, 1)} mi`],
              ]} />}
            />
            <Bar dataKey="steps" fill={theme["seq-2"]} maxBarSize={24} radius={thin ? 0 : [4, 4, 0, 0]} isAnimationActive={false} />
            <Line dataKey="steps_7d" stroke={theme["series-1"]} strokeWidth={2} dot={false} isAnimationActive={false}
                  activeDot={{ r: 4, stroke: theme.page, strokeWidth: 2 }} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

/* ---------- One metric trend (used for resting HR, HRV, weight, VO2) ---------- */

export function TrendChart({ rows, theme, title, sub, dataKey, rawKey, unit, digits = 0, domainPad = 2, color, refLines = [] }) {
  const c = color || theme["series-1"];
  return (
    <div className="chart">
      <h3>{title}</h3>
      <p className="sub">{sub}</p>
      <div className="plot" style={{ height: 220 }}>
        <ResponsiveContainer>
          <ComposedChart data={rows} margin={{ top: 6, right: 8, left: 0, bottom: 0 }}>
            <Grid theme={theme} />
            <XAxis dataKey="date" ticks={xTicks(rows, "date", 5)} tickFormatter={tickFor(rows)} {...axisProps(theme)} />
            <YAxis width={40} domain={[(m) => Math.floor(m - domainPad), (m) => Math.ceil(m + domainPad)]}
                   allowDecimals={false} {...axisProps(theme)} axisLine={false} />
            <Tooltip
              cursor={{ stroke: theme.axis }}
              content={<Tip title={(p) => longDate(p.date)} rows={(p) => [
                ...(rawKey ? [["That day", p[rawKey] == null ? "–" : `${fmt.dec(p[rawKey], digits)} ${unit}`]] : []),
                [rawKey ? "7-day average" : title, p[dataKey] == null ? "–" : `${fmt.dec(p[dataKey], digits)} ${unit}`, c],
              ]} />}
            />
            {refLines.map((r) => (
              <ReferenceLine key={r.x} x={r.x} stroke={theme.muted} strokeDasharray="0" label={{ value: r.label, fill: theme.muted, fontSize: 11, position: "insideTopLeft" }} />
            ))}
            <Area dataKey={dataKey} stroke="none" fill={c} fillOpacity={0.1} isAnimationActive={false} connectNulls />
            <Line dataKey={dataKey} stroke={c} strokeWidth={2} dot={false} isAnimationActive={false} connectNulls
                  activeDot={{ r: 4, stroke: theme.page, strokeWidth: 2 }} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

/* ---------- Sleep stages: stacked per night, weekly when the range is long ---------- */

const STAGES = [
  { key: "deep_min", label: "Deep", token: "series-1" },
  { key: "core_min", label: "Core", token: "series-3" },
  { key: "rem_min", label: "REM", token: "series-2" },
  { key: "awake_min", label: "Awake", token: "series-4" },
];

export function SleepChart({ rows, theme }) {
  const weekly = rows.length > 60;
  const data = useMemo(() => {
    const src = weekly ? weeklyAverage(rows, STAGES.map((s) => s.key)) : rows.filter((r) => r.deep_min != null);
    return src.map((r) => ({ ...r, ...Object.fromEntries(STAGES.map((s) => [s.key, (r[s.key] ?? 0) / 60])) }));
  }, [rows, weekly]);
  return (
    <div className="chart">
      <h3>Sleep stages</h3>
      <p className="sub">{weekly ? "Average hours per night, by week." : "Hours per night."} Stages from Apple Watch.</p>
      <Legend items={STAGES.map((s) => ({ label: s.label, color: theme[s.token] }))} />
      <div className="plot">
        <ResponsiveContainer>
          <BarChart data={data} margin={{ top: 6, right: 8, left: 0, bottom: 0 }} barCategoryGap={2}>
            <Grid theme={theme} />
            <XAxis dataKey="date" ticks={xTicks(data, "date")} tickFormatter={weekly && data.length > 40 ? monthLabel : dayLabel} {...axisProps(theme)} />
            <YAxis width={40} tickFormatter={(v) => `${v}h`} {...axisProps(theme)} axisLine={false} />
            <Tooltip
              cursor={{ fill: theme.grid }}
              content={<Tip title={(p) => (weekly ? `Week of ${longDate(p.date)}` : `Night ending ${longDate(p.date)}`)} rows={(p) => [
                ...STAGES.map((s) => [s.label, fmt.hours(p[s.key]), theme[s.token]]),
                ["Asleep", fmt.hours(p.deep_min + p.core_min + p.rem_min)],
              ]} />}
            />
            {STAGES.map((s, i) => (
              <Bar key={s.key} dataKey={s.key} stackId="s" fill={theme[s.token]} maxBarSize={24}
                   stroke={theme.page} strokeWidth={data.length > 40 ? 0 : 1}
                   radius={i === STAGES.length - 1 ? [4, 4, 0, 0] : 0} isAnimationActive={false} />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

/* ---------- Weekly running miles ---------- */

export function RunMilesChart({ weeks, theme, raceWeek }) {
  return (
    <div className="chart">
      <h3>Weekly running miles</h3>
      <p className="sub">Half-marathon build, race week marked.</p>
      <div className="plot">
        <ResponsiveContainer>
          <BarChart data={weeks} margin={{ top: 18, right: 8, left: 0, bottom: 0 }} barCategoryGap={2}>
            <Grid theme={theme} />
            <XAxis dataKey="week_start" ticks={xTicks(weeks, "week_start")} tickFormatter={weeks.length > 40 ? monthLabel : dayLabel} {...axisProps(theme)} />
            <YAxis width={32} {...axisProps(theme)} axisLine={false} />
            <Tooltip
              cursor={{ fill: theme.grid }}
              content={<Tip title={(p) => `Week of ${longDate(p.week_start)}`} rows={(p) => [
                ["Run miles", fmt.dec(p.run_mi, 1), theme["series-2"]],
                ["Workouts", p.workouts],
                ["Avg resting HR", `${fmt.dec(p.avg_resting_hr, 1)} bpm`],
              ]} />}
            />
            {raceWeek && weeks.some((w) => w.week_start === raceWeek) && (
              <ReferenceLine x={raceWeek} stroke={theme.muted} label={{ value: "Race", fill: theme.muted, fontSize: 11, position: "top" }} />
            )}
            <Bar dataKey="run_mi" fill={theme["series-2"]} maxBarSize={24} radius={[4, 4, 0, 0]} isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

/* ---------- Heart-rate zones per month (ordinal, one hue light -> dark) ---------- */

const ZONES = ["Z1 Recovery", "Z2 Endurance", "Z3 Tempo", "Z4 Threshold", "Z5 Max"];

export function ZonesChart({ zones, theme }) {
  const data = useMemo(() => {
    const m = new Map();
    for (const z of zones) {
      if (!m.has(z.month)) m.set(z.month, { month: z.month, ...Object.fromEntries(ZONES.map((k) => [k, 0])) });
      m.get(z.month)[z.zone] = z.minutes / 60;
    }
    return [...m.values()];
  }, [zones]);
  const colors = ZONES.map((_, i) => theme[`seq-${i + 1}`]);
  return (
    <div className="chart">
      <h3>Training intensity</h3>
      <p className="sub">Workout hours per month by heart-rate zone (% of max HR).</p>
      <Legend items={ZONES.map((z, i) => ({ label: z, color: colors[i] }))} />
      <div className="plot">
        <ResponsiveContainer>
          <BarChart data={data} margin={{ top: 6, right: 8, left: 0, bottom: 0 }} barCategoryGap={4}>
            <Grid theme={theme} />
            <XAxis dataKey="month" tickFormatter={monthLabel} interval="preserveStartEnd" {...axisProps(theme)} />
            <YAxis width={40} tickFormatter={(v) => `${v}h`} {...axisProps(theme)} axisLine={false} />
            <Tooltip
              cursor={{ fill: theme.grid }}
              content={<Tip title={(p) => monthLabel(p.month)} rows={(p) =>
                ZONES.map((z, i) => [z, fmt.hours(p[z]), colors[i]])} />}
            />
            {ZONES.map((z, i) => (
              <Bar key={z} dataKey={z} stackId="z" fill={colors[i]} stroke={theme.page} strokeWidth={1}
                   maxBarSize={28} radius={i === ZONES.length - 1 ? [4, 4, 0, 0] : 0} isAnimationActive={false} />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

/* ---------- Weekday x hour heatmap (plain grid, sequential) ---------- */

export function StepHeatmap({ profile, theme }) {
  const [hover, setHover] = useState(null);
  const hours = [...new Set(profile.map((p) => p.hour))].sort((a, b) => a - b);
  const days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const byKey = new Map(profile.map((p) => [`${p.weekday_num}-${p.hour}`, p.avg_steps]));
  const steps = ["seq-0", "seq-1", "seq-2", "seq-3", "seq-4", "seq-5"];
  // quantile bins: a few long-run hours would otherwise flatten every other cell into one color
  const sorted = profile.map((p) => p.avg_steps).filter((v) => v > 0).sort((a, b) => a - b);
  const cuts = [0.2, 0.4, 0.6, 0.8].map((q) => sorted[Math.floor(q * (sorted.length - 1))]);
  const color = (v) => (!v ? theme["seq-0"] : theme[steps[1 + cuts.filter((c) => v > c).length]]);
  const hourLabel = (h) => (h === 0 ? "12a" : h < 12 ? `${h}a` : h === 12 ? "12p" : `${h - 12}p`);
  return (
    <div className="chart">
      <h3>When the steps happen</h3>
      <p className="sub">Average steps by weekday and hour of day.</p>
      <div className="heat" style={{ "--cols": hours.length }} onMouseLeave={() => setHover(null)}>
        <span />
        {hours.map((h) => <span key={h} className="cl">{h % 3 === 0 ? hourLabel(h) : ""}</span>)}
        {days.map((d, di) => (
          <FragmentRow key={d}>
            <span className="rl">{d}</span>
            {hours.map((h) => {
              const v = byKey.get(`${di + 1}-${h}`) ?? 0;
              return (
                <span key={h} className="cell" style={{ background: color(v) }}
                      onMouseEnter={() => setHover({ d, h, v })}
                      aria-label={`${d} ${hourLabel(h)}: ${fmt.int(v)} steps`} role="img" />
              );
            })}
          </FragmentRow>
        ))}
      </div>
      <div className="heat-scale">
        <span>Fewer</span>
        <span className="bar">{steps.map((s) => <span key={s} style={{ background: theme[s] }} />)}</span>
        <span>More steps</span>
      </div>
      <div className="heat-tip">{hover ? `${hover.d} ${hourLabel(hover.h)}–${hourLabel((hover.h + 1) % 24)}: ${fmt.int(hover.v)} steps on average` : "Hover a cell for the value."}</div>
    </div>
  );
}

function FragmentRow({ children }) {
  return <>{children}</>;
}

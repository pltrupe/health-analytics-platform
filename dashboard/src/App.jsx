import { useMemo, useState } from "react";
import { RunMilesChart, SleepChart, StepHeatmap, StepsChart, TrendChart, ZonesChart } from "./charts.jsx";
import { fmt, longDate, useDashboardData, useTheme } from "./lib.js";

const REPO = "https://github.com/pltrupe/health-analytics-platform";
const RANGES = [
  { key: "30", label: "30D", days: 30 },
  { key: "90", label: "90D", days: 90 },
  { key: "182", label: "6M", days: 182 },
  { key: "all", label: "12M", days: Infinity },
];
const RACE_DAY = "2026-04-26";
const RACE_WEEK = "2026-04-20";

function Delta({ now, before, digits = 0, unit = "", upIsGood = true }) {
  if (now == null || before == null) return null;
  const diff = now - before;
  if (Math.abs(diff) < 10 ** -digits / 2) return <div className="delta">No change vs prior 30 days</div>;
  const up = diff > 0;
  const cls = up ? (upIsGood ? "up-good" : "up-bad") : upIsGood ? "down-bad" : "down-good";
  return (
    <div className="delta">
      <span className={cls}>
        {up ? "▲" : "▼"} {up ? "+" : "−"}{fmt.dec(Math.abs(diff), digits)}{unit}
      </span>{" "}
      vs prior 30 days
    </div>
  );
}

function Tiles({ meta }) {
  const k = meta.kpis;
  return (
    <div className="tiles">
      <div className="tile">
        <p className="label">Steps per day</p>
        <div className="value">{fmt.int(k.steps.last_30)}</div>
        <Delta now={k.steps.last_30} before={k.steps.prior_30} />
      </div>
      <div className="tile">
        <p className="label">Resting heart rate</p>
        <div className="value">{fmt.dec(k.resting_hr.last_30, 1)}<small>bpm</small></div>
        <Delta now={k.resting_hr.last_30} before={k.resting_hr.prior_30} digits={1} upIsGood={false} />
      </div>
      <div className="tile">
        <p className="label">Heart-rate variability</p>
        <div className="value">{fmt.dec(k.hrv_ms.last_30, 0)}<small>ms</small></div>
        <Delta now={k.hrv_ms.last_30} before={k.hrv_ms.prior_30} unit=" ms" />
      </div>
      <div className="tile">
        <p className="label">Sleep per night</p>
        <div className="value">{fmt.hours(k.sleep_hours.last_30)}</div>
        <Delta now={k.sleep_hours.last_30 * 60} before={k.sleep_hours.prior_30 * 60} unit=" min" />
      </div>
      <div className="tile">
        <p className="label">Miles run, all year</p>
        <div className="value">{fmt.int(meta.totals.run_miles)}</div>
        <div className="delta">{meta.totals.runs} runs · {meta.totals.workouts} workouts</div>
      </div>
    </div>
  );
}

function WorkoutTable({ workouts }) {
  const recent = workouts.slice(-12).reverse();
  return (
    <div className="table-scroll">
      <table className="data">
        <thead>
          <tr>
            <th>Date</th><th>Activity</th><th className="num">Duration</th><th className="num">Distance</th>
            <th className="num">Pace</th><th className="num">Avg HR</th><th className="num">Energy</th>
          </tr>
        </thead>
        <tbody>
          {recent.map((w) => (
            <tr key={w.workout_id}>
              <td>{longDate(w.workout_date)}</td>
              <td>{w.activity}</td>
              <td className="num">{fmt.int(w.duration_min)} min</td>
              <td className="num">{w.distance_mi ? `${fmt.dec(w.distance_mi, 2)} mi` : "–"}</td>
              <td className="num">{fmt.pace(w.pace_min_per_mi)}</td>
              <td className="num">{fmt.int(w.avg_hr)}</td>
              <td className="num">{fmt.int(w.energy_kcal)} kcal</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DailyTable({ rows }) {
  return (
    <details className="table-view">
      <summary>View the daily numbers as a table</summary>
      <div className="table-scroll">
        <table className="data">
          <thead>
            <tr>
              <th>Date</th><th className="num">Steps</th><th className="num">Resting HR</th><th className="num">HRV</th>
              <th className="num">Sleep</th><th className="num">Deep</th><th className="num">REM</th><th className="num">Weight</th>
            </tr>
          </thead>
          <tbody>
            {[...rows].reverse().map((r) => (
              <tr key={r.date}>
                <td>{longDate(r.date)}</td>
                <td className="num">{fmt.int(r.steps)}</td>
                <td className="num">{fmt.dec(r.resting_hr, 0)}</td>
                <td className="num">{fmt.dec(r.hrv_ms, 0)}</td>
                <td className="num">{fmt.hours(r.sleep_hours)}</td>
                <td className="num">{fmt.int(r.deep_min)}m</td>
                <td className="num">{fmt.int(r.rem_min)}m</td>
                <td className="num">{fmt.dec(r.weight_lb, 1)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

function Pipeline({ meta, daily }) {
  const naive = daily.reduce((s, r) => s + (r.naive_steps ?? 0), 0);
  const dedup = daily.reduce((s, r) => s + (r.steps ?? 0), 0);
  const over = dedup ? (naive / dedup - 1) * 100 : 0;
  const rc = meta.row_counts;
  return (
    <>
      <div className="pipeline">
        <div className="stage">
          <div className="num">01 / Source</div>
          <h4>Apple Health</h4>
          <p>The iPhone's <code>export.zip</code>: one XML file holding every sample from the phone, the Watch and a smart scale.</p>
        </div>
        <div className="stage">
          <div className="num">02 / Land</div>
          <h4>Volumes</h4>
          <p>The zip lands in a Unity Catalog Volume, gets unzipped to a <code>raw</code> Volume and archived.</p>
        </div>
        <div className="stage">
          <div className="num">03 / Bronze</div>
          <h4>COPY INTO</h4>
          <p>Idempotent <code>COPY INTO</code> loads <code>Record</code>, <code>Workout</code> and <code>ActivitySummary</code> XML into Delta, as-is. {fmt.int(rc.bronze_records)} records.</p>
        </div>
        <div className="stage">
          <div className="num">04 / Model</div>
          <h4>PySpark</h4>
          <p>Silver types, de-dupes and unit-normalizes; gold builds a gap-free daily spine, rolling averages and HR zones. Quality gates fail the job.</p>
        </div>
        <div className="stage">
          <div className="num">05 / Serve</div>
          <h4>React on Vercel</h4>
          <p>Gold tables export as JSON snapshots; this Recharts dashboard reads them as a static site.</p>
        </div>
      </div>
      <div className="callout">
        <div className="big">+{fmt.dec(over, 0)}%</div>
        <p>
          <b>Why the modeling matters.</b> The iPhone in a pocket and the Watch on a wrist both count the same walk.
          Summing every step sample would overstate this year by {fmt.int(naive - dedup)} steps. The gold layer
          keeps one device per hour (Watch first, then iPhone), which is how the Health app itself reconciles sources.
        </p>
      </div>
    </>
  );
}

export default function App() {
  const { data, error } = useDashboardData();
  const theme = useTheme();
  const [range, setRange] = useState("90");

  const view = useMemo(() => {
    if (!data) return null;
    const days = RANGES.find((r) => r.key === range).days;
    const daily = Number.isFinite(days) ? data.daily.slice(-days) : data.daily;
    const first = daily[0].date;
    const weeks = data.weekly.filter((w) => w.week_start >= first.slice(0, 10) || daily.length >= data.daily.length);
    return { daily, weeks, first };
  }, [data, range]);

  if (error) return <div className="wrap loading">Couldn't load dashboard data ({error.message}).</div>;
  if (!data) return <div className="wrap loading">Loading…</div>;
  const { meta } = data;
  const raceInView = view.daily.some((r) => r.date === RACE_DAY);

  return (
    <>
      <header className="hero">
        <div className="wrap">
          <p className="kicker">Personal portfolio · Health analytics platform</p>
          <h1>A year of Apple Health data, <em>modeled</em> end to end.</h1>
          <p className="lead">
            An Apple Health export lands in Databricks Volumes, loads to Delta with COPY INTO, is modeled with PySpark into
            bronze, silver and gold tables, and is served here through React and Recharts.
          </p>
          <ul className="stack">
            <li>Databricks</li><li>Unity Catalog Volumes</li><li>COPY INTO</li><li>PySpark</li><li>Delta Lake</li>
            <li>Asset Bundles</li><li>React</li><li>Recharts</li><li>Vercel</li>
          </ul>
          <div className="hero-links">
            <a className="btn" href={REPO}>View the code</a>
            <a className="btn" href="#pipeline">How it's built</a>
          </div>
          <p className="notice">
            The data is synthetic: a generated Apple Health export in Apple's exact format for a fictional runner training for a
            half marathon. No real person's health data is published here.
          </p>
        </div>
      </header>

      <div className="toolbar">
        <div className="wrap">
          <span className="range-label">
            {longDate(view.daily[0].date)} – {longDate(view.daily[view.daily.length - 1].date)}
          </span>
          <div className="seg" role="group" aria-label="Date range">
            {RANGES.map((r) => (
              <button key={r.key} aria-pressed={range === r.key} onClick={() => setRange(r.key)}>{r.label}</button>
            ))}
          </div>
        </div>
      </div>

      <main className="wrap">
        <section className="block">
          <div className="section-head">
            <h2>The last 30 days</h2>
            <p>Each figure compares the latest 30 days with the 30 days before. Green means the change is good news.</p>
          </div>
          <Tiles meta={meta} />
        </section>

        <section className="block">
          <div className="section-head">
            <h2>Activity</h2>
            <p>Movement through the week and across the year.</p>
          </div>
          <StepsChart rows={view.daily} theme={theme} />
          <div className="grid-2">
            <RunMilesChart weeks={view.weeks} theme={theme} raceWeek={RACE_WEEK} />
            <StepHeatmap profile={data.step_profile} theme={theme} />
          </div>
        </section>

        <section className="block">
          <div className="section-head">
            <h2>Heart and recovery</h2>
            <p>Resting heart rate falls and HRV rises as aerobic fitness builds. Each is on its own scale.</p>
          </div>
          <div className="grid-2">
            <TrendChart rows={view.daily} theme={theme} title="Resting heart rate" sub="7-day average, bpm. Lower is fitter."
                        dataKey="resting_hr_7d" rawKey="resting_hr" unit="bpm" digits={1}
                        refLines={raceInView ? [{ x: RACE_DAY, label: "Race" }] : []} />
            <TrendChart rows={view.daily} theme={theme} title="Heart-rate variability" sub="7-day average SDNN, ms. Higher means better recovered."
                        dataKey="hrv_ms_7d" rawKey="hrv_ms" unit="ms" digits={0} domainPad={4}
                        refLines={raceInView ? [{ x: RACE_DAY, label: "Race" }] : []} />
          </div>
          <ZonesChart zones={data.hr_zones} theme={theme} />
        </section>

        <section className="block">
          <div className="section-head">
            <h2>Sleep and body</h2>
            <p>Nights are credited to the morning they end.</p>
          </div>
          <SleepChart rows={view.daily} theme={theme} />
          <div className="grid-2">
            <TrendChart rows={view.daily} theme={theme} title="Weight" sub="Smart-scale weigh-ins, last reading carried forward, lb."
                        dataKey="weight_lb_filled" unit="lb" digits={1} />
            <TrendChart rows={view.daily} theme={theme} title="Cardio fitness (VO₂ max)" sub="Apple Watch estimate after outdoor runs, mL/kg/min."
                        dataKey="vo2_max_filled" unit="mL/kg/min" digits={1} domainPad={1} />
          </div>
          <DailyTable rows={view.daily} />
        </section>

        <section className="block">
          <div className="section-head">
            <h2>Recent workouts</h2>
            <p>
              Longest run {fmt.dec(meta.records.longest_run?.distance_mi, 1)} mi on {longDate(meta.records.longest_run?.workout_date ?? meta.last_date)};
              fastest pace over 3 mi {fmt.pace(meta.records.fastest_run?.pace_min_per_mi)}.
            </p>
          </div>
          <WorkoutTable workouts={data.workouts} />
        </section>

        <section className="block" id="pipeline">
          <div className="section-head">
            <h2>How it's built</h2>
            <p>One Databricks Asset Bundle deploys a five-task serverless job. Source on <a href={REPO}>GitHub</a>.</p>
          </div>
          <Pipeline meta={meta} daily={data.daily} />
        </section>
      </main>

      <footer>
        <div className="wrap">
          <span>Data refreshed from <code>{meta.source}</code> on {longDate(meta.generated_at)}.</span>
          <span>Built by Penny Trupe · <a href={REPO}>Source</a></span>
        </div>
      </footer>
    </>
  );
}

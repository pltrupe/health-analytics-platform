"""Generate a synthetic Apple Health export (export.zip) for one fictional person.

The output mirrors what the iPhone Health app produces from
Profile > Export All Health Data: a zip containing apple_health_export/export.xml
with <Record>, <Workout> and <ActivitySummary> elements, Apple's attribute names,
Apple's "yyyy-MM-dd HH:mm:ss -0400" timestamps, and the same quirks real exports
have (overlapping iPhone + Apple Watch step samples, nested MetadataEntry and
WorkoutStatistics children, sleep stages as category records).

No real person's data is involved. The story baked into the year:
  * a half-marathon training block that peaks in April (race on 2026-04-26)
  * resting heart rate drifting down and HRV up as fitness improves
  * a holiday slump in late December, a sick week in February
  * a high-step vacation in July

Usage:
    python generator/generate_export.py                # writes data/sample/export.zip
    python generator/generate_export.py --days 90 --seed 7 --out /tmp/export.zip

Standard library only.
"""

from __future__ import annotations

import argparse
import io
import math
import random
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import quoteattr

START = date(2025, 10, 1)
DAYS = 365
RACE_DAY = date(2026, 4, 26)
SICK_WEEK = (date(2026, 2, 9), date(2026, 2, 15))
HOLIDAYS = (date(2025, 12, 22), date(2026, 1, 2))
VACATION = (date(2026, 7, 11), date(2026, 7, 19))

WATCH = ("Demo's Apple Watch", "11.6")
PHONE = ("Demo's iPhone", "18.6")
SCALE = ("Withings", "6.12.0")
WATCH_DEVICE = (
    "&lt;&lt;HKDevice: 0x0&gt;, name:Apple Watch, manufacturer:Apple Inc., "
    "model:Watch, hardware:Watch7,2, software:11.6&gt;"
)

HK = "HKQuantityTypeIdentifier"
SLEEP = "HKCategoryTypeIdentifierSleepAnalysis"


# --------------------------------------------------------------------------- time


def _nth_sunday(year: int, month: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(6 - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def utc_offset(dt: datetime) -> str:
    """US Eastern offset with DST (2nd Sunday of March to 1st Sunday of November)."""
    dst_start = datetime.combine(_nth_sunday(dt.year, 3, 2), datetime.min.time()) + timedelta(hours=2)
    dst_end = datetime.combine(_nth_sunday(dt.year, 11, 1), datetime.min.time()) + timedelta(hours=2)
    return "-0400" if dst_start <= dt < dst_end else "-0500"


def ts(dt: datetime) -> str:
    return f"{dt:%Y-%m-%d %H:%M:%S} {utc_offset(dt)}"


def in_range(d: date, span: tuple[date, date]) -> bool:
    return span[0] <= d <= span[1]


# ------------------------------------------------------------------------ writing


@dataclass
class Writer:
    buf: io.StringIO
    rng: random.Random
    count: int = 0

    def record(self, type_: str, source: tuple[str, str], unit: str | None, start: datetime,
               end: datetime, value, metadata: dict | None = None, device: bool = False) -> None:
        created = end + timedelta(seconds=self.rng.randint(5, 600))
        attrs = [("type", type_), ("sourceName", source[0]), ("sourceVersion", source[1])]
        if device:
            attrs.append(("device", None))  # placeholder, written raw below
        if unit is not None:
            attrs.append(("unit", unit))
        attrs += [("creationDate", ts(created)), ("startDate", ts(start)), ("endDate", ts(end)),
                  ("value", str(value))]
        parts = []
        for k, v in attrs:
            parts.append(f'{k}="{WATCH_DEVICE}"' if k == "device" else f"{k}={quoteattr(v)}")
        line = " <Record " + " ".join(parts)
        if metadata:
            self.buf.write(line + ">\n")
            for k, v in metadata.items():
                self.buf.write(f"  <MetadataEntry key={quoteattr(k)} value={quoteattr(str(v))}/>\n")
            self.buf.write(" </Record>\n")
        else:
            self.buf.write(line + "/>\n")
        self.count += 1

    def workout(self, activity: str, start: datetime, minutes: float, energy: float,
                distance: float | None, hr: tuple[float, float, float], indoor: bool) -> None:
        end = start + timedelta(minutes=minutes)
        created = end + timedelta(seconds=self.rng.randint(5, 120))
        w = self.buf.write
        w(f' <Workout workoutActivityType="HKWorkoutActivityType{activity}" duration="{minutes:.4f}" '
          f'durationUnit="min" sourceName={quoteattr(WATCH[0])} sourceVersion="{WATCH[1]}" '
          f'device="{WATCH_DEVICE}" creationDate="{ts(created)}" startDate="{ts(start)}" endDate="{ts(end)}">\n')
        w(f'  <MetadataEntry key="HKIndoorWorkout" value="{1 if indoor else 0}"/>\n')
        w(f'  <MetadataEntry key="HKTimeZone" value="America/New_York"/>\n')
        w(f'  <WorkoutStatistics type="{HK}ActiveEnergyBurned" startDate="{ts(start)}" endDate="{ts(end)}" '
          f'sum="{energy:.3f}" unit="Cal"/>\n')
        if distance is not None:
            w(f'  <WorkoutStatistics type="{HK}DistanceWalkingRunning" startDate="{ts(start)}" '
              f'endDate="{ts(end)}" sum="{distance:.4f}" unit="mi"/>\n')
        w(f'  <WorkoutStatistics type="{HK}HeartRate" startDate="{ts(start)}" endDate="{ts(end)}" '
          f'average="{hr[0]:.2f}" minimum="{hr[1]:.0f}" maximum="{hr[2]:.0f}" unit="count/min"/>\n')
        w(" </Workout>\n")
        self.count += 1

    def activity_summary(self, d: date, energy: float, exercise: int, stand: int) -> None:
        self.buf.write(
            f' <ActivitySummary dateComponents="{d:%Y-%m-%d}" activeEnergyBurned="{energy:.3f}" '
            f'activeEnergyBurnedGoal="520" activeEnergyBurnedUnit="Cal" appleMoveTime="0" '
            f'appleMoveTimeGoal="0" appleExerciseTime="{exercise}" appleExerciseTimeGoal="30" '
            f'appleStandHours="{stand}" appleStandHoursGoal="12"/>\n')
        self.count += 1


# ------------------------------------------------------------------- the person


def fitness(d: date) -> float:
    """0..1 training progress: builds Oct to the April race, eases, then maintains."""
    t = (d - START).days
    race = (RACE_DAY - START).days
    if t <= race:
        f = t / race
    else:
        f = 1.0 - min(0.25, (t - race) / 120)
    if d >= HOLIDAYS[0] and d <= HOLIDAYS[1] + timedelta(days=5):
        f -= 0.08
    if in_range(d, SICK_WEEK) or in_range(d, (SICK_WEEK[1], SICK_WEEK[1] + timedelta(days=7))):
        f -= 0.06
    return max(0.0, min(1.0, f))


def planned_workouts(d: date, rng: random.Random) -> list[tuple[str, int, float]]:
    """(activity, start_hour, minutes) for the day."""
    if in_range(d, SICK_WEEK):
        return []
    if d == RACE_DAY:
        return [("Running", 7, 118.0)]
    holiday = in_range(d, HOLIDAYS)
    weeks_to_race = (RACE_DAY - d).days / 7
    long_run = 50 + 70 * fitness(d) if weeks_to_race > 1 else 40
    dow = d.weekday()
    plan: list[tuple[str, int, float]] = []
    if dow == 0 and not holiday:
        plan.append(("TraditionalStrengthTraining", 18, rng.uniform(35, 50)))
    if dow == 1:
        plan.append(("Running", 6, rng.uniform(32, 45)))
    if dow == 2 and not holiday:
        plan.append(("TraditionalStrengthTraining", 18, rng.uniform(35, 50)))
    if dow == 3:
        plan.append(("Running", 6, rng.uniform(35, 50)))
    if dow == 5:
        plan.append(("Running", 8, long_run * rng.uniform(0.92, 1.05)))
    if dow == 6:
        plan.append(("Yoga", 9, rng.uniform(25, 45)))
    if dow == 4 and rng.random() < 0.35:
        plan.append(("Cycling", 17, rng.uniform(40, 70)))
    if in_range(d, VACATION):
        plan = [("Hiking", 9, rng.uniform(150, 240))] + ([("Running", 7, 40.0)] if dow in (1, 3) else [])
    if holiday:
        plan = [p for p in plan if rng.random() < 0.45]
    # life happens
    return [p for p in plan if rng.random() > 0.08]


def build(days: int, seed: int) -> tuple[str, int]:
    rng = random.Random(seed)
    buf = io.StringIO()
    out = Writer(buf, rng)
    end_date = START + timedelta(days=days - 1)
    export_dt = datetime.combine(end_date + timedelta(days=1), datetime.min.time()) + timedelta(hours=9, minutes=14)

    buf.write('<?xml version="1.0" encoding="UTF-8"?>\n')
    buf.write("<!DOCTYPE HealthData [\n<!ELEMENT HealthData (ExportDate,Me,(Record|Workout|ActivitySummary)*)>\n"
              "<!ATTLIST HealthData locale CDATA #REQUIRED>\n]>\n")
    buf.write('<HealthData locale="en_US">\n')
    buf.write(f' <ExportDate value="{ts(export_dt)}"/>\n')
    buf.write(' <Me HKCharacteristicTypeIdentifierDateOfBirth="1988-05-14" '
              'HKCharacteristicTypeIdentifierBiologicalSex="HKBiologicalSexFemale" '
              'HKCharacteristicTypeIdentifierBloodType="HKBloodTypeNotSet" '
              'HKCharacteristicTypeIdentifierFitzpatrickSkinType="HKFitzpatrickSkinTypeNotSet" '
              'HKCharacteristicTypeIdentifierCardioFitnessMedicationsUse="None"/>\n')

    weight = 168.0
    for i in range(days):
        d = START + timedelta(days=i)
        f = fitness(d)
        sick = in_range(d, SICK_WEEK)
        weekend = d.weekday() >= 5
        midnight = datetime.combine(d, datetime.min.time())

        # ---- sleep: the night that ENDS this morning
        bed_hour = 22.6 + (0.9 if weekend or in_range(d, HOLIDAYS) else 0) + rng.gauss(0, 0.35)
        sleep_need = 7.1 + (0.6 if sick else 0) + (0.4 if weekend else 0) - (0.5 if in_range(d, HOLIDAYS) else 0)
        sleep_hours = max(4.8, rng.gauss(sleep_need, 0.45))
        bed = midnight - timedelta(hours=24 - bed_hour)
        latency = timedelta(minutes=rng.uniform(6, 22))
        wake = bed + latency + timedelta(hours=sleep_hours)
        out.record(SLEEP, PHONE, None, bed, wake + timedelta(minutes=rng.uniform(3, 12)),
                   "HKCategoryValueSleepAnalysisInBed")
        cursor = bed + latency
        cycle = 0
        while cursor < wake:
            deep_share = max(0.05, 0.32 - 0.07 * cycle) * (0.9 + 0.25 * f)
            rem_share = min(0.38, 0.12 + 0.06 * cycle)
            length = timedelta(minutes=rng.uniform(85, 100))
            for stage, share in (("AsleepCore", 1 - deep_share - rem_share - 0.04), ("AsleepDeep", deep_share),
                                 ("AsleepREM", rem_share), ("Awake", 0.04)):
                if share <= 0:
                    continue
                seg_end = min(wake, cursor + length * share * rng.uniform(0.85, 1.15))
                if seg_end <= cursor:
                    break
                out.record(SLEEP, WATCH, None, cursor, seg_end, f"HKCategoryValueSleepAnalysis{stage}")
                cursor = seg_end
            cycle += 1

        # ---- workouts planned for today
        workouts = planned_workouts(d, rng)
        workout_minutes_by_hour: dict[int, float] = {}
        workout_steps_by_hour: dict[int, int] = {}
        workout_energy = 0.0
        exercise_minutes = 0
        for activity, hour, minutes in workouts:
            start = midnight + timedelta(hours=hour, minutes=rng.randint(0, 25))
            if activity in ("Running",):
                pace = 10.6 - 1.4 * f + rng.gauss(0, 0.25)  # min/mi
                if d == RACE_DAY:
                    pace = 8.95
                distance = minutes / pace
                avg_hr = 156 - 8 * f + rng.gauss(0, 3)
                energy = distance * 108
                spm = 165
            elif activity == "Hiking":
                distance = minutes / 24.0
                avg_hr, energy, spm = 118 + rng.gauss(0, 4), minutes * 7.2, 95
            elif activity == "Cycling":
                distance, avg_hr, energy, spm = None, 138 + rng.gauss(0, 4), minutes * 9.1, 0
            elif activity == "Yoga":
                distance, avg_hr, energy, spm = None, 96 + rng.gauss(0, 4), minutes * 3.4, 0
            else:
                distance, avg_hr, energy, spm = None, 118 + rng.gauss(0, 5), minutes * 5.8, 0
            out.workout(activity, start, minutes, energy, distance,
                        (avg_hr, avg_hr - rng.uniform(25, 40), avg_hr + rng.uniform(14, 26)),
                        indoor=activity in ("TraditionalStrengthTraining", "Yoga"))
            # heart rate every minute during the workout
            t = start
            while t < start + timedelta(minutes=minutes):
                warm = min(1.0, (t - start).total_seconds() / 480)
                bpm = (avg_hr - 22) + 22 * warm + rng.gauss(0, 4)
                out.record(f"{HK}HeartRate", WATCH, "count/min", t, t, round(bpm),
                           {"HKMetadataKeyHeartRateMotionContext": 2}, device=True)
                t += timedelta(seconds=rng.randint(55, 65))
            workout_energy += energy
            exercise_minutes += int(minutes if activity != "Yoga" else minutes * 0.3)
            remaining = minutes
            h = start.hour
            first = 60 - start.minute
            while remaining > 0:
                chunk = min(remaining, first if h == start.hour else 60)
                workout_minutes_by_hour[h] = workout_minutes_by_hour.get(h, 0) + chunk
                workout_steps_by_hour[h] = workout_steps_by_hour.get(h, 0) + int(chunk * spm)
                remaining -= chunk
                h += 1
            if activity == "Running" and rng.random() < 0.4:
                vo2 = 38.5 + 7.5 * f + rng.gauss(0, 0.4)
                out.record(f"{HK}VO2Max", WATCH, "mL/min·kg", start + timedelta(minutes=minutes),
                           start + timedelta(minutes=minutes), round(vo2, 2),
                           {"HKVO2MaxTestType": 2})

        # ---- hourly steps / distance / energy / exercise while awake
        base_steps = 7400 + (1800 if weekend else 0) + 900 * f
        if sick:
            base_steps = 2600
        if in_range(d, HOLIDAYS):
            base_steps *= 0.72
        if in_range(d, VACATION):
            base_steps = 13500
        base_steps *= rng.uniform(0.8, 1.2)
        wake_hour = max(5, wake.hour)
        hours = list(range(wake_hour, 23))
        weights = [1.0 + (0.8 if h in (8, 12, 17, 18) else 0) + (0.3 if 10 <= h <= 16 else 0) for h in hours]
        total_w = sum(weights)
        stand_hours = 0
        for h, wgt in zip(hours, weights):
            steps = int(base_steps * wgt / total_w * rng.uniform(0.5, 1.5)) + workout_steps_by_hour.get(h, 0)
            if steps < 25:
                continue
            start = midnight + timedelta(hours=h, minutes=rng.randint(0, 20))
            end = start + timedelta(minutes=rng.randint(20, 39))
            watch_worn = rng.random() < 0.93 and not (sick and rng.random() < 0.5)
            phone_carried = rng.random() < 0.7 and workout_minutes_by_hour.get(h, 0) == 0
            if watch_worn:
                out.record(f"{HK}StepCount", WATCH, "count", start, end, steps, device=True)
                out.record(f"{HK}DistanceWalkingRunning", WATCH, "mi", start, end,
                           round(steps * rng.uniform(0.00041, 0.00047), 5), device=True)
                out.record(f"{HK}ActiveEnergyBurned", WATCH, "Cal", start, end,
                           round(steps * 0.038 + rng.uniform(1, 6), 3), device=True)
                ex = int(workout_minutes_by_hour.get(h, 0) + (rng.choice([0, 0, 0, 2, 4, 6]) if steps > 900 else 0))
                if ex:
                    out.record(f"{HK}AppleExerciseTime", WATCH, "min", start, end, min(60, ex))
                if steps > 250:
                    stand_hours += 1
            if phone_carried:
                phone_steps = int(steps * rng.uniform(0.75, 0.95))
                out.record(f"{HK}StepCount", PHONE, "count", start + timedelta(minutes=2), end, phone_steps)
                out.record(f"{HK}DistanceWalkingRunning", PHONE, "mi", start + timedelta(minutes=2), end,
                           round(phone_steps * 0.00044, 5))

        # ---- resting heart rate, HRV, background heart rate
        rhr = 64 - 7 * f + (7 if sick else 0) + rng.gauss(0, 1.1)
        out.record(f"{HK}RestingHeartRate", WATCH, "count/min", wake, wake + timedelta(hours=15),
                   round(rhr))
        for k in range(3):
            hrv = 38 + 18 * f - (12 if sick else 0) + rng.gauss(0, 5)
            t = bed + timedelta(hours=1.5 + 2 * k)
            out.record(f"{HK}HeartRateVariabilitySDNN", WATCH, "ms", t, t + timedelta(minutes=1),
                       round(max(12.0, hrv), 3))
        t = midnight + timedelta(hours=wake_hour, minutes=rng.randint(0, 14))
        while t.hour < 23 and t.date() == d:
            if not any(midnight + timedelta(hours=hh) <= t < midnight + timedelta(hours=hh + 1)
                       for hh in workout_minutes_by_hour):
                bpm = rhr + 14 + rng.gauss(0, 8) + (10 if 17 <= t.hour <= 18 else 0)
                out.record(f"{HK}HeartRate", WATCH, "count/min", t, t, round(max(rhr, bpm)),
                           {"HKMetadataKeyHeartRateMotionContext": rng.choice([0, 1, 1])}, device=True)
            t += timedelta(minutes=rng.randint(11, 19))

        # ---- weigh-ins (most mornings, from the smart scale)
        if rng.random() < 0.55:
            weight += -0.035 + rng.gauss(0, 0.05) + (0.12 if in_range(d, HOLIDAYS) else 0)
            weight = max(156.0, weight)
            t = wake + timedelta(minutes=rng.randint(5, 25))
            out.record(f"{HK}BodyMass", SCALE, "lb", t, t, round(weight + rng.gauss(0, 0.6), 1))

        active = base_steps * 0.038 + workout_energy * 0.85 + rng.uniform(20, 60)
        out.activity_summary(d, active, exercise_minutes, min(16, stand_hours))

    buf.write("</HealthData>\n")
    return buf.getvalue(), out.count


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--days", type=int, default=DAYS)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "sample" / "export.zip")
    p.add_argument("--xml", action="store_true", help="also write the raw export.xml beside the zip")
    args = p.parse_args()

    xml, n = build(args.days, args.seed)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.out, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("apple_health_export/export.xml", xml)
    if args.xml:
        args.out.with_name("export.xml").write_text(xml, encoding="utf-8")
    print(f"wrote {args.out} ({n:,} elements, {len(xml) / 1e6:.1f} MB uncompressed)")


if __name__ == "__main__":
    main()

"use client";

const fmt = (iso: string) =>
  new Date(iso).toLocaleString(undefined, {
    weekday: "short", hour: "2-digit", minute: "2-digit",
    timeZone: "UTC", hour12: false,
  }) + " UTC";

export default function TimeSlider({
  times, index, onChange, servedTime,
}: {
  times: string[]; index: number;
  onChange: (i: number) => void;
  servedTime: string | null;
}) {
  if (times.length === 0) return null;
  // The API snaps requests to the nearest computed hour. If what it served
  // differs from what we asked for, say so rather than mislabelling the map.
  const mismatch = servedTime && times[index] && servedTime !== times[index];
  return (
    <div className="slider card">
      <div className="row">
        <span className="t">{fmt(times[index])}</span>
        <span className="muted">
          step {index + 1} of {times.length}
          {mismatch ? ` · showing ${fmt(servedTime!)}` : ""}
        </span>
      </div>
      <input
        type="range" min={0} max={times.length - 1} value={index}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </div>
  );
}

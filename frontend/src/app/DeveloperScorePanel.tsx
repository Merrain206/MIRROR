"use client";

import { useState } from "react";

const API = process.env.NEXT_PUBLIC_MIRROR_API_URL ?? "http://127.0.0.1:8000";
const LABELS: Record<string, string> = {
  happiness: "HAPPINESS", trust: "TRUST", connection: "CONNECTION",
  agency: "AGENCY", stability: "STABILITY", truth: "TRUTH",
  dependence: "DEPENDENCE",
};
type Metric = { value: number; components: Record<string, number>;
  character_contributions: Record<string, number>; evidence: string[] };
type Snapshot = { day: number; formula_version: string;
  metrics: Record<string, Metric>; change_since_previous: Record<string, number | null> };

export default function DeveloperScorePanel({ gameId }: { gameId: string }) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState("");
  async function calculate() {
    setError("");
    try {
      const response = await fetch(`${API}/debug/games/${gameId}/scores`, { method: "POST" });
      if (!response.ok) throw new Error(`评分失败 (${response.status})`);
      setSnapshot(await response.json() as Snapshot);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "评分失败");
    }
  }
  return <section className="mt-6 rounded border border-amber-700 bg-slate-900 p-4">
    <div className="flex items-center justify-between gap-3">
      <h2 className="font-semibold text-amber-300">开发评分面板</h2>
      <button onClick={() => void calculate()} className="rounded bg-amber-300 px-3 py-1 text-slate-950">计算当前快照</button>
    </div>
    {error && <p role="alert" className="mt-2 text-rose-300">{error}</p>}
    {snapshot && <>
      <p className="mt-2 text-xs text-slate-400">Day {snapshot.day} · Formula {snapshot.formula_version}</p>
      <div className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {Object.entries(LABELS).map(([key, label]) => {
          const metric = snapshot.metrics[key];
          return <details key={key} className="rounded border border-slate-700 p-3">
            <summary className="cursor-pointer font-medium">{label} {metric.value}
              <span className="ml-2 text-xs text-slate-400">{snapshot.change_since_previous[key] ?? "—"}</span>
            </summary>
            <dl className="mt-2 text-xs text-slate-300">
              {Object.entries(metric.components).map(([part, value]) => <div key={part} className="flex justify-between gap-2">
                <dt>{part}</dt><dd>{value}</dd>
              </div>)}
            </dl>
            <p className="mt-2 text-xs text-slate-400">角色：{Object.entries(metric.character_contributions).map(([name, value]) => `${name} ${value}`).join(" · ")}</p>
            <p className="mt-2 break-all text-xs text-slate-500">证据事件：{metric.evidence.length ? metric.evidence.join(", ") : "静态初始状态"}</p>
          </details>;
        })}
      </div>
    </>}
  </section>;
}

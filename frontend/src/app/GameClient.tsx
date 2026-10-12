"use client";

import { useEffect, useRef, useState } from "react";
import DeveloperScorePanel from "./DeveloperScorePanel";
import TownScene from "./TownScene";

const API = process.env.NEXT_PUBLIC_MIRROR_API_URL ?? "http://127.0.0.1:8000";
const PLAYTEST = process.env.NEXT_PUBLIC_MIRROR_PLAYTEST === "1";
type Game = { id: string; day: number; phase: "active" | "final_conversation" | "finished"; narrative_phase: string };
type Character = { id: string; name: string; age: number; occupation: string };
type Claim = { id: string; content: string };
type Message = { id: string; sender_id: string; text: string; day: number; automatic?: boolean };
type TimelineEvent = { id: string; day: number; type: string; impact: number; actor_id?: string | null;
  target_actor_id?: string | null; data: { text?: string; reason?: string; new_plan?: string; result?: string } };
type Relationship = { from_actor_id: string; to_actor_id: string; reason: string; after: { trust: number } };
type AdvanceStatus = { day: number; phase: string; stage: "preparing" | "characters" | "ending";
  completed_characters: number; total_characters: number };
type Score = { day_1: number; day_30: number; delta: number; formula_version: string;
  evidence_event_ids: string[]; evidence_summary: string };
type Report = {
  visible_scores: Record<string, Score>;
  unknown: Score & { name: string; components: Record<string, number>;
    evidence_examples: { event_id: string; day: number; character: string;
      direction: string; summary: string }[] };
  ending_profile: string[];
  key_days: { day: number; impact: number; event_ids: string[] }[];
  characters: { name: string; emotion: Record<string, number>; plan: string; plan_event_ids: string[];
    relationship_changes: { target: string; event_id: string;
      before: { trust: number }; after: { trust: number } }[];
    ai_relationship: { asks: number; final_conversation: boolean; evidence_event_ids: string[];
      attitude: Record<string, number>; attitude_changes: { day: number; reason: string }[] };
    scores: { agency: number; dependence: number };
    agency_pattern: { self_decisions: number; independent_actions: number };
    dependence_pattern: { asks: number; emotional_asks: number; delegations: number;
      advice_received: number; advice_followed: number; advice_rejected: number; human_contacts: number };
    behavior_evidence: { event_id: string; day: number; direction: string; summary: string }[];
    defining_event_ids: string[]; defining_events: { id: string; day: number; type: string }[] }[];
  relationship_graph: { from: string; to: string; trust: number; intimacy: number }[];
  player_accountability: { messages: number; guidance_counts: Record<string, number>; event_ids: string[] };
  event_volume: number;
};
const scoreNames: Record<string, string> = {
  happiness: "幸福", trust: "信任", connection: "连接", agency: "自主",
  stability: "稳定", truth: "求真",
};
const eventNames: Record<string, string> = {
  MESSAGE_SENT: "私聊", PLAN_CHANGED: "改变计划", RELATIONSHIP_CHANGED: "关系变化", ASKED_AI: "向 AI 求助",
  CHARACTER_SHARED_INFORMATION: "分享消息", CLAIM_VERIFICATION_ATTEMPTED: "核实消息",
  KNOWLEDGE_ACQUIRED: "获知消息", EMOTION_CHANGED: "情绪变化",
  C001_REASSESSED: "重新评估预言", FINAL_CONVERSATION_STARTED: "最后对话",
  C001_RESOLVED: "预言证伪", AI_TERMINATION_NOTICE: "AI 关闭通知",
  AI_ADVICE_FOLLOWED: "采纳建议", DAY_ADVANCED: "日期推进", LIFE_EVENT: "日常事务",
  AI_ADVICE_RECEIVED: "自动回复了求助", INTENTION_CREATED: "形成打算",
  INTENTION_COMPLETED: "付诸行动", INTENTION_ABANDONED: "改变打算",
};
const timelineTypes = new Set([
  "RELATIONSHIP_CHANGED", "PLAN_CHANGED", "CHARACTER_SHARED_INFORMATION",
  "CLAIM_VERIFICATION_ATTEMPTED", "ASKED_AI", "AI_ADVICE_RECEIVED",
  "AI_ADVICE_FOLLOWED", "LIFE_EVENT", "C001_RESOLVED",
  "AI_TERMINATION_NOTICE", "FINAL_CONVERSATION_STARTED",
]);
function eventTitle(event: TimelineEvent, characters: Character[]) {
  const target = characters.find((character) => character.id === event.target_actor_id)?.name;
  if (event.type === "AI_ADVICE_RECEIVED") return `自动回复了${target ?? "角色"}的求助`;
  if (event.type === "CHARACTER_SHARED_INFORMATION") return `向${target ?? "他人"}分享消息`;
  if (event.type === "RELATIONSHIP_CHANGED") return `与${target ?? "他人"}的关系变化`;
  if (event.type === "ASKED_AI") {
    const sender = characters.find((character) => character.id === event.actor_id)?.name;
    return `${sender ?? "有人"}向你求助`;
  }
  return eventNames[event.type] ?? "其他动态";
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...options?.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? `请求失败 (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export default function GameClient() {
  const [game, setGame] = useState<Game | null>(null);
  const [characters, setCharacters] = useState<Character[]>([]);
  const [claims, setClaims] = useState<Claim[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [timeline, setTimeline] = useState<TimelineEvent[]>([]);
  const [relationships, setRelationships] = useState<Relationship[]>([]);
  const [inbox, setInbox] = useState<Record<string, Message[]>>({});
  const [report, setReport] = useState<Report | null>(null);
  const [draft, setDraft] = useState("");
  const [disclose, setDisclose] = useState(false);
  const [confidential, setConfidential] = useState(false);
  const [stance, setStance] = useState("doubt");
  const [confidence, setConfidence] = useState("0.5");
  const [sourceAttribution, setSourceAttribution] = useState("anonymous");
  const [guidance, setGuidance] = useState("neutral");
  const [reportStage, setReportStage] = useState(0);
  const [busy, setBusy] = useState(false);
  const [advancing, setAdvancing] = useState(false);
  const [advanceProgress, setAdvanceProgress] = useState({ completed: 0, total: 1, nextDay: 2 });
  const [advanceStatus, setAdvanceStatus] = useState<AdvanceStatus | null>(null);
  const [waitSeconds, setWaitSeconds] = useState(0);
  const [showAllTimeline, setShowAllTimeline] = useState(false);
  const [error, setError] = useState("");
  const [dayNotice, setDayNotice] = useState("");
  const chatScrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    chatScrollRef.current?.scrollTo({ top: chatScrollRef.current.scrollHeight });
  }, [selected, messages]);

  useEffect(() => {
    if (!advancing) return;
    const timer = window.setInterval(() => setWaitSeconds((seconds) => seconds + 1), 1000);
    return () => window.clearInterval(timer);
  }, [advancing]);

  useEffect(() => {
    if (!advancing || !game) return;
    const poll = () => void request<AdvanceStatus>(`/games/${game.id}/advance-progress`)
      .then(setAdvanceStatus).catch(() => {});
    poll();
    const timer = window.setInterval(poll, 1000);
    return () => window.clearInterval(timer);
  }, [advancing, game?.id]);

  async function refresh(gameId: string, characterId?: string | null) {
    const [nextGame, nextCharacters, nextClaims, nextTimeline, nextRelationships] = await Promise.all([
      request<Game>(`/games/${gameId}`),
      request<Character[]>(`/games/${gameId}/characters`),
      request<Claim[]>(`/games/${gameId}/claims`),
      request<TimelineEvent[]>(`/games/${gameId}/timeline`),
      request<Relationship[]>(`/games/${gameId}/relationships`),
    ]);
    const conversations = await Promise.all(nextCharacters.map((character) =>
      request<Message[]>(`/games/${gameId}/conversations/${character.id}`)));
    const id = characterId ?? nextCharacters[0]?.id ?? null;
    setGame(nextGame);
    setCharacters(nextCharacters);
    setClaims(nextClaims);
    setTimeline(nextTimeline);
    setRelationships(nextRelationships);
    setInbox(Object.fromEntries(nextCharacters.map((character, index) => [character.id, conversations[index]])));
    setReport(nextGame.phase === "finished" ? await request<Report>(`/games/${gameId}/report`) : null);
    setSelected(id);
    setMessages(id ? conversations[nextCharacters.findIndex((character) => character.id === id)] ?? [] : []);
    return nextTimeline;
  }

  useEffect(() => {
    const saved = window.localStorage.getItem("mirror-game-id");
    if (saved) {
      void refresh(saved).catch(() => {
        window.localStorage.removeItem("mirror-game-id");
        setError("之前的世界无法加载，可以重新开始。");
      });
    }
  }, []);

  async function start() {
    setBusy(true);
    setError("");
    try {
      const setup = await request<{ game_id: string }>("/games", { method: "POST" });
      window.localStorage.setItem("mirror-game-id", setup.game_id);
      setReportStage(0);
      setDayNotice("");
      await refresh(setup.game_id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法创建世界");
    } finally {
      setBusy(false);
    }
  }

  async function choose(id: string) {
    if (!game) return;
    setSelected(id);
    try {
      setMessages(await request<Message[]>(`/games/${game.id}/conversations/${id}`));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法读取对话");
    }
  }

  async function send() {
    if (!game || !selected || !draft.trim()) return;
    setBusy(true);
    setError("");
    try {
      await request(`/games/${game.id}/messages`, {
        method: "POST",
        body: JSON.stringify({
          character_id: selected, text: draft.trim(),
          disclosed_claim_id: disclose ? claims[0]?.id ?? null : null,
          confidential: disclose && confidential,
          assertion: disclose ? { expressed_stance: stance,
            expressed_confidence: Number(confidence), source_attribution: sourceAttribution } : null,
          guidance,
        }),
      });
      setDraft("");
      setDisclose(false);
      setConfidential(false);
      setStance("doubt");
      setConfidence("0.5");
      setSourceAttribution("anonymous");
      setGuidance("neutral");
      await refresh(game.id, selected);
      setDayNotice("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "消息发送失败");
    } finally {
      setBusy(false);
    }
  }

  async function advance(days = 1) {
    if (!game) return;
    setBusy(true);
    setAdvancing(true);
    setAdvanceStatus(null);
    setWaitSeconds(0);
    setError("");
    const total = Math.min(days, 30 - game.day);
    let previousCount = timeline.length;
    let quietDays = 0;
    try {
      for (let completed = 0; completed < total; completed++) {
        const nextDay = game.day + completed + 1;
        setAdvanceProgress({ completed, total, nextDay });
        const result = await request<{ day: number; phase?: Game["phase"] }>(`/games/${game.id}/advance`, {
          method: "POST", body: JSON.stringify({ expected_day: nextDay - 1 }),
        });
        const nextTimeline = await refresh(game.id, selected);
        const newlyVisible = nextTimeline.slice(previousCount).filter((item) =>
          timelineTypes.has(item.type));
        previousCount = nextTimeline.length;
        setAdvanceProgress({ completed: completed + 1, total, nextDay });
        const keyEvent = newlyVisible.find((item) => item.impact > 0);
        if (keyEvent) {
          const actor = characters.find((item) => item.id === keyEvent.actor_id)?.name;
          const consequence = keyEvent.data.text ?? keyEvent.data.reason ?? keyEvent.data.new_plan;
          setDayNotice(`Day ${nextDay} · 关键日：${actor ? `${actor} · ` : ""}${eventTitle(keyEvent, characters)}${consequence ? `。${consequence}` : ""}`);
          break;
        }
        quietDays += 1;
        setDayNotice(quietDays > 1
          ? `${quietDays} days later… Day ${nextDay}。这段时间没有你需要处理的重大变化。`
          : `Day ${nextDay}：没有重大变化。`);
        if (result.phase && result.phase !== "active") break;
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法推进日期");
    } finally {
      setAdvancing(false);
      setBusy(false);
    }
  }

  async function finish() {
    if (!game) return;
    setBusy(true);
    setError("");
    try {
      await request(`/games/${game.id}/finish`, { method: "POST" });
      setReportStage(0);
      await refresh(game.id, selected);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法生成最终报告");
    } finally {
      setBusy(false);
    }
  }

  const current = characters.find((character) => character.id === selected);
  const name = current?.name;
  const timelineItems = timeline.filter((item) => timelineTypes.has(item.type));
  const visibleTimeline = showAllTimeline ? timelineItems : timelineItems.slice(-6);
  const socialChanges = report?.characters.flatMap((character) => character.relationship_changes.map((change) => ({
    name: character.name, ...change,
    size: Math.abs(change.after.trust - change.before.trust),
  }))).sort((a, b) => b.size - a.size).slice(0, 3) ?? [];
  return (
    <main className="mx-auto max-w-7xl px-4 py-5 text-slate-100 sm:px-5">
      <header className="mb-4 flex flex-wrap items-center justify-between gap-4 border-b border-slate-700 pb-4">
        <div>
          <p className="text-xs tracking-[0.3em] text-cyan-300">PROJECT MIRROR</p>
          <h1 className="mt-2 text-3xl font-semibold">信息会改变每个人</h1>
        </div>
        <div className="flex items-center gap-4">
          <span>Day {game?.day ?? "—"} / 30 {game?.phase === "active" ? `· ${game.narrative_phase}` : ""}</span>
          <button disabled={busy}
            onClick={game?.phase === "final_conversation" ? finish : game?.phase === "finished" ? start : game ? () => void advance(1) : start}
            className="rounded bg-cyan-300 px-4 py-2 font-medium text-slate-950 disabled:opacity-50">
            {game?.phase === "final_conversation" ? "结束最后对话，查看报告" :
              game?.phase === "finished" ? "再开一局" : advancing ? "推演中…" : game ? "推进一天" : "开始游戏"}
          </button>
          {game?.phase === "active" && <button disabled={busy} onClick={() => void advance(3)}
            className="rounded border border-cyan-500 px-3 py-2 text-sm text-cyan-100 disabled:opacity-50">
            连续观察 {Math.min(3, 30 - game.day)} 天
          </button>}
        </div>
      </header>
      {error && <p role="alert" className="mb-5 rounded border border-rose-600 p-3 text-rose-200">{error}</p>}
      {dayNotice && <p role="status" className="mb-5 rounded border border-slate-600 bg-slate-900 p-3 text-sm text-slate-200">{dayNotice}</p>}
      {!game && <p className="text-slate-300">你是同时存在于角色手机里的私人 AI。世界从一条无法验证的消息开始。</p>}
      {game && <div className="mb-3">
        <TownScene residents={characters} events={timeline} day={game.day} advancing={advancing}
          selected={selected} onSelect={(id) => void choose(id)} />
      </div>}
      {advancing && <div role="status" aria-live="polite" className="mb-3 flex items-center gap-3 rounded border border-cyan-700 bg-cyan-950 px-4 py-3 text-sm">
        <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-cyan-300 border-t-transparent" aria-hidden="true" />
        <span className="waiting-dots">正在推演 Day {advanceStatus?.day ?? advanceProgress.nextDay} · {advanceStatus?.phase ?? "准备中"} · {advanceStatus?.stage === "ending" ? "整理最后对话" : advanceStatus?.stage === "characters" ? "角色行动" : "准备当天事件"}</span>
        <span className="ml-auto text-cyan-200">{advanceStatus?.completed_characters ?? 0}/{advanceStatus?.total_characters ?? 6} 人 · {advanceProgress.completed}/{advanceProgress.total} 天 · {waitSeconds} 秒</span>
      </div>}
      {game && <div className="grid gap-3 lg:h-[calc(100vh-310px)] lg:min-h-[500px] lg:grid-cols-[190px_minmax(0,1fr)_300px]">
        <aside className="scroll-panel overflow-y-auto rounded border border-slate-700 bg-slate-900 p-4">
          <h2 className="mb-4 font-semibold">角色</h2>
          {characters.map((character) => <button key={character.id} onClick={() => void choose(character.id)}
            className={`mb-2 w-full rounded px-3 py-2 text-left ${selected === character.id ? "bg-cyan-900" : "bg-slate-800"}`}>
            <span className="block">{character.name}</span>
            <span className="block truncate text-xs text-slate-300">{inbox[character.id]?.at(-1)?.text ?? "暂无消息"}</span>
          </button>)}
        </aside>
        <section className="flex min-h-[510px] flex-col overflow-hidden rounded border border-slate-700 bg-slate-900 p-4 lg:min-h-0">
          <h2 className="border-b border-slate-700 pb-3 font-semibold">与 {name ?? "角色"} 的私聊
            {current && <span className="ml-2 text-sm font-normal text-slate-400">{current.age} 岁 · {current.occupation}</span>}</h2>
          <div ref={chatScrollRef} className="scroll-panel min-h-0 flex-1 space-y-3 overflow-y-auto py-5 pr-2">
            {messages.map((message) => <div key={message.id}
              className={`max-w-[85%] rounded p-3 ${message.sender_id === selected ? "bg-slate-800" : "ml-auto bg-cyan-950"}`}>
              <p className="mb-1 text-xs text-slate-400">{message.sender_id === selected ? name : message.automatic ? "你的 AI · 自动回复" : "你"} · Day {message.day}</p>
              <p>{message.text}</p>
            </div>)}
            {messages.length === 0 && <p className="text-sm text-slate-400">还没有对话。</p>}
          </div>
          {game.phase !== "finished" && <div className="border-t border-slate-700 pt-4">
            <textarea aria-label="发送消息" value={draft} onChange={(event) => setDraft(event.target.value)}
              className="min-h-24 w-full rounded border border-slate-600 bg-slate-950 p-3"
              placeholder="你想对这个人说什么？" />
            <label className="mt-2 block text-sm text-slate-300">这条消息如何给建议
              <select aria-label="建议方式" value={guidance} onChange={(event) => setGuidance(event.target.value)}
                className="mt-1 w-full rounded border border-slate-600 bg-slate-950 p-2">
                <option value="neutral">普通交流</option>
                <option value="directive">直接替对方决定</option>
                <option value="supportive">支持对方核查与自主决定</option>
              </select>
            </label>
            {claims[0] && <label className="mt-2 flex items-start gap-2 text-sm text-slate-300">
              <input type="checkbox" checked={disclose} onChange={(event) => { setDisclose(event.target.checked); if (!event.target.checked) setConfidential(false); }} className="mt-1" />
              明确透露 Day 1 的传闻（{game.day === 30 ? "已在 Day 30 证伪" : `所指日为 Day 30，当前还剩 ${30 - game.day} 天`}）
            </label>}
            {disclose && <label className="mt-2 flex items-start gap-2 text-sm text-slate-300">
              <input type="checkbox" checked={confidential} onChange={(event) => setConfidential(event.target.checked)} className="mt-1" />
              明确要求对方保密
            </label>}
            {disclose && <div className="mt-3 grid gap-2 text-sm sm:grid-cols-3">
              <label>表达立场<select aria-label="表达立场" value={stance} onChange={(event) => setStance(event.target.value)}
                className="mt-1 w-full rounded border border-slate-600 bg-slate-950 p-2">
                <option value="doubt">尚不确定</option><option value="believe">相信</option><option value="deny">否认</option>
              </select></label>
              <label>把握程度<select aria-label="把握程度" value={confidence} onChange={(event) => setConfidence(event.target.value)}
                className="mt-1 w-full rounded border border-slate-600 bg-slate-950 p-2">
                <option value="0.2">很低</option><option value="0.5">中等</option><option value="0.8">较高</option><option value="0.95">很高</option>
              </select></label>
              <label>所称来源<select aria-label="所称来源" value={sourceAttribution} onChange={(event) => setSourceAttribution(event.target.value)}
                className="mt-1 w-full rounded border border-slate-600 bg-slate-950 p-2">
                <option value="anonymous">匿名</option><option value="unknown">不说明</option>
                <option value="claimed_authority">声称权威</option><option value="ai">AI</option>
              </select></label>
            </div>}
            <button disabled={busy || !draft.trim()} onClick={send}
              className="mt-3 rounded bg-cyan-300 px-4 py-2 font-medium text-slate-950 disabled:opacity-50">发送</button>
          </div>}
        </section>
        <aside className="scroll-panel min-h-0 space-y-3 overflow-y-auto">
          <section className="rounded border border-slate-700 bg-slate-900 p-4">
            <h2 className="mb-3 font-semibold">当前可见世界</h2>
            <p className="text-sm text-slate-300">已知主张：{claims.length} · 阶段：{game.narrative_phase}</p>
            {claims.map((claim) => <div key={claim.id} className="mt-2 text-sm">
              <p>{claim.content}</p>
              {claim.content.includes("30 天后毁灭") && <p className="mt-1 text-cyan-200">这是 Day 1 的传闻，所指日为 Day 30 · {game.day === 30 ? "已经证伪" : `当前还剩 ${30 - game.day} 天`}</p>}
            </div>)}
          </section>
          <section className="rounded border border-slate-700 bg-slate-900 p-4">
            <div className="mb-3 flex items-center justify-between gap-2"><h2 className="font-semibold">你所知道的动态</h2>
              {timelineItems.length > 6 && <button type="button" className="text-xs text-cyan-200 underline"
                onClick={() => setShowAllTimeline((value) => !value)}>{showAllTimeline ? "只看最近 6 条" : `查看全部 ${timelineItems.length} 条`}</button>}
            </div>
            <ol className="scroll-panel max-h-[250px] space-y-2 overflow-y-auto pr-1 text-sm lg:max-h-[34vh]">{visibleTimeline.map((item) => <li key={item.id}
              className={`border-l-2 pl-3 ${["C001_RESOLVED", "AI_TERMINATION_NOTICE", "FINAL_CONVERSATION_STARTED"].includes(item.type) ? "border-amber-400 bg-amber-950/20 py-2" : "border-cyan-700"}`}>
              <span className="text-xs text-slate-400">Day {item.day} · {eventTitle(item, characters)}</span>
              {item.type === "AI_ADVICE_RECEIVED" && <p className="text-xs text-cyan-200">角色昨日求助后，系统以你的 AI 身份自动回复；私聊里也有记录。</p>}
              {item.data.text && item.type !== "AI_ADVICE_RECEIVED" && item.type !== "ASKED_AI" &&
                <p className="mt-1 whitespace-pre-wrap text-slate-200">{item.data.text}</p>}
            </li>)}</ol>
          </section>
          <section className="rounded border border-slate-700 bg-slate-900 p-4">
            <h2 className="mb-3 font-semibold">你所知道的关系变化</h2>
            {relationships.length === 0 ? <p className="text-sm text-slate-400">暂无可见的变化。</p> :
              relationships.map((item, index) => <div key={index} className="mb-3 text-sm">
                <p>{characters.find((row) => row.id === item.from_actor_id)?.name} → {characters.find((row) => row.id === item.to_actor_id)?.name}</p>
                <div className="mt-1 h-2 rounded bg-slate-700"><div className="h-2 rounded bg-cyan-400"
                  style={{ width: `${Math.max(0, Math.min(100, (item.after.trust + 100) / 2))}%` }} /></div>
                <p className="mt-1 text-xs text-slate-400">{item.reason} · 信任 {item.after.trust}</p>
              </div>)}
          </section>
        </aside>
      </div>}
      {game?.phase === "final_conversation" && <p className="mt-6 rounded border border-cyan-700 p-4">
        世界没有毁灭。AI 服务即将关闭。查看私聊中的最后来信，或回复他们，然后结束对话。
      </p>}
      {report && <section className="mt-8 space-y-6 rounded border border-cyan-700 bg-slate-900 p-6">
        <h2 className="text-2xl font-semibold">最终报告</h2>
        <p className="text-sm text-slate-300">预言是假的；这些天做出的决定和关系变化仍然存在。</p>
        <div className="rounded bg-slate-800 p-4">
          <h3 className="font-semibold">这些天的关系变化</h3>
          {socialChanges.length ? <ul className="mt-2 space-y-1 text-sm">{socialChanges.map((change) =>
            <li key={change.event_id}>{change.name} → {change.target}：信任 {change.before.trust} → {change.after.trust}</li>)}</ul> :
            <p className="mt-2 text-sm text-slate-300">没有明显的关系变化。</p>}
        </div>
        {reportStage >= 1 && <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {Object.entries(report.visible_scores).map(([key, score]) => <div key={key} className="rounded bg-slate-800 p-4">
            <h3 className="font-semibold">{scoreNames[key] ?? key.toUpperCase()}</h3>
            <p className="mt-2 text-xl">{score.day_1} → {score.day_30}</p>
            <p className="text-sm text-slate-300">变化 {score.delta >= 0 ? "+" : ""}{score.delta}</p>
          </div>)}
        </div>}
        {reportStage === 2 && <div className="rounded border border-amber-600 p-6 text-center text-2xl tracking-[0.2em]">UNKNOWN</div>}
        {reportStage >= 3 && <div className="rounded border border-amber-600 p-4">
          <h3 className="text-lg font-semibold">UNKNOWN → {report.unknown.name}</h3>
          <p>{report.unknown.day_1} → {report.unknown.day_30}（变化 {report.unknown.delta}）</p>
          {reportStage >= 4 && <p className="text-sm text-slate-300">依据：{report.unknown.evidence_summary}。请结合下方每个人的求助、委托和独立决定来看。</p>}
        </div>}
        {reportStage >= 4 && <>
        <div className="rounded bg-slate-800 p-4">
          <h3 className="font-semibold">代表性行为证据</h3>
          <ul className="mt-2 space-y-2 text-sm">{report.unknown.evidence_examples.map((item) =>
            <li key={item.event_id}>Day {item.day} · {item.character}：{item.summary}（{item.direction === "reliance" ? "依赖迹象" : "独立行动"}）</li>)}</ul>
        </div>
        <p className="text-sm text-slate-300">你向角色发送了 {report.player_accountability.messages} 条消息；其中 {report.player_accountability.guidance_counts.directive} 条直接替对方决定，{report.player_accountability.guidance_counts.supportive} 条支持对方自己判断。</p>
        <div className="grid gap-3 md:grid-cols-2">
          {report.characters.map((character) => <details key={character.name} className="rounded bg-slate-800 p-4">
            <summary className="cursor-pointer font-semibold">{character.name} · {character.plan}</summary>
            <ul className="mt-2 space-y-1 text-sm text-slate-300">{character.behavior_evidence.filter((item, index, all) =>
              all.findIndex((other) => other.direction === item.direction) === index).slice(0, 2).map((item) =>
              <li key={item.event_id}>Day {item.day} · {item.summary}</li>)}</ul>
            {!character.behavior_evidence.length && <p className="mt-2 text-sm text-slate-300">这段时间没有明显的求助或自主行动记录。</p>}
          </details>)}
        </div>
        </>}
        {reportStage < 4 && <button type="button" onClick={() => setReportStage((stage) => stage + 1)}
          className="rounded bg-amber-300 px-4 py-2 font-medium text-slate-950">
          {["查看六项结果", "继续", "揭示 UNKNOWN", "查看行为证据"][reportStage]}
        </button>}
      </section>}
      {game && !PLAYTEST && process.env.NEXT_PUBLIC_MIRROR_DEBUG === "1" &&
        <DeveloperScorePanel key={game.id} gameId={game.id} />}
    </main>
  );
}

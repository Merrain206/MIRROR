type Resident = { id: string; name: string };
type TownEvent = { id: string; day: number; type: string; actor_id?: string | null; target_actor_id?: string | null };

const spots = [
  { left: 14, top: 62 }, { left: 31, top: 36 }, { left: 48, top: 68 },
  { left: 64, top: 35 }, { left: 80, top: 66 }, { left: 88, top: 36 },
];

const eventLabels: Record<string, string> = {
  ASKED_AI: "来信求助", AI_ADVICE_RECEIVED: "收到建议", PLAN_CHANGED: "计划有变",
  CHARACTER_SHARED_INFORMATION: "消息传开", CLAIM_VERIFICATION_ATTEMPTED: "寻找证据",
  LIFE_EVENT: "小镇日常", C001_RESOLVED: "传闻证伪", AI_TERMINATION_NOTICE: "告别通知",
  RELATIONSHIP_CHANGED: "关系变化", KNOWLEDGE_ACQUIRED: "听到新消息",
};

export default function TownScene({ residents, events, day, advancing, selected, onSelect }: {
  residents: Resident[]; events: TownEvent[]; day: number; advancing: boolean;
  selected: string | null; onSelect: (id: string) => void;
}) {
  const recent = events.filter((event) => eventLabels[event.type] && event.day >= day - 1).slice(-2);
  return <section className={`town-scene ${advancing ? "town-scene-advancing" : ""}`} aria-label="小镇动态示意图">
    <div className="town-sky" />
    <div className="town-ground" />
    <div className="town-road town-road-horizontal" />
    <div className="town-road town-road-vertical" />
    <div className="town-building town-building-one"><span>社区中心</span></div>
    <div className="town-building town-building-two"><span>图书馆</span></div>
    <div className="town-building town-building-three"><span>工作室</span></div>
    <div className="town-tree town-tree-one" /><div className="town-tree town-tree-two" />
    <div className="town-tree town-tree-three" /><div className="town-tree town-tree-four" />
    {residents.map((resident, index) => <button key={resident.id} type="button"
      className={`town-resident ${selected === resident.id ? "town-resident-selected" : ""}`}
      style={{ left: `${spots[index % spots.length].left}%`, top: `${spots[index % spots.length].top}%`, animationDelay: `${index * -.7}s` }}
      onClick={() => onSelect(resident.id)} title={`与 ${resident.name} 聊天`}>
      <span className="town-resident-head">{resident.name.charAt(0)}</span><span className="town-resident-name">{resident.name}</span>
    </button>)}
    {recent.map((event, index) => {
      const actor = residents.findIndex((row) => row.id === (event.target_actor_id ?? event.actor_id));
      const spot = spots[actor >= 0 ? actor % spots.length : index];
      return <div key={event.id} className="town-event" style={{ left: `${spot.left}%`, top: `${Math.max(8, spot.top - 28)}%` }}>
        {eventLabels[event.type]}
      </div>;
    })}
    <div className="town-caption">Day {day} · 可见动态示意</div>
  </section>;
}

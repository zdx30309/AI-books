import {
  ArrowRight,
  EyeOff,
  Lightbulb,
  MessageCircle,
  SmilePlus,
  Sparkles,
  Volume2,
} from "lucide-react";

export interface StoryComicFormState {
  title: string;
  episodeCount: string;
  premise: string;
  setting: string;
  mood: string;
  ending: string;
  character: string;
  source: string;
}

export interface HomophoneComicFormState {
  title: string;
  episodeCount: string;
  seed: string;
  scene: string;
  flavor: string;
  edge: string;
  character: string;
  avoidTopics: string;
  source: string;
}

export const createStoryComicForm = (): StoryComicFormState => ({
  title: "",
  episodeCount: "12",
  premise: "",
  setting: "城市日常",
  mood: "会心一笑",
  ending: "轻反转",
  character: "",
  source: "",
});

export const createHomophoneComicForm = (): HomophoneComicFormState => ({
  title: "",
  episodeCount: "12",
  seed: "",
  scene: "日常尴尬",
  flavor: "干巴荒诞",
  edge: "好笑但不刻薄",
  character: "",
  avoidTopics: "",
  source: "",
});

export function buildStoryComicBrief(value: StoryComicFormState) {
  return [
    "【图文故事工作台】",
    `故事起点：${value.premise.trim() || "由系列名自由发想，但必须是一件具体生活小事"}`,
    `生活场域：${value.setting}`,
    `阅读感受：${value.mood}`,
    `结尾方式：${value.ending}`,
    value.source.trim() ? `真实素材：\n${value.source.trim()}` : "",
  ].filter(Boolean).join("\n");
}

export function buildHomophoneComicBrief(value: HomophoneComicFormState) {
  return [
    "【谐音梗编剧室】",
    `谐音种子：${value.seed.trim() || "未指定，先海选原创谐音，再选择真正能讲成故事的一组"}`,
    `笑点场景：${value.scene}`,
    `喜剧口味：${value.flavor}`,
    `吐槽力度：${value.edge}`,
    `避开内容：${value.avoidTopics.trim() || "攻击弱者、身份群体、外貌、疾病，以及任何需要错别字才能成立的梗"}`,
    value.source.trim() ? `真实素材：\n${value.source.trim()}` : "",
  ].filter(Boolean).join("\n");
}

const STORY_SETTINGS = ["城市日常", "朋友相处", "办公室", "家庭生活", "邻里偶遇", "小动物日常"];
const STORY_MOODS = ["会心一笑", "温暖但不煽情", "轻微荒诞", "安静回味"];
const STORY_ENDINGS = ["轻反转", "动作收尾", "一句回扣", "留一点没说完"];
const HOMOPHONE_SCENES = ["日常尴尬", "朋友互损", "办公室观察", "自嘲翻车", "社交误会", "向上吐槽"];
const HOMOPHONE_FLAVORS = ["干巴荒诞", "一本正经胡说", "突然回神", "越想越好笑"];
const HOMOPHONE_EDGES = [
  { value: "轻松无攻击", title: "轻松", text: "只做误会与荒诞" },
  { value: "好笑但不刻薄", title: "够损", text: "吐槽行为，不伤人" },
  { value: "犀利向上讽刺", title: "犀利", text: "只讽刺权力与坏习惯" },
];

const inputClass =
  "w-full rounded-xl border border-gold-500/15 bg-base-800/65 px-4 py-3 text-sm leading-relaxed text-warm-100 placeholder:text-warm-600 outline-none transition-colors focus:border-gold-400/50";

function ChoiceChips({
  options,
  value,
  onChange,
  tone = "story",
}: {
  options: string[];
  value: string;
  onChange: (value: string) => void;
  tone?: "story" | "joke";
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {options.map((option) => {
        const active = option === value;
        return (
          <button
            key={option}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(option)}
            className={`rounded-full border px-3.5 py-2 text-xs transition-all ${
              active
                ? tone === "story"
                  ? "border-emerald-300/50 bg-emerald-300/10 text-emerald-100"
                  : "border-orange-300/55 bg-orange-300/10 text-orange-100"
                : "border-gold-500/15 bg-base-800/45 text-warm-400 hover:border-gold-400/35 hover:text-warm-200"
            }`}
          >
            {option}
          </button>
        );
      })}
    </div>
  );
}

function CountField({
  id,
  value,
  onChange,
}: {
  id: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <div>
      <label htmlFor={id} className="mb-2 block text-[11px] tracking-[0.2em] text-warm-400">
        故事数量
      </label>
      <div className="relative">
        <input
          id={id}
          type="number"
          min={1}
          step={1}
          value={value}
          onChange={(event) => onChange(event.target.value.replace(/[^\d]/g, ""))}
          className={`${inputClass} pr-16`}
        />
        <span className="pointer-events-none absolute right-4 top-1/2 -translate-y-1/2 text-xs text-warm-500">
          篇
        </span>
      </div>
    </div>
  );
}

export function StoryComicCreator({
  value,
  onChange,
}: {
  value: StoryComicFormState;
  onChange: (next: StoryComicFormState) => void;
}) {
  const patch = (next: Partial<StoryComicFormState>) => onChange({ ...value, ...next });

  return (
    <section className="overflow-hidden rounded-2xl border border-emerald-300/20 bg-[linear-gradient(145deg,rgba(21,45,38,0.52),rgba(15,12,11,0.9))]">
      <div className="border-b border-emerald-300/15 px-5 py-5 sm:px-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="mb-2 flex items-center gap-2 text-[11px] tracking-[0.22em] text-emerald-200/80">
              <Lightbulb className="h-4 w-4" strokeWidth={1.6} />
              图文故事工作台
            </div>
            <h3 className="font-serif text-xl text-warm-50">先给一件小事，不必先写完整剧本</h3>
            <p className="mt-2 max-w-xl text-xs leading-6 text-warm-400">
              系统会把一个生活起点拆成连续滑动的小故事。每张只推进一拍，允许停顿和留白，不会再套用治愈漫画的通用表单。
            </p>
          </div>
          <div className="flex items-center gap-2 rounded-full border border-emerald-300/15 bg-base-900/30 px-3 py-2 text-[10px] tracking-wider text-emerald-100/80">
            <span>一件小事</span><ArrowRight className="h-3 w-3" /><span>逐页变化</span><ArrowRight className="h-3 w-3" /><span>轻轻落地</span>
          </div>
        </div>
      </div>

      <div className="space-y-5 p-5 sm:p-6">
        <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_150px]">
          <div>
            <label htmlFor="story-comic-title" className="mb-2 block text-[11px] tracking-[0.2em] text-warm-400">
              系列名
            </label>
            <input
              id="story-comic-title"
              value={value.title}
              onChange={(event) => patch({ title: event.target.value })}
              placeholder="比如：今天发生了一点小事"
              className={inputClass}
            />
          </div>
          <CountField id="story-comic-count" value={value.episodeCount} onChange={(episodeCount) => patch({ episodeCount })} />
        </div>

        <div>
          <div className="mb-2 flex items-center justify-between gap-3">
            <label htmlFor="story-comic-premise" className="text-[11px] tracking-[0.2em] text-warm-300">
              这次从什么小事开始
            </label>
            <span className="text-[10px] text-warm-600">可以只写半句话</span>
          </div>
          <textarea
            id="story-comic-premise"
            value={value.premise}
            onChange={(event) => patch({ premise: event.target.value })}
            placeholder="比如：下雨天，一把伞在便利店门口被拿错了。"
            rows={3}
            className={`${inputClass} resize-none`}
          />
        </div>

        <div className="grid gap-5 lg:grid-cols-2">
          <div>
            <p className="mb-2 text-[11px] tracking-[0.2em] text-warm-400">故事发生在哪</p>
            <ChoiceChips options={STORY_SETTINGS} value={value.setting} onChange={(setting) => patch({ setting })} />
          </div>
          <div>
            <p className="mb-2 text-[11px] tracking-[0.2em] text-warm-400">读完是什么感觉</p>
            <ChoiceChips options={STORY_MOODS} value={value.mood} onChange={(mood) => patch({ mood })} />
          </div>
        </div>

        <div>
          <p className="mb-2 text-[11px] tracking-[0.2em] text-warm-400">最后怎么停下来</p>
          <ChoiceChips options={STORY_ENDINGS} value={value.ending} onChange={(ending) => patch({ ending })} />
        </div>

        <details className="group rounded-xl border border-emerald-300/10 bg-base-900/25 px-4 py-3">
          <summary className="cursor-pointer list-none text-xs tracking-wider text-warm-300 marker:hidden">
            固定角色与参考素材 <span className="ml-2 text-warm-600">选填</span>
          </summary>
          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            <textarea
              value={value.character}
              onChange={(event) => patch({ character: event.target.value })}
              placeholder="固定角色：外形、性格、随身物件……"
              rows={4}
              className={`${inputClass} resize-none`}
            />
            <textarea
              value={value.source}
              onChange={(event) => patch({ source: event.target.value })}
              placeholder="可粘贴真实经历或已有文案，只取事件，不照抄句子。"
              rows={4}
              className={`${inputClass} resize-none`}
            />
          </div>
        </details>
      </div>
    </section>
  );
}

export function HomophoneComicCreator({
  value,
  onChange,
}: {
  value: HomophoneComicFormState;
  onChange: (next: HomophoneComicFormState) => void;
}) {
  const patch = (next: Partial<HomophoneComicFormState>) => onChange({ ...value, ...next });

  return (
    <section className="overflow-hidden rounded-2xl border border-orange-300/25 bg-[radial-gradient(circle_at_top_right,rgba(251,146,60,0.1),transparent_42%),linear-gradient(145deg,rgba(44,27,20,0.78),rgba(15,12,11,0.94))]">
      <div className="border-b border-orange-300/15 px-5 py-5 sm:px-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="mb-2 flex items-center gap-2 text-[11px] tracking-[0.22em] text-orange-200/85">
              <Volume2 className="h-4 w-4" strokeWidth={1.7} />
              谐音梗编剧室
            </div>
            <h3 className="font-serif text-xl text-warm-50">先让故事好笑，最后才让声音对上</h3>
            <p className="mt-2 max-w-xl text-xs leading-6 text-warm-400">
              你只需选笑点方向，或者给一组谐音种子。系统先写正常故事，前六成藏住答案，最后两三张再完成回扣。
            </p>
          </div>
          <div className="rounded-xl border border-orange-300/15 bg-base-900/35 px-3 py-2 text-[10px] leading-5 text-orange-100/80">
            不错字 · 不解释 · 不硬凑
          </div>
        </div>
      </div>

      <div className="space-y-5 p-5 sm:p-6">
        <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_150px]">
          <div>
            <label htmlFor="homophone-comic-title" className="mb-2 block text-[11px] tracking-[0.2em] text-warm-400">
              系列名
            </label>
            <input
              id="homophone-comic-title"
              value={value.title}
              onChange={(event) => patch({ title: event.target.value })}
              placeholder="比如：怎么听都不太对"
              className={inputClass}
            />
          </div>
          <CountField id="homophone-comic-count" value={value.episodeCount} onChange={(episodeCount) => patch({ episodeCount })} />
        </div>

        <div className="rounded-xl border border-orange-300/15 bg-base-900/30 p-4">
          <div className="mb-3 flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-orange-200" strokeWidth={1.6} />
            <label htmlFor="homophone-seed" className="text-[11px] tracking-[0.2em] text-warm-300">
              有想用的谐音吗
            </label>
            <span className="text-[10px] text-warm-600">没有就留空，让编剧室先海选</span>
          </div>
          <input
            id="homophone-seed"
            value={value.seed}
            onChange={(event) => patch({ seed: event.target.value })}
            placeholder="格式不限，例如：报销 / 抱箱；也可以只写一个想吐槽的词"
            className={inputClass}
          />
        </div>

        <div>
          <p className="mb-2 flex items-center gap-2 text-[11px] tracking-[0.2em] text-warm-400">
            <MessageCircle className="h-3.5 w-3.5" /> 笑谁，或者笑哪件事
          </p>
          <ChoiceChips options={HOMOPHONE_SCENES} value={value.scene} onChange={(scene) => patch({ scene })} tone="joke" />
        </div>

        <div>
          <p className="mb-2 flex items-center gap-2 text-[11px] tracking-[0.2em] text-warm-400">
            <SmilePlus className="h-3.5 w-3.5" /> 怎么好笑
          </p>
          <ChoiceChips options={HOMOPHONE_FLAVORS} value={value.flavor} onChange={(flavor) => patch({ flavor })} tone="joke" />
        </div>

        <div>
          <p className="mb-2 text-[11px] tracking-[0.2em] text-warm-400">吐槽力度</p>
          <div className="grid gap-2 sm:grid-cols-3">
            {HOMOPHONE_EDGES.map((option) => {
              const active = value.edge === option.value;
              return (
                <button
                  key={option.value}
                  type="button"
                  aria-pressed={active}
                  onClick={() => patch({ edge: option.value })}
                  className={`rounded-xl border px-3 py-3 text-left transition-all ${
                    active
                      ? "border-orange-300/50 bg-orange-300/10"
                      : "border-gold-500/15 bg-base-900/25 hover:border-orange-300/25"
                  }`}
                >
                  <span className={`block text-sm ${active ? "text-orange-100" : "text-warm-300"}`}>{option.title}</span>
                  <span className="mt-1 block text-[10px] leading-4 text-warm-600">{option.text}</span>
                </button>
              );
            })}
          </div>
        </div>

        <div className="rounded-xl border border-orange-300/10 bg-base-900/25 p-4">
          <div className="mb-3 flex items-center gap-2 text-[11px] tracking-[0.18em] text-warm-400">
            <EyeOff className="h-4 w-4 text-orange-200/80" strokeWidth={1.6} />
            揭晓节奏预览
          </div>
          <div className="grid grid-cols-8 gap-1.5" aria-label="前六成讲故事，最后两三张揭晓谐音">
            {["开场", "动作", "继续", "反常", "更怪", "发问", "答案", "回扣"].map((label, index) => (
              <div key={label} className="text-center">
                <div className={`h-1.5 rounded-full ${index < 5 ? "bg-warm-600/45" : index < 7 ? "bg-orange-300/45" : "bg-orange-200"}`} />
                <span className="mt-1.5 block text-[9px] text-warm-600">{label}</span>
              </div>
            ))}
          </div>
        </div>

        <div>
          <label htmlFor="homophone-avoid" className="mb-2 block text-[11px] tracking-[0.2em] text-warm-400">
            这次别碰什么 <span className="tracking-normal text-warm-600">选填</span>
          </label>
          <input
            id="homophone-avoid"
            value={value.avoidTopics}
            onChange={(event) => patch({ avoidTopics: event.target.value })}
            placeholder="例如：不写职场、不碰外貌、不用网络热梗"
            className={inputClass}
          />
        </div>

        <details className="group rounded-xl border border-orange-300/10 bg-base-900/25 px-4 py-3">
          <summary className="cursor-pointer list-none text-xs tracking-wider text-warm-300 marker:hidden">
            固定角色与真实素材 <span className="ml-2 text-warm-600">选填</span>
          </summary>
          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            <textarea
              value={value.character}
              onChange={(event) => patch({ character: event.target.value })}
              placeholder="固定角色：外形、性格、彼此关系……"
              rows={4}
              className={`${inputClass} resize-none`}
            />
            <textarea
              value={value.source}
              onChange={(event) => patch({ source: event.target.value })}
              placeholder="可以写一件真实糗事，系统只借事件，不照搬台词。"
              rows={4}
              className={`${inputClass} resize-none`}
            />
          </div>
        </details>
      </div>
    </section>
  );
}

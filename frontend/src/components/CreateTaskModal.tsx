import { useEffect, useState } from "react";
import { BookOpenCheck, Loader2, X } from "lucide-react";
import { api, ApiError } from "@/api/client";
import type { TaskCategoryOption } from "@/api/types";

interface Props {
  open: boolean;
  onClose: () => void;
  onCreated: (taskId: string) => void;
}

const DEFAULT_CATEGORIES: TaskCategoryOption[] = [
  {
    id: "social_science",
    label: "社会痛点",
    core_feature: "直面现实困境，高共鸣",
    directions: "教育、婚姻、住房、职场压力、养老",
    prompt_status: "ready",
  },
  {
    id: "life_philosophy",
    label: "人生哲学",
    core_feature: "提供认知升级或心灵慰藉",
    directions: "励志、成长、价值观、生活态度",
    prompt_status: "ready",
  },
  {
    id: "popular_science",
    label: "科普读物",
    core_feature: "把复杂知识讲明白",
    directions: "AI、经济、投资、科技、名人思想",
    prompt_status: "pending",
  },
  {
    id: "wisdom_stories",
    label: "小故事·智慧",
    core_feature: "以故事承载哲理，易传播",
    directions: "佛学、禅宗、寓言、民间智慧",
    prompt_status: "pending",
  },
  {
    id: "biography",
    label: "传记·人物",
    core_feature: "浓缩人生关键节点与品格",
    directions: "名人成长、失败与转折、精神内核",
    prompt_status: "pending",
  },
  {
    id: "history_culture",
    label: "历史·文化",
    core_feature: "切片式讲述，有趣有料",
    directions: "冷知识、人物趣闻、古代生活、历史启示",
    prompt_status: "pending",
  },
  {
    id: "emotion_relationship",
    label: "情感·关系",
    core_feature: "贴近日常，治愈或实用",
    directions: "亲情、友情、爱情、社交边界",
    prompt_status: "pending",
  },
  {
    id: "psychology_self_help",
    label: "心理自助",
    core_feature: "方法导向，解决具体情绪问题",
    directions: "焦虑、内耗、拖延、自卑、失眠",
    prompt_status: "pending",
  },
  {
    id: "suspense_micro_reasoning",
    label: "悬疑·微推理",
    core_feature: "篇幅短小，反转或悬念",
    directions: "日常谜团、封闭场景、人性故事",
    prompt_status: "pending",
  },
  {
    id: "workplace_survival",
    label: "职场生存",
    core_feature: "实战技巧，新人友好",
    directions: "沟通、汇报、效率、避坑、跳槽",
    prompt_status: "pending",
  },
  {
    id: "health_wellness",
    label: "健康·养生",
    core_feature: "轻科普，可立即行动",
    directions: "睡眠、运动、饮食、放松、预防",
    prompt_status: "pending",
  },
  {
    id: "classic_interpretation",
    label: "经典·解读",
    core_feature: "降低门槛，提取精华",
    directions: "名著、电影、哲学、艺术",
    prompt_status: "pending",
  },
  {
    id: "web_short_story",
    label: "网文短篇",
    core_feature: "网络文学风格，强节奏、爽点密集",
    directions: "系统文、重生/穿书、甜宠、沙雕脑洞、悬疑反转式短篇",
    prompt_status: "pending",
  },
];

export function CreateTaskModal({ open, onClose, onCreated }: Props) {
  const [url, setUrl] = useState("");
  const [rawText, setRawText] = useState("");
  const [category, setCategory] = useState("social_science");
  const [categories, setCategories] = useState<TaskCategoryOption[]>(DEFAULT_CATEGORIES);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const selectedCategory =
    categories.find((item) => item.id === category) ?? DEFAULT_CATEGORIES[0]!;

  useEffect(() => {
    if (!open) {
      setUrl("");
      setRawText("");
      setCategory("social_science");
      setCategories(DEFAULT_CATEGORIES);
      setError(null);
      setSubmitting(false);
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    api
      .getTaskCategories()
      .then((res) => {
        if (!cancelled && res.items.length > 0) {
          setCategories(res.items);
          setCategory((prev) =>
            res.items.some((item) => item.id === prev) ? prev : res.items[0].id,
          );
        }
      })
      .catch(() => {
        if (!cancelled) setCategories(DEFAULT_CATEGORIES);
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const submit = async () => {
    setError(null);
    if (!url.trim() && !rawText.trim()) {
      setError("请填入抖音链接，或直接粘贴视频文稿");
      return;
    }
    setSubmitting(true);
    try {
      const config: Record<string, unknown> = { category };
      if (rawText.trim()) config.raw_text = rawText.trim();
      const res = await api.createTask({
        url: url.trim() || "manual://raw_text",
        config,
      });
      onCreated(res.task_id);
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? `[${e.status}] ${e.detail}`
          : e instanceof Error
            ? e.message
            : "创建任务失败";
      setError(msg);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-base-900/80 backdrop-blur-sm animate-fade-in-up"
      onClick={onClose}
    >
      <div
        className="card-warm relative w-[min(640px,92vw)] p-8 shadow-card-warm"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <button
          type="button"
          onClick={onClose}
          aria-label="关闭"
          className="absolute right-5 top-5 flex h-8 w-8 items-center justify-center rounded-full text-warm-400 transition-colors hover:bg-warm-600/15 hover:text-warm-100"
        >
          <X className="h-4 w-4" strokeWidth={1.5} />
        </button>

        <h2 className="font-serif text-2xl tracking-wide text-warm-100">
          开始创作
        </h2>
        <p className="mt-2 text-xs tracking-wide text-warm-400">
          选择书籍方向后，粘贴抖音链接或已有文稿，AI 会生成对应类型的完整书稿
        </p>

        <div className="mt-7 space-y-5">
          <div>
            <label
              htmlFor="task-category"
              className="mb-2 flex items-center gap-2 text-[11px] tracking-widest text-warm-300"
            >
              <BookOpenCheck className="h-3.5 w-3.5 text-gold-300" strokeWidth={1.7} />
              书籍方向
            </label>
            <select
              id="task-category"
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              className="w-full rounded-lg border border-gold-500/15 bg-base-700/60 px-4 py-2.5 text-sm text-warm-100 outline-none transition-colors focus:border-gold-400/50"
            >
              {categories.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
            </select>
            <p className="mt-2 text-xs leading-relaxed text-warm-500">
              {selectedCategory.core_feature} · {selectedCategory.directions}
            </p>
          </div>

          <div>
            <label
              htmlFor="task-url"
              className="mb-2 block text-[11px] tracking-widest text-warm-300"
            >
              视频链接
            </label>
            <input
              id="task-url"
              type="text"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://v.douyin.com/..."
              className="w-full rounded-lg border border-gold-500/15 bg-base-700/60 px-4 py-2.5 text-sm text-warm-100 placeholder:text-warm-500 outline-none transition-colors focus:border-gold-400/50"
            />
          </div>

          <div className="relative flex items-center text-[10px] tracking-widest text-warm-500">
            <div className="flex-1 border-t border-gold-500/10" />
            <span className="px-3">或直接粘贴已有文稿</span>
            <div className="flex-1 border-t border-gold-500/10" />
          </div>

          <div>
            <label
              htmlFor="task-raw-text"
              className="mb-2 block text-[11px] tracking-widest text-warm-300"
            >
              视频文稿（可选）
            </label>
            <textarea
              id="task-raw-text"
              value={rawText}
              onChange={(e) => setRawText(e.target.value)}
              placeholder="如果已经有视频文稿，可以直接粘贴在这里，跳过抖音解析步骤"
              rows={6}
              className="w-full resize-none rounded-lg border border-gold-500/15 bg-base-700/60 px-4 py-2.5 text-sm leading-relaxed text-warm-100 placeholder:text-warm-500 outline-none transition-colors focus:border-gold-400/50"
            />
          </div>

          {error && (
            <div className="rounded-lg border border-red-500/30 bg-red-500/8 px-4 py-2.5 text-xs text-red-300">
              {error}
            </div>
          )}
        </div>

        <div className="mt-8 flex items-center justify-end gap-3">
          <button
            type="button"
            onClick={onClose}
            className="rounded-full px-5 py-2 text-sm tracking-widest text-warm-400 transition-colors hover:text-warm-100"
          >
            取消
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={submitting}
            className="btn-gold disabled:opacity-60"
          >
            {submitting ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" strokeWidth={2} />
                <span className="tracking-widest">提交中</span>
              </>
            ) : (
              <span className="tracking-widest">立即生成</span>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}

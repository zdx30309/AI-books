import { Check, Circle, Loader2, Pause } from "lucide-react";
import { publicTaskStep } from "@/utils/taskStatus";

interface Step {
  key: string;
  label: string;
  /** 触发本步的进度阈值（>= 即视为已开始） */
  start: number;
}

const BASE_STEPS: Step[] = [
  { key: "queue", label: "排队建库", start: 0 },
  { key: "fetch", label: "解析视频文案", start: 8 },
  { key: "writer", label: "生成初稿", start: 25 },
  { key: "polish", label: "第一轮润色", start: 50 },
  { key: "refine", label: "第二轮润色", start: 72 },
  { key: "format", label: "整理终稿", start: 87 },
  { key: "persist", label: "章节入库 / 写终稿", start: 92 },
];

const COMIC_STEPS: Step[] = [
  { key: "queue", label: "排队建库", start: 0 },
  { key: "fetch", label: "接收漫画短语", start: 8 },
  { key: "structure", label: "生成目录与短语结构", start: 25 },
  { key: "images", label: "封面与短语配图生成", start: 86 },
  { key: "persist", label: "章节入库 / 写终稿", start: 92 },
];

const DIRECT_WRITER_STEPS: Step[] = [
  { key: "queue", label: "排队建库", start: 0 },
  { key: "fetch", label: "解析原文", start: 8 },
  { key: "writer", label: "生成初稿", start: 25 },
  { key: "format", label: "整理终稿", start: 87 },
  { key: "persist", label: "章节入库 / 写终稿", start: 92 },
];

function withAigcStep(steps: Step[], required: boolean): Step[] {
  if (!required) return steps;
  return [
    ...steps.filter((step) => step.key !== "persist"),
    { key: "aigc", label: "朱雀检测 / 双模型质量审稿", start: 91 },
    { key: "persist", label: "双达标后入库 / 写终稿", start: 94 },
  ];
}

interface Props {
  progress: number;
  currentStep: string;
  status: string;
  category?: string | null;
  aigcRequired?: boolean;
}

export function WorkflowStepper({ progress, currentStep, status, category, aigcRequired = false }: Props) {
  const isFailed = status === "failed";
  const isManualReview = status === "manual_review";
  const isPaused = status === "paused" || isManualReview;
  const isCompleted = status === "completed" || (!isFailed && !isPaused && progress >= 100);
  const isComicWorkflow =
    ["comic_series", "healing_comic", "story_comic", "homophone_comic"].includes(category || "")
    || currentStep.includes("漫画");
  const isDirectWriterWorkflow =
    category === "classical_chinese_novel_style" ||
    category === "classical_chinese_reference_rewrite";
  const steps = isComicWorkflow
    ? COMIC_STEPS
    : isDirectWriterWorkflow
      ? withAigcStep(DIRECT_WRITER_STEPS, aigcRequired)
      : withAigcStep(BASE_STEPS, aigcRequired);
  const displayStep = publicTaskStep(currentStep, status, progress);

  return (
    <ol className="space-y-3">
      {steps.map((step, idx) => {
        const next = steps[idx + 1];
        // 已完成：下一步已经开始 / 整体完成
        const done = isCompleted || (next ? progress >= next.start : false);
        // 进行中：自身已开始但下一步未开始
        const active = !done && progress >= step.start;
        const paused = isPaused && active;
        // 失败：当前步骤定格在 active
        const failed = isFailed && active;

        return (
          <li key={step.key} className="flex items-start gap-3">
            <span
              className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full transition-colors ${
                done
                  ? "bg-gold-500/15 text-gold-300"
                  : failed
                    ? "bg-red-500/15 text-red-300"
                    : paused
                    ? "bg-gold-500/10 text-gold-200"
                    : active
                    ? "bg-gold-500/10 text-gold-200"
                    : "bg-warm-600/10 text-warm-500"
              }`}
            >
              {done ? (
                <Check className="h-3 w-3" strokeWidth={2.5} />
              ) : failed ? (
                <Circle className="h-2.5 w-2.5 fill-current" />
              ) : paused ? (
                <Pause className="h-3 w-3" strokeWidth={2} />
              ) : active ? (
                <Loader2 className="h-3 w-3 animate-spin" strokeWidth={2} />
              ) : (
                <Circle className="h-2 w-2 fill-current" />
              )}
            </span>
            <div className="flex-1 -mt-0.5">
              <div
                className={`text-sm tracking-wide ${
                  done
                    ? "text-warm-200"
                    : failed
                      ? "text-red-200"
                      : paused
                      ? "text-warm-100"
                      : active
                      ? "text-warm-100"
                      : "text-warm-500"
                }`}
              >
                {step.label}
              </div>
              {(active || paused) && displayStep && (
                <div className="mt-1 text-[11px] tracking-wide text-warm-400">
                  {failed ? "已停止" : isManualReview ? "正在处理" : paused ? "已暂停" : displayStep}
                </div>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

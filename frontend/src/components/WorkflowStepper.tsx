import { Check, Circle, Loader2, Pause } from "lucide-react";
import { AIGC_GATE_VISIBLE } from "@/config/featureFlags";

interface Step {
  key: string;
  label: string;
  /** 触发本步的进度阈值（>= 即视为已开始） */
  start: number;
}

const STEPS: Step[] = [
  { key: "queue", label: "排队建库", start: 0 },
  { key: "fetch", label: "解析视频文案", start: 8 },
  { key: "writer", label: "OpenAI 主模型生成初稿", start: 25 },
  { key: "polish", label: "Gemini 一润（按段落分块）", start: 50 },
  { key: "refine", label: "Gemini 二润（整篇微调）", start: 72 },
  { key: "format", label: "DeepSeek 终稿格式化", start: 87 },
  ...(AIGC_GATE_VISIBLE
    ? [{ key: "aigc", label: "朱雀检测 / 高风险段局部改写", start: 91 }]
    : []),
  { key: "persist", label: "章节入库 / 写终稿", start: AIGC_GATE_VISIBLE ? 94 : 92 },
];

const COMIC_STEPS: Step[] = [
  { key: "queue", label: "排队建库", start: 0 },
  { key: "fetch", label: "接收漫画短语", start: 8 },
  { key: "structure", label: "生成目录与短语结构", start: 25 },
  { key: "images", label: "封面与短语配图生成", start: 86 },
  ...(AIGC_GATE_VISIBLE
    ? [{ key: "aigc", label: "朱雀检测 / 高风险段局部改写", start: 91 }]
    : []),
  { key: "persist", label: "章节入库 / 写终稿", start: AIGC_GATE_VISIBLE ? 94 : 92 },
];

const DIRECT_WRITER_STEPS: Step[] = [
  { key: "queue", label: "排队建库", start: 0 },
  { key: "fetch", label: "解析原文", start: 8 },
  { key: "writer", label: "OpenAI 主模型生成初稿", start: 25 },
  { key: "format", label: "DeepSeek 终稿格式化", start: 87 },
  ...(AIGC_GATE_VISIBLE
    ? [{ key: "aigc", label: "朱雀检测 / 高风险段局部改写", start: 91 }]
    : []),
  { key: "persist", label: "章节入库 / 写终稿", start: AIGC_GATE_VISIBLE ? 94 : 92 },
];

interface Props {
  progress: number;
  currentStep: string;
  status: string;
  category?: string | null;
}

export function WorkflowStepper({ progress, currentStep, status, category }: Props) {
  const isFailed = status === "failed";
  const isPaused = status === "paused";
  const isCompleted = status === "completed" || (!isFailed && !isPaused && progress >= 100);
  const isComicWorkflow =
    category === "comic_series" || category === "healing_comic" || currentStep.includes("漫画");
  const isDirectWriterWorkflow =
    category === "classical_chinese_novel_style" ||
    category === "classical_chinese_reference_rewrite";
  const steps = isComicWorkflow
    ? COMIC_STEPS
    : isDirectWriterWorkflow
      ? DIRECT_WRITER_STEPS
      : STEPS;

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
              {(active || paused) && currentStep && (
                <div className="mt-1 text-[11px] tracking-wide text-warm-400">
                  {failed ? "已停止" : paused ? "已暂停" : currentStep}
                </div>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, RefreshCw, Loader2 } from "lucide-react";
import { api, ApiError } from "@/api/client";
import type { SystemHealth } from "@/api/types";
import { toast } from "@/components/Toaster";

interface Props {
  taskId: string;
  thresholdSeconds?: number;
  progress: number;
  status: string;
  currentStep?: string;
  onRetried?: () => void;
}

const LONG_RUNNING_STEP_PATTERNS = [
  "OpenAI",
  "Gemini",
  "DeepSeek",
  "格式化",
  "封面",
  "生成",
  "润",
];

export function StuckBanner({
  taskId,
  thresholdSeconds = 900,
  progress,
  status,
  currentStep = "",
  onRetried,
}: Props) {
  const [health, setHealth] = useState<SystemHealth | null>(null);
  const [healthLoading, setHealthLoading] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const lastProgressRef = useRef({ progress, changedAt: Date.now() });

  useEffect(() => {
    if (progress !== lastProgressRef.current.progress) {
      lastProgressRef.current = { progress, changedAt: Date.now() };
      setHealth(null);
    }
  }, [progress]);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const idleSeconds = (now - lastProgressRef.current.changedAt) / 1000;
  const isKnownLongRunningStep = useMemo(
    () =>
      status === "processing" &&
      LONG_RUNNING_STEP_PATTERNS.some((pattern) => currentStep.includes(pattern)),
    [currentStep, status],
  );
  const effectiveThreshold = isKnownLongRunningStep
    ? Math.max(thresholdSeconds, 1800)
    : thresholdSeconds;
  const isStuck =
    (status === "pending" || status === "processing") &&
    idleSeconds >= effectiveThreshold;

  useEffect(() => {
    if (!isStuck || health || healthLoading) return;
    setHealthLoading(true);
    api
      .systemHealth()
      .then(setHealth)
      .catch(() => undefined)
      .finally(() => setHealthLoading(false));
  }, [isStuck, health, healthLoading]);

  useEffect(() => {
    if (!isStuck) return;
    const timer = window.setInterval(() => {
      api.systemHealth().then(setHealth).catch(() => undefined);
    }, 30000);
    return () => window.clearInterval(timer);
  }, [isStuck]);

  if (!isStuck) return null;

  const healthTimedOut = Boolean(health?.celery_error?.includes("TimeoutError"));
  const noWorker =
    health &&
    !health.celery_alive &&
    !healthTimedOut &&
    (status === "pending" || progress <= 1);
  const workerOk = health?.celery_alive;
  const workerBusyOrUnknown = health && !workerOk && !noWorker;

  const retry = async () => {
    setRetrying(true);
    try {
      await api.retryTask(taskId);
      onRetried?.();
    } catch (e) {
      const msg = e instanceof ApiError ? e.detail : "重试失败";
      toast.error(`重试失败：${msg}`);
    } finally {
      setRetrying(false);
    }
  };

  return (
    <div className="mb-6 overflow-hidden rounded-2xl border border-amber-500/30 bg-amber-500/5 backdrop-blur-md">
      <div className="flex items-start gap-4 p-6">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-amber-500/15">
          <AlertTriangle
            className="h-4 w-4 text-amber-300"
            strokeWidth={1.8}
          />
        </div>

        <div className="flex-1">
          <h3 className="font-serif text-base tracking-wide text-warm-100">
            任务耗时较久
          </h3>

          {healthLoading && !health && (
            <p className="mt-2 flex items-center gap-2 text-xs tracking-wide text-warm-400">
              <Loader2 className="h-3 w-3 animate-spin" strokeWidth={1.5} />
              正在检测后端 Celery 状态...
            </p>
          )}

          {noWorker && (
            <p className="mt-2 text-xs leading-relaxed tracking-wide text-warm-300">
              后端没有检测到可用的 Celery worker，任务可能还在队列里等待处理。
              服务器部署环境请检查 Docker worker 服务是否运行。
            </p>
          )}

          {workerOk && (
            <p className="mt-2 text-xs leading-relaxed tracking-wide text-warm-300">
              Worker 在线。当前步骤已经 {Math.floor(idleSeconds)}
              秒没有进度变化，可能是模型调用或封面生成耗时较久。
              {health.celery_workers.length > 0 && (
                <span className="ml-2 text-[10px] text-warm-500">
                  Workers: {health.celery_workers.join(", ")}
                </span>
              )}
            </p>
          )}

          {workerBusyOrUnknown && (
            <p className="mt-2 text-xs leading-relaxed tracking-wide text-warm-300">
              健康检查暂时没有返回，通常是 worker 正在处理长任务或队列较忙。
              如果任务已经进入 OpenAI、Gemini、DeepSeek 或封面阶段，请先等待当前请求返回。
            </p>
          )}

          {!health && !healthLoading && (
            <p className="mt-2 text-xs leading-relaxed tracking-wide text-warm-300">
              当前步骤已经 {Math.floor(idleSeconds)}
              秒没有进度变化。长模型调用期间前端可能暂时看不到进度更新。
            </p>
          )}

          <div className="mt-4 flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={retry}
              disabled={retrying}
              className="inline-flex items-center gap-2 rounded-full border border-amber-400/40 bg-amber-500/10 px-4 py-1.5 text-xs tracking-widest text-amber-100 transition-colors hover:border-amber-300 hover:bg-amber-500/20 disabled:opacity-60"
            >
              {retrying ? (
                <Loader2 className="h-3 w-3 animate-spin" strokeWidth={2} />
              ) : (
                <RefreshCw className="h-3 w-3" strokeWidth={1.8} />
              )}
              <span>{retrying ? "重新投递中" : "重试任务"}</span>
            </button>

            <button
              type="button"
              onClick={() => {
                setHealthLoading(true);
                api
                  .systemHealth()
                  .then(setHealth)
                  .catch(() => undefined)
                  .finally(() => setHealthLoading(false));
              }}
              className="text-xs tracking-widest text-warm-400 transition-colors hover:text-warm-100"
            >
              重新检测
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

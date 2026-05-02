import { useEffect, useRef, useState } from "react";
import { AlertTriangle, RefreshCw, Terminal, Loader2 } from "lucide-react";
import { api, ApiError } from "@/api/client";
import type { SystemHealth } from "@/api/types";

interface Props {
  taskId: string;
  /** 任务进度多久没有变化后视为"卡住"。默认 120s。 */
  thresholdSeconds?: number;
  /** 任务进度（用来判断是否已经动起来） */
  progress: number;
  /** 任务状态 */
  status: string;
  onRetried?: () => void;
}

export function StuckBanner({
  taskId,
  thresholdSeconds = 120,
  progress,
  status,
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

  // 任务处于活动态但进度长期不变化时展示。覆盖 0%、66% 等所有僵尸进度。
  const isStuck =
    (status === "pending" || status === "processing") &&
    idleSeconds >= thresholdSeconds;

  // 卡住后开始拉系统健康
  useEffect(() => {
    if (!isStuck || health || healthLoading) return;
    setHealthLoading(true);
    api
      .systemHealth()
      .then(setHealth)
      .catch(() => undefined)
      .finally(() => setHealthLoading(false));
  }, [isStuck, health, healthLoading]);

  // 每 8s 复查一次
  useEffect(() => {
    if (!isStuck) return;
    const t = window.setInterval(() => {
      api.systemHealth().then(setHealth).catch(() => undefined);
    }, 8000);
    return () => window.clearInterval(t);
  }, [isStuck]);

  if (!isStuck) return null;

  const noWorker = health && !health.celery_alive;
  const workerOk = health && health.celery_alive;

  const retry = async () => {
    setRetrying(true);
    try {
      await api.retryTask(taskId);
      onRetried?.();
    } catch (e) {
      const msg = e instanceof ApiError ? e.detail : "重试失败";
      alert(`重试失败：${msg}`);
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
            任务似乎卡住了
          </h3>

          {healthLoading && !health && (
            <p className="mt-2 flex items-center gap-2 text-xs tracking-wide text-warm-400">
              <Loader2 className="h-3 w-3 animate-spin" strokeWidth={1.5} />
              正在检测后端 Celery 状态…
            </p>
          )}

          {noWorker && (
            <>
              <p className="mt-2 text-xs leading-relaxed tracking-wide text-warm-300">
                后端检测到 <span className="text-amber-300">Celery worker 没有运行</span>
                ，所以任务一直在队列里没人处理。请在新终端启动 worker：
              </p>
              <div className="mt-3 flex items-start gap-2 rounded-lg border border-amber-500/15 bg-base-900/60 px-4 py-3">
                <Terminal
                  className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-300"
                  strokeWidth={1.5}
                />
                <code className="select-all text-xs leading-relaxed text-amber-100">
                  cd E:/video-plan/backend{"\n"}
                  celery -A celery_app.celery_app worker --pool=solo --loglevel=info
                </code>
              </div>
              <p className="mt-3 text-[11px] tracking-wide text-warm-400">
                启动 worker 后，点下面「重试任务」按钮即可重新投递。
              </p>
            </>
          )}

          {workerOk && (
            <p className="mt-2 text-xs leading-relaxed tracking-wide text-warm-300">
              Worker 在线，但任务进度已经 {Math.floor(idleSeconds)} 秒没有变化。
              这通常是早先重启或断线留下的僵尸进度，建议点「重试任务」重新投递一次。
              {health.celery_workers.length > 0 && (
                <span className="ml-2 text-[10px] text-warm-500">
                  Workers: {health.celery_workers.join(", ")}
                </span>
              )}
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

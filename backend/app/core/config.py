"""
Gravity Singularity - 全局配置中心
通过 pydantic-settings 从 .env 加载并强类型校验所有环境变量。
"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # --- 应用 ---
    APP_NAME: str = "GravitySingularity"
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"

    # --- 数据库 (Supabase Postgres) ---
    DATABASE_URL: str = (
        "postgresql+psycopg2://postgres:postgres@localhost:5432/postgres?sslmode=require"
    )
    DATABASE_POOL_SIZE: int = 60
    DATABASE_MAX_OVERFLOW: int = 140
    DATABASE_POOL_TIMEOUT: int = 120

    # --- Redis / Celery (Upstash 兼容) ---
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/0"
    CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP: bool = True
    CELERY_BROKER_HEARTBEAT: int = 30
    CELERY_BROKER_POOL_LIMIT: int = 0
    CELERY_BROKER_SOCKET_CONNECT_TIMEOUT: int = 60
    CELERY_BROKER_SOCKET_TIMEOUT: int = 0
    CELERY_REDIS_HEALTH_CHECK_INTERVAL: int = 10
    CELERY_REDIS_MAX_RETRIES: int = 20
    CELERY_WORKER_CANCEL_LONG_RUNNING_TASKS_ON_CONNECTION_LOSS: bool = False
    CELERY_WORKER_CONCURRENCY: int = 200
    MAX_ACTIVE_GENERATION_TASKS: int = 250
    GENERATION_TASK_MAX_RETRIES: int = 5
    GENERATION_TASK_RETRY_BACKOFF_SECONDS: int = 60
    STALE_TASK_WATCHDOG_ENABLED: bool = True
    STALE_TASK_WATCHDOG_INTERVAL_SECONDS: int = 120
    TASK_RUN_HEARTBEAT_INTERVAL_SECONDS: int = 60
    STALE_PROCESSING_REQUEUE_SECONDS: int = 900
    FAILED_TASK_AUTO_RECOVERY_ENABLED: bool = True
    FAILED_TASK_AUTO_RECOVERY_SECONDS: int = 600
    FAILED_TASK_AUTO_RECOVERY_MAX_ATTEMPTS: int = 24
    PAID_API_GATE_WAIT_TIMEOUT_SECONDS: int = 14400
    PAID_API_GATE_TTL_SECONDS: int = 600

    # --- 主笔模型路由 ---
    WRITER_PROVIDER: str = "openai"  # 可选 "openai" / "deepseek" / "gemini"

    # --- OpenAI 主笔配置 ---
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_HTTPS_PROXY: str = ""
    OPENAI_WRITER_MODEL: str = "gpt-5.5"
    OPENAI_WRITER_REASONING_EFFORT: str = "xhigh"  # none/low/medium/high/xhigh
    OPENAI_WRITER_VERBOSITY: str = "medium"  # low/medium/high
    OPENAI_WRITER_MAX_OUTPUT_TOKENS: int = 60000
    OPENAI_WRITER_MAX_RETRIES: int = 3
    OPENAI_WRITER_RETRY_BACKOFF_SECONDS: float = 8.0
    OPENAI_WRITER_STREAM: bool = False
    OPENAI_WRITER_MAX_CONCURRENT_CALLS: int = 16

    # --- OpenAI 终审配置（保留，但默认关闭，因为主笔已是 OpenAI） ---
    OPENAI_AUDITOR_ENABLED: bool = False
    OPENAI_AUDITOR_MODEL: str = "gpt-5.5"

    # --- DeepSeek V4 (OpenAI 兼容接口) ---
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com/v1"
    DEEPSEEK_MODEL: str = "deepseek-chat"
    DEEPSEEK_FORMAT_MAX_TOKENS: int = 20000
    DEEPSEEK_REQUEST_TIMEOUT: int = 180
    DEEPSEEK_MAX_RETRIES: int = 4
    DEEPSEEK_FORMAT_CALL_TIMEOUT: int = 1800
    DEEPSEEK_FORMAT_MAX_CONCURRENT_CALLS: int = 16

    # --- Gemini / Google Gen AI ---
    GOOGLE_API_KEY: str = ""
    GOOGLE_GENAI_USE_VERTEXAI: bool = True
    GOOGLE_CLOUD_PROJECT: str = "video-plan2"
    GOOGLE_CLOUD_LOCATION: str = "global"
    GOOGLE_APPLICATION_CREDENTIALS: str = ""
    GOOGLE_HTTPS_PROXY: str = ""
    GEMINI_MODEL: str = "gemini-3-flash-preview"
    GEMINI_FALLBACK_TO_API_KEY: bool = True
    GEMINI_REQUEST_TIMEOUT: int = 180
    GEMINI_REFINE_REQUEST_TIMEOUT: int = 300
    GEMINI_MAX_RETRIES: int = 5
    GEMINI_THINKING_BUDGET: int = 0
    GEMINI_MAX_CONCURRENT_CALLS: int = 8
    GEMINI_POLISH_CONCURRENCY: int = 1
    GEMINI_POLISH_CALL_TIMEOUT: int = 0
    GEMINI_REFINE_CALL_TIMEOUT: int = 0
    GEMINI_POLISH_MAX_OUTPUT_TOKENS: int = 4096
    GEMINI_REFINE_MAX_OUTPUT_TOKENS: int = 60000

    # --- 图像生成 ---
    IMAGE_API_KEY: str = ""
    IMAGE_BASE_URL: str = "https://api.openai.com/v1"
    IMAGE_MODEL: str = "gpt-image-2"
    IMAGE_SIZE: str = "1024x1536"
    IMAGE_QUALITY: str = ""
    IMAGE_RESPONSE_FORMAT: str = ""
    IMAGE_OUTPUT_FORMAT: str = ""
    IMAGE_MAX_CONCURRENT_CALLS: int = 4
    IMAGE_API_MAX_RETRIES: int = 1
    IMAGE_GATE_WAIT_TIMEOUT_SECONDS: int = 30
    COVER_WORKFLOW_ENABLED: bool = True
    COVER_BOOK_TASK_GENERATION_ENABLED: bool = False
    COVER_TASK_MAX_RETRIES: int = 360
    COVER_TASK_RETRY_SECONDS: int = 60
    COVER_JOB_MAX_ROWS: int = 500
    COVER_DUPLICATE_WINDOW_DAYS: int = 15
    COVER_REFERENCE_FETCH_ENABLED: bool = True
    COVER_REFERENCE_CACHE_ENABLED: bool = False
    COVER_REFERENCE_DOWNLOAD_IMAGES: bool = False
    COVER_REFERENCE_CACHE_DIR: str = "dangdang_book_covers"
    COVER_REFERENCE_TIMEOUT_SECONDS: int = 12
    COVER_DEEPSEEK_STYLE_ANALYSIS_ENABLED: bool = True
    COVER_DEEPSEEK_STYLE_ANALYSIS_MAX_TOKENS: int = 1200

    # --- 轻抖 API (视频文案提取) ---
    QINGDOU_API_KEY: str = ""
    QINGDOU_BASE_URL: str = "https://www.qingdou.vip/web/api/commitGetTextTask"
    QINGDOU_MAX_VIDEO_DURATION: int = 0
    QINGDOU_POLL_INTERVAL_SECONDS: float = 3.0
    QINGDOU_POLL_TIMEOUT_SECONDS: int = 300
    QINGDOU_MAX_CONCURRENT_TASKS: int = 30
    QINGDOU_SERIAL_LOCK_ENABLED: bool = False
    QINGDOU_LOCK_WAIT_TIMEOUT_SECONDS: int = 1800
    QINGDOU_LOCK_TTL_SECONDS: int = 900
    QINGDOU_BUSY_RETRY_SECONDS: float = 10.0
    QINGDOU_BUSY_MAX_ATTEMPTS: int = 30
    QINGDOU_HTTP_MAX_RETRIES: int = 4
    QINGDOU_HTTP_RETRY_BACKOFF_SECONDS: float = 2.0

    # --- YouTube transcript extraction (free providers, no paid model API) ---
    YOUTUBE_TRANSCRIPT_ENABLED: bool = True
    YOUTUBE_TRANSCRIPT_PROVIDERS: str = "youtube_transcript_api,yt_dlp"
    YOUTUBE_TRANSCRIPT_LANGUAGES: str = "zh-Hans,zh-Hant,zh,en,en-US,en-GB"
    YOUTUBE_TRANSCRIPT_TOTAL_TIMEOUT_SECONDS: int = 10
    YOUTUBE_TRANSCRIPT_TIMEOUT_SECONDS: int = 20
    YOUTUBE_TRANSCRIPT_MIN_CHARS: int = 80
    YOUTUBE_HTTPS_PROXY: str = ""
    YOUTUBE_COOKIES_FILE: str = ""
    YOUTUBE_CACHE_DIR: str = "backend/output/youtube_cache"

    # --- 5000字章节墙 (业务硬性指标) ---
    CHAPTER_TARGET_WORDS: int = 5000
    CHAPTER_MIN_WORDS: int = 4500
    SUB_SCENE_OVERLAP_WORDS: int = 800
    MAX_PATCH_ROUNDS: int = 3

    # --- LLM 调用 ---
    LLM_OUTLINE_TEMPERATURE: float = 0.2
    LLM_WRITING_TEMPERATURE: float = 0.75
    LLM_REQUEST_TIMEOUT: int = 120
    LLM_MAX_RETRIES: int = 3

    # --- 鉴权 (JWT) ---
    JWT_SECRET: str = "change-me-please-very-long-random-secret-key-for-jwt-signing-only"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRES_HOURS: int = 24 * 7   # 默认 7 天

    # --- CORS ---
    CORS_ALLOW_ORIGINS: str = "*"

    @property
    def cors_origins_list(self) -> list[str]:
        raw = (self.CORS_ALLOW_ORIGINS or "").strip()
        if raw == "*" or not raw:
            return ["*"]
        return [o.strip() for o in raw.split(",") if o.strip()]


settings = Settings()

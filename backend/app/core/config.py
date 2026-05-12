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

    # --- OpenAI 终审配置（保留，但默认关闭，因为主笔已是 OpenAI） ---
    OPENAI_AUDITOR_ENABLED: bool = False
    OPENAI_AUDITOR_MODEL: str = "gpt-5.5"

    # --- DeepSeek V4 (OpenAI 兼容接口) ---
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com/v1"
    DEEPSEEK_MODEL: str = "deepseek-chat"
    DEEPSEEK_FORMAT_MAX_TOKENS: int = 20000
    DEEPSEEK_REQUEST_TIMEOUT: int = 180
    DEEPSEEK_MAX_RETRIES: int = 1
    DEEPSEEK_FORMAT_CALL_TIMEOUT: int = 1800

    # --- Gemini / Google Gen AI ---
    GOOGLE_API_KEY: str = ""
    GOOGLE_GENAI_USE_VERTEXAI: bool = True
    GOOGLE_CLOUD_PROJECT: str = "video-plan2"
    GOOGLE_CLOUD_LOCATION: str = "global"
    GOOGLE_APPLICATION_CREDENTIALS: str = ""
    GOOGLE_HTTPS_PROXY: str = ""
    GEMINI_MODEL: str = "gemini-3-flash-preview"
    GEMINI_FALLBACK_TO_API_KEY: bool = True
    GEMINI_REQUEST_TIMEOUT: int = 30
    GEMINI_REFINE_REQUEST_TIMEOUT: int = 240
    GEMINI_MAX_RETRIES: int = 1
    GEMINI_THINKING_BUDGET: int = 0
    GEMINI_POLISH_CALL_TIMEOUT: int = 45
    GEMINI_REFINE_CALL_TIMEOUT: int = 180
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

    # --- 轻抖 API (视频文案提取) ---
    QINGDOU_API_KEY: str = ""
    QINGDOU_BASE_URL: str = "https://www.qingdou.vip/web/api/commitGetTextTask"
    QINGDOU_MAX_VIDEO_DURATION: int = 0
    QINGDOU_POLL_INTERVAL_SECONDS: float = 3.0
    QINGDOU_POLL_TIMEOUT_SECONDS: int = 300
    QINGDOU_MAX_CONCURRENT_TASKS: int = 10
    QINGDOU_SERIAL_LOCK_ENABLED: bool = False
    QINGDOU_LOCK_WAIT_TIMEOUT_SECONDS: int = 1800
    QINGDOU_LOCK_TTL_SECONDS: int = 900
    QINGDOU_BUSY_RETRY_SECONDS: float = 10.0
    QINGDOU_BUSY_MAX_ATTEMPTS: int = 30

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

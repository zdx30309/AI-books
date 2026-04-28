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

    # --- 主笔模型路由 ---
    WRITER_PROVIDER: str = "openai"  # 可选 "openai" 或 "deepseek"

    # --- OpenAI 主笔配置 ---
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_WRITER_MODEL: str = "gpt-5.5"
    OPENAI_WRITER_REASONING_EFFORT: str = "xhigh"  # none/low/medium/high/xhigh
    OPENAI_WRITER_VERBOSITY: str = "medium"  # low/medium/high
    OPENAI_WRITER_MAX_OUTPUT_TOKENS: int = 60000

    # --- OpenAI 终审配置（保留，但默认关闭，因为主笔已是 OpenAI） ---
    OPENAI_AUDITOR_ENABLED: bool = False
    OPENAI_AUDITOR_MODEL: str = "gpt-5.5"

    # --- DeepSeek V4 (OpenAI 兼容接口) ---
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com/v1"
    DEEPSEEK_MODEL: str = "deepseek-chat"

    # --- 图像生成 ---
    IMAGE_API_KEY: str = ""
    IMAGE_BASE_URL: str = "https://api.openai.com/v1"
    IMAGE_MODEL: str = "gpt-image-1"
    IMAGE_SIZE: str = "1024x1536"

    # --- 轻抖 API (视频文案提取) ---
    QINGDOU_API_KEY: str = ""
    QINGDOU_BASE_URL: str = "https://www.qingdou.vip/web/api/commitGetTextTask"
    QINGDOU_MAX_VIDEO_DURATION: int = 900
    QINGDOU_POLL_INTERVAL_SECONDS: float = 3.0
    QINGDOU_POLL_TIMEOUT_SECONDS: int = 300

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

    # --- CORS ---
    CORS_ALLOW_ORIGINS: str = "*"

    @property
    def cors_origins_list(self) -> list[str]:
        raw = (self.CORS_ALLOW_ORIGINS or "").strip()
        if raw == "*" or not raw:
            return ["*"]
        return [o.strip() for o in raw.split(",") if o.strip()]


settings = Settings()

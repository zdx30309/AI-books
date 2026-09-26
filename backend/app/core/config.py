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
    DATABASE_CONNECT_TIMEOUT: int = 15
    DATABASE_POOL_RECYCLE_SECONDS: int = 300

    # --- Redis / Celery (Upstash 兼容) ---
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/0"
    # Paid upstream concurrency gates are isolated from Celery/Upstash so a
    # queue-side rate limit cannot block or duplicate model requests.
    PAID_API_GATE_REDIS_URL: str = ""
    CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP: bool = True
    CELERY_BROKER_HEARTBEAT: int = 30
    CELERY_BROKER_POOL_LIMIT: int = 0
    CELERY_BROKER_SOCKET_CONNECT_TIMEOUT: int = 60
    CELERY_BROKER_SOCKET_TIMEOUT: int = 0
    CELERY_REDIS_HEALTH_CHECK_INTERVAL: int = 10
    CELERY_REDIS_MAX_RETRIES: int = 20
    CELERY_WORKER_CANCEL_LONG_RUNNING_TASKS_ON_CONNECTION_LOSS: bool = False
    # Queue many jobs, but keep worker threads bounded; paid-call concurrency
    # is controlled separately by the distributed provider gates.
    CELERY_WORKER_CONCURRENCY: int = 12
    CELERY_MAINTENANCE_WORKER_CONCURRENCY: int = 1
    CELERY_MAINTENANCE_QUEUE: str = "maintenance"
    CELERY_TASK_TIME_LIMIT_SECONDS: int = 14400
    CELERY_TASK_SOFT_TIME_LIMIT_SECONDS: int = 13800
    MAX_ACTIVE_GENERATION_TASKS: int = 250
    GENERATION_TASK_MAX_RETRIES: int = 5
    GENERATION_TASK_RETRY_BACKOFF_SECONDS: int = 60
    STALE_TASK_WATCHDOG_ENABLED: bool = True
    STALE_TASK_WATCHDOG_INTERVAL_SECONDS: int = 120
    TASK_RUN_HEARTBEAT_INTERVAL_SECONDS: int = 60
    STALE_PROCESSING_REQUEUE_SECONDS: int = 900
    # A pending task with no active run is normally waiting for a Celery
    # message.  If that message/lease disappeared, the maintenance worker may
    # safely redispatch it after this grace period; the database run claim is
    # still the final duplicate-execution barrier.
    PENDING_TASK_ORPHAN_REQUEUE_SECONDS: int = 180
    DISPATCH_GUARD_STALE_SECONDS: int = 900
    FAILED_TASK_AUTO_RECOVERY_ENABLED: bool = True
    FAILED_TASK_AUTO_RECOVERY_SECONDS: int = 600
    FAILED_TASK_AUTO_RECOVERY_MAX_ATTEMPTS: int = 24
    PAID_API_GATE_WAIT_TIMEOUT_SECONDS: int = 14400
    PAID_API_GATE_TTL_SECONDS: int = 600
    # Short Vertex/GenAI request-rate throttles must not become a 30-minute
    # global production pause.  Billing/dunning failures keep the long guard.
    PAID_API_RATE_LIMIT_TTL_SECONDS: int = 60
    # In production, block paid calls if the shared breaker cannot be read.
    # Development/test environments may leave this unset to avoid requiring
    # a Redis instance for local pure-function tests.
    PAID_API_GUARD_FAIL_CLOSED: bool | None = None

    # --- 主笔模型路由 ---
    WRITER_PROVIDER: str = "openai"  # 可选 "openai" / "deepseek" / "gemini"

    # --- OpenAI 主笔配置 ---
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_HTTPS_PROXY: str = ""
    OPENAI_WRITER_MODEL: str = "gpt-5.6-sol"
    OPENAI_WRITER_REASONING_EFFORT: str = "xhigh"  # none/low/medium/high/xhigh
    OPENAI_WRITER_VERBOSITY: str = "medium"  # low/medium/high
    OPENAI_WRITER_MAX_OUTPUT_TOKENS: int = 0
    CLASSICAL_WRITER_MAX_OUTPUT_TOKENS: int = 0
    OPENAI_WRITER_MAX_RETRIES: int = 3
    OPENAI_WRITER_RETRY_BACKOFF_SECONDS: float = 8.0
    OPENAI_WRITER_STREAM: bool = False
    OPENAI_PROMPT_CACHE_RETENTION: str = "24h"
    OPENAI_WRITER_MAX_CONCURRENT_CALLS: int = 16
    FINANCE_WEB_SEARCH_MODEL: str = ""
    FINANCE_WEB_SEARCH_TIMEOUT_SECONDS: int = 240
    FINANCE_WEB_SEARCH_CONTEXT_SIZE: str = "medium"
    OPENAI_OCR_MODEL: str = ""
    OPENAI_OCR_MAX_OUTPUT_TOKENS: int = 5000

    # --- Testing Endpoint (OpenAI ChatGPT API only) ---
    TESTING_ENDPOINT_OPENAI_MODEL: str = ""
    TESTING_ENDPOINT_OPENAI_REASONING_EFFORT: str = ""
    TESTING_ENDPOINT_OPENAI_VERBOSITY: str = ""
    TESTING_ENDPOINT_OPENAI_MAX_OUTPUT_TOKENS: int = 0
    TESTING_ENDPOINT_OPENAI_TIMEOUT_SECONDS: int = 1800
    TESTING_ENDPOINT_BOOK_CHUNK_CHARS: int = 50_000
    TESTING_ENDPOINT_STYLE_NOTE_MAX_OUTPUT_TOKENS: int = 6000
    TESTING_ENDPOINT_STYLE_PROFILE_MAX_OUTPUT_TOKENS: int = 8000
    TESTING_ENDPOINT_VIDEO_MAX_CHARS: int = 160_000
    TESTING_ENDPOINT_MAX_VIDEO_LINKS: int = 50
    TESTING_ENDPOINT_YOUTUBE_TRANSCRIPT_PROVIDERS: str = "youtube_transcript_api,youtube_transcript_ai,pytubefix,yt_dlp,openai_audio"

    # --- OpenAI 终审配置（保留，但默认关闭，因为主笔已是 OpenAI） ---
    OPENAI_AUDITOR_ENABLED: bool = False
    OPENAI_AUDITOR_MODEL: str = "gpt-5.6-sol"

    # --- DeepSeek V4 (OpenAI 兼容接口) ---
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com/v1"
    DEEPSEEK_MODEL: str = "deepseek-v4-flash"
    # 主笔走 DeepSeek 时（WRITER_PROVIDER=deepseek）使用的模型与输出上限
    DEEPSEEK_WRITER_MODEL: str = "deepseek-flash"
    DEEPSEEK_WRITER_MAX_OUTPUT_TOKENS: int = 0
    DEEPSEEK_FORMAT_MAX_TOKENS: int = 0
    CLASSICAL_DEEPSEEK_FORMAT_MAX_TOKENS: int = 0
    DEEPSEEK_REQUEST_TIMEOUT: int = 180
    DEEPSEEK_MAX_RETRIES: int = 4
    DEEPSEEK_FORMAT_CALL_TIMEOUT: int = 1800
    DEEPSEEK_FORMAT_MAX_CONCURRENT_CALLS: int = 16
    # 公版古文白话固定由 OpenAI gpt-5.6-sol 原生执行渲染后的完整 Prompt。
    CLASSICAL_TRANSLATION_WRITER_PROVIDER: str = "openai"

    # --- Backend polish/refine provider ---
    # Keep the historical Gemini-named frontend stages while allowing the
    # backend to route them through a dedicated Claude relay.
    BACKEND_POLISH_PROVIDER: str = "gemini"
    BACKEND_REFINE_PROVIDER: str = "gemini"
    CLAUDE_API_KEY: str = ""
    CLAUDE_BASE_URL: str = "https://anyrouter.top/v1"
    # AnyRouter exposes this account's Opus model under ``claude-opus-5``.
    # Keep the value configurable so a relay-side model rename never requires
    # changing business logic.
    CLAUDE_MODEL: str = "claude-opus-5"
    # AnyRouter requires the Anthropic 1M-context beta flag for the Opus 5
    # route. Leave it configurable so an operator can disable the beta without
    # changing business logic if the relay changes its header contract.
    CLAUDE_ANTHROPIC_BETA: str = "context-1m-2025-08-07"
    CLAUDE_HTTPS_PROXY: str = ""
    CLAUDE_REQUEST_TIMEOUT: int = 1800
    CLAUDE_MAX_OUTPUT_TOKENS: int = 0
    CLAUDE_MAX_CONCURRENT_CALLS: int = 1

    # --- xAI / Grok (only used by the dedicated report-review page) ---
    XAI_API_KEY: str = ""
    XAI_BASE_URL: str = "https://api.x.ai/v1"
    XAI_HTTPS_PROXY: str = ""
    XAI_REQUEST_TIMEOUT: int = 600
    XAI_MAX_RETRIES: int = 2

    # --- Gemini / Google Gen AI ---
    GOOGLE_API_KEY: str = ""
    GOOGLE_GENAI_USE_VERTEXAI: bool = True
    GOOGLE_CLOUD_PROJECT: str = "video-plan2"
    GOOGLE_CLOUD_LOCATION: str = "global"
    GOOGLE_APPLICATION_CREDENTIALS: str = ""
    GOOGLE_HTTPS_PROXY: str = ""
    # Vertex AI publishes the requested Gemini 3.1 Flash tier as Flash-Lite.
    GEMINI_MODEL: str = "gemini-3.1-flash-lite"
    GEMINI_FALLBACK_TO_API_KEY: bool = True
    GEMINI_REQUEST_TIMEOUT: int = 180
    GEMINI_REFINE_REQUEST_TIMEOUT: int = 300
    GEMINI_MAX_RETRIES: int = 5
    GEMINI_THINKING_BUDGET: int = 0
    GEMINI_THINKING_LEVEL: str = "medium"
    GEMINI_MAX_CONCURRENT_CALLS: int = 8
    # Vertex may return RESOURCE_EXHAUSTED for a short request-rate window even
    # when the project is billed correctly.  Keep the existing model/workflow,
    # but pace starts across worker threads so the cooldown can recover.
    GEMINI_MIN_REQUEST_INTERVAL_SECONDS: float = 30.0
    GEMINI_POLISH_CONCURRENCY: int = 1
    GEMINI_POLISH_CALL_TIMEOUT: int = 0
    GEMINI_REFINE_CALL_TIMEOUT: int = 0
    GEMINI_POLISH_MAX_OUTPUT_TOKENS: int = 0
    GEMINI_REFINE_MAX_OUTPUT_TOKENS: int = 0

    # --- 全局朱雀 AI 文本检测闸门 ---
    AIGC_GATE_ENABLED: bool = False
    AIGC_GATE_FAIL_CLOSED: bool = True
    AIGC_GATE_THRESHOLD: int = 50
    AIGC_GATE_MAX_REWRITE_ROUNDS: int = 2
    AIGC_GATE_CHUNK_MIN_CHARS: int = 350
    AIGC_GATE_CHUNK_MAX_CHARS: int = 1800
    AIGC_GATE_DETECT_CONCURRENCY: int = 4
    AIGC_GATE_REWRITE_CONCURRENCY: int = 2
    AIGC_GATE_REWRITE_TEMPERATURE: float = 0.85
    AIGC_POLICY_VERSION: str = "v2"
    AIGC_OPENAI_MODEL: str = ""
    AIGC_OPENAI_REASONING_EFFORT: str = "low"
    AIGC_OPENAI_SERVICE_TIER: str = "flex"
    TENCENTCLOUD_SECRET_ID: str = ""
    TENCENTCLOUD_SECRET_KEY: str = ""
    TENCENT_AIGC_BIZ_TYPE: str = ""
    TENCENT_AIGC_REGION: str = "ap-beijing"

    # --- 独立朱雀报告审稿台（不影响全局闸门和历史任务） ---
    AIGC_REVIEW_REWRITE_PROVIDER: str = "deepseek"
    AIGC_REVIEW_REWRITE_MODEL: str = "deepseek-v4-flash"
    AIGC_REVIEW_GROK_MODEL: str = "grok-4.5"
    AIGC_REVIEW_GROK_REASONING_EFFORT: str = "low"
    AIGC_REVIEW_GROK_MAX_OUTPUT_TOKENS: int = 12_000
    AIGC_REVIEW_GROK_INPUT_USD_PER_M: float = 2.0
    AIGC_REVIEW_GROK_CACHED_INPUT_USD_PER_M: float = 0.5
    AIGC_REVIEW_GROK_OUTPUT_USD_PER_M: float = 6.0
    AIGC_REVIEW_USD_CNY_RATE: float = 7.2
    AIGC_REVIEW_AUDIT_MODEL: str = "gemini-3.1-flash-lite"
    AIGC_REVIEW_MAX_REWRITE_ATTEMPTS: int = 1
    AIGC_REVIEW_HUMAN_TARGET: float = 25.0
    AIGC_REVIEW_EDITORIAL_UNIT_MIN_CHARS: int = 600
    AIGC_REVIEW_EDITORIAL_UNIT_MAX_CHARS: int = 900
    AIGC_REVIEW_MACRO_BLOCK_MIN_CHARS: int = 1_200
    AIGC_REVIEW_MACRO_BLOCK_MAX_CHARS: int = 2_500
    AIGC_REVIEW_AUDIT_BATCH_UNITS: int = 1
    AIGC_REVIEW_AUDIT_MAX_OUTPUT_TOKENS: int = 6_000
    AIGC_REVIEW_RUN_BUDGET_CNY: float = 0.30
    AIGC_REVIEW_CALIBRATION_BUDGET_CNY: float = 0.30
    AIGC_REVIEW_MAX_MANUSCRIPT_CHARS: int = 200_000
    AIGC_REVIEW_MAX_PDF_BYTES: int = 30_000_000
    AIGC_REVIEW_MAX_PDF_PAGES: int = 40
    AIGC_REVIEW_OCR_SCALE: float = 1.5
    AIGC_REVIEW_OCR_RETRY_SCALE: float = 2.2
    AIGC_REVIEW_OCR_MAX_SCAN_PAGES: int = 6
    AIGC_REVIEW_ZHUQUE_BASE_URL: str = "https://matrix.tencent.com"
    AIGC_REVIEW_ZHUQUE_PROFILE_TIMEOUT_SECONDS: float = 12.0
    AIGC_REVIEW_ZHUQUE_PROFILE_CACHE_SECONDS: int = 3_600

    # --- 图像生成 ---
    IMAGE_API_KEY: str = ""
    IMAGE_BASE_URL: str = "https://api.openai.com/v1"
    IMAGE_MODEL: str = "gpt-image-2"
    IMAGE_SIZE: str = "2450x3500"
    IMAGE_QUALITY: str = ""
    IMAGE_RESPONSE_FORMAT: str = ""
    IMAGE_OUTPUT_FORMAT: str = ""
    IMAGE_API_REQUEST_TIMEOUT_SECONDS: int = 900
    IMAGE_API_HARD_TIMEOUT_SECONDS: int = 780
    IMAGE_RESULT_DOWNLOAD_TIMEOUT_SECONDS: int = 60
    IMAGE_RESULT_DOWNLOAD_MAX_RETRIES: int = 3
    IMAGE_MAX_CONCURRENT_CALLS: int = 2
    IMAGE_API_MAX_RETRIES: int = 3
    IMAGE_GATE_WAIT_TIMEOUT_SECONDS: int = 900
    COVER_WORKFLOW_ENABLED: bool = True
    COVER_BOOK_TASK_GENERATION_ENABLED: bool = False
    COVER_TASK_MAX_RETRIES: int = 360
    COVER_TASK_RETRY_SECONDS: int = 60
    COVER_JOB_MAX_ROWS: int = 500
    COVER_JOB_MAX_CONCURRENT_ITEMS: int = 2
    COVER_JOB_MAX_ITEMS_PER_RUN: int = 8
    COVER_ITEM_MAX_AUTO_RETRIES: int = 5
    COVER_PROCESSING_STALE_SECONDS: int = 1800
    OFFICIAL_API_DIRECT_ONLY: bool = True
    COVER_BATCH_V2_ENABLED: bool = True
    COVER_BATCH_USE_OPENAI_BATCH: bool = False
    COVER_BATCH_MAX_CONCURRENT_ITEMS: int = 2
    COVER_BATCH_MAX_ITEMS_PER_RUN: int = 8
    COVER_BATCH_LEASE_SECONDS: int = 1800
    COVER_BATCH_MAX_AUTO_RETRIES: int = 3
    COVER_BATCH_RETRY_BASE_SECONDS: int = 60
    COVER_BATCH_RETRY_MAX_SECONDS: int = 900
    COVER_BATCH_UNCERTAIN_AUTO_RETRY: bool = True
    COVER_REFERENCE_FETCH_ENABLED: bool = True
    COVER_REFERENCE_CACHE_ENABLED: bool = False
    COVER_REFERENCE_DOWNLOAD_IMAGES: bool = False
    COVER_REFERENCE_CACHE_DIR: str = "dangdang_book_covers"
    COVER_REFERENCE_TIMEOUT_SECONDS: int = 12
    COVER_ORTHODOX_REFERENCE_ZIP_PATH: str = ""
    COVER_DEEPSEEK_STYLE_ANALYSIS_ENABLED: bool = True
    COVER_DEEPSEEK_STYLE_ANALYSIS_MAX_TOKENS: int = 1200

    # --- 轻抖 API (视频文案提取) ---
    QINGDOU_API_KEY: str = ""
    QINGDOU_BASE_URL: str = "https://www.qingdou.vip/web/api/commitGetTextTask"
    QINGDOU_MAX_VIDEO_DURATION: int = 0
    QINGDOU_POLL_INTERVAL_SECONDS: float = 3.0
    QINGDOU_POLL_TIMEOUT_SECONDS: int = 300
    QINGDOU_MAX_CONCURRENT_TASKS: int = 30
    # Qingdou only permits one in-flight extraction per account. Keep the
    # distributed lock enabled by default across all Celery worker threads.
    QINGDOU_SERIAL_LOCK_ENABLED: bool = True
    QINGDOU_LOCK_WAIT_TIMEOUT_SECONDS: int = 1800
    QINGDOU_LOCK_TTL_SECONDS: int = 900
    QINGDOU_BUSY_RETRY_SECONDS: float = 10.0
    QINGDOU_BUSY_MAX_ATTEMPTS: int = 30
    QINGDOU_HTTP_MAX_RETRIES: int = 4
    QINGDOU_HTTP_RETRY_BACKOFF_SECONDS: float = 2.0

    # --- YouTube transcript extraction (free providers, no paid model API) ---
    YOUTUBE_TRANSCRIPT_ENABLED: bool = True
    YOUTUBE_TRANSCRIPT_PROVIDERS: str = "youtube_transcript_api,youtube_transcript_ai,pytubefix,yt_dlp,openai_audio"
    YOUTUBE_TRANSCRIPT_LANGUAGES: str = "zh-Hans,zh-Hant,zh,en,en-US,en-GB"
    YOUTUBE_TRANSCRIPT_TOTAL_TIMEOUT_SECONDS: int = 240
    YOUTUBE_TRANSCRIPT_TIMEOUT_SECONDS: int = 45
    YOUTUBE_TRANSCRIPT_MIN_CHARS: int = 80
    YOUTUBE_HTTPS_PROXY: str = ""
    YOUTUBE_COOKIES_FILE: str = ""
    YOUTUBE_COOKIES_CONTENT: str = ""
    YOUTUBE_VISITOR_DATA: str = ""
    YOUTUBE_PO_TOKEN: str = ""
    YOUTUBE_YT_DLP_EXTRACTOR_ARGS: str = ""
    YOUTUBE_AUDIO_TRANSCRIPTION_ENABLED: bool = True
    YOUTUBE_AUDIO_TRANSCRIPTION_MODEL: str = "gpt-4o-mini-transcribe"
    AUDIO_TRANSCRIPTION_FALLBACK_MODELS: str = "whisper-1"
    AUDIO_TRANSCRIPTION_CHUNK_SECONDS: int = 600
    YOUTUBE_AUDIO_MAX_BYTES: int = 25_000_000
    YOUTUBE_CACHE_DIR: str = "backend/output/youtube_cache"

    # --- WeChat Channels fallback resolvers ---
    WECHAT_CHANNELS_PROVIDERS: str = "tikhub,wechatvideodownloader,generic"
    WECHAT_CHANNELS_TIKHUB_API_KEY: str = ""
    WECHAT_CHANNELS_TIKHUB_BASE_URL: str = "https://api.tikhub.io"
    WECHAT_CHANNELS_DOWNLOADER_API_KEY: str = ""
    WECHAT_CHANNELS_DOWNLOADER_BASE_URL: str = "https://api.wechatvideodownloader.com/sph/"
    WECHAT_CHANNELS_GENERIC_RESOLVER_URL: str = ""
    WECHAT_CHANNELS_GENERIC_RESOLVER_METHOD: str = "GET"
    WECHAT_CHANNELS_GENERIC_RESOLVER_API_KEY: str = ""
    WECHAT_CHANNELS_GENERIC_RESOLVER_AUTH_HEADER: str = "Authorization"
    WECHAT_CHANNELS_MEDIA_DOWNLOAD_TIMEOUT_SECONDS: int = 180
    WECHAT_CHANNELS_TRANSCRIPTION_TIMEOUT_SECONDS: int = 600
    WECHAT_CHANNELS_MEDIA_MAX_BYTES: int = 200_000_000
    WECHAT_CHANNELS_AUDIO_MAX_BYTES: int = 25_000_000
    WECHAT_CHANNELS_AUDIO_TRANSCRIPTION_MODEL: str = "gpt-4o-mini-transcribe"
    WECHAT_CHANNELS_TRANSCRIPT_MIN_CHARS: int = 20

    # --- Bilibili transcript fallback ---
    BILIBILI_TRANSCRIPT_FALLBACK_ENABLED: bool = True
    BILIBILI_MEDIA_DOWNLOAD_TIMEOUT_SECONDS: int = 300
    BILIBILI_TRANSCRIPTION_TIMEOUT_SECONDS: int = 900
    BILIBILI_MEDIA_MAX_BYTES: int = 300_000_000
    BILIBILI_AUDIO_MAX_BYTES: int = 80_000_000
    BILIBILI_AUDIO_TRANSCRIPTION_MODEL: str = "gpt-4o-mini-transcribe"
    BILIBILI_AUDIO_TRANSCRIPTION_MAX_CONCURRENT: int = 2
    BILIBILI_AUDIO_GATE_WAIT_TIMEOUT_SECONDS: int = 3600
    BILIBILI_TRANSCRIPT_MIN_CHARS: int = 20

    # --- 古文白话 ---
    CLASSICAL_DICTIONARY_PATH: str = "backend/output/classical_dictionary/dict-revised.json.xz"
    CLASSICAL_DICTIONARY_CONTEXT_MAX_CHARS: int = 4500
    CLASSICAL_DICTIONARY_MAX_ENTRIES: int = 80
    LONG_NOVEL_STRONG_MODEL: str = ""
    LONG_NOVEL_DEFAULT_MODEL: str = ""
    LONG_NOVEL_CHEAP_MODEL: str = ""
    LONG_NOVEL_MAX_PROJECT_COST: float = 0.0
    LONG_NOVEL_MAX_CHAPTER_REPAIR_COUNT: int = 1
    LONG_NOVEL_INPUT_COST_PER_1M: float = 5.0
    LONG_NOVEL_CACHED_INPUT_COST_PER_1M: float = 0.5
    LONG_NOVEL_OUTPUT_COST_PER_1M: float = 30.0
    LONG_NOVEL_GEMINI_INPUT_COST_PER_1M: float = 1.5
    LONG_NOVEL_GEMINI_OUTPUT_COST_PER_1M: float = 7.5
    LONG_NOVEL_DEEPSEEK_INPUT_COST_PER_1M: float = 0.14
    LONG_NOVEL_DEEPSEEK_OUTPUT_COST_PER_1M: float = 0.28
    LONG_NOVEL_PROCESSING_MODE: str = "flex"
    LONG_NOVEL_FLEX_BACKGROUND: bool = True
    LONG_NOVEL_FLEX_FALLBACK_TO_STANDARD: bool = True
    LONG_NOVEL_FLEX_POLL_INTERVAL_SECONDS: float = 5.0
    LONG_NOVEL_FLEX_TIMEOUT_SECONDS: int = 3600
    # Cache warmup is an extra paid request whose result is discarded.  Keep it
    # opt-in so normal production runs never spend tokens before real work.
    LONG_NOVEL_PROMPT_CACHE_WARMUP: bool = False
    LONG_NOVEL_STATE_MAX_OUTPUT_TOKENS: int = 14000
    LONG_NOVEL_CONTINUITY_MAX_OUTPUT_TOKENS: int = 10000
    LONG_NOVEL_STATE_REASONING_EFFORT: str = "high"
    LONG_NOVEL_CONTINUITY_REASONING_EFFORT: str = "high"

    # --- Epic Novel Engine ---
    EPIC_EDITOR_MODEL_PROVIDER: str = ""
    EPIC_EDITOR_MODEL_NAME: str = ""
    EPIC_AUTHOR_MODEL_PROVIDER: str = ""
    EPIC_AUTHOR_MODEL_NAME: str = ""
    EPIC_CHEAP_MODEL_PROVIDER: str = ""
    EPIC_CHEAP_MODEL_NAME: str = ""
    EPIC_MAX_PROJECT_COST: float = 0.0
    EPIC_MAX_CHAPTER_REPAIR_COUNT: int = 1
    EPIC_ENABLE_EDITOR_REVIEW: bool = True
    EPIC_ENABLE_REFERENCE_RAG: bool = True
    EPIC_ENABLE_WORLD_ATLAS: bool = True
    EPIC_ENABLE_MEGA_BIBLE: bool = True
    EPIC_ENABLE_REFERENCE_PROFILE: bool = True
    EPIC_USE_EXISTING_WRITER_FOR_DRAFTS: bool = True
    EPIC_ALLOW_SMOKE_FALLBACK: bool = True
    EPIC_STRUCTURED_GENERATION_FALLBACK_TO_EDITOR: bool = True
    EPIC_CHAPTER_WRITER_MAX_OUTPUT_TOKENS: int = 0
    EPIC_CONTEXT_RECENT_CHAPTER_LIMIT: int = 0
    EPIC_CONTEXT_BIBLE_SECTION_LIMIT: int = 0
    EPIC_CONTEXT_STATE_LIMIT: int = 0
    EPIC_CONTEXT_REFERENCE_CARD_LIMIT: int = 0
    EPIC_PROFILE_SUMMARY_ITEM_LIMIT: int = 0
    EPIC_REFERENCE_INGEST_SECTION_LIMIT: int = 0
    EPIC_REFERENCE_SECTION_CHARS: int = 5000
    EPIC_REFERENCE_MODEL_PROVIDER: str = "deepseek"
    EPIC_REFERENCE_MODEL_NAME: str = "deepseek-v4-pro"
    EPIC_REFERENCE_ENABLE_LLM_EXTRACTION: bool = True
    EPIC_REFERENCE_BATCH_TOKEN_BUDGET: int = 60000
    EPIC_REFERENCE_RETRY_FAILED_ONLY: bool = True
    EPIC_REFERENCE_FORCE_REINGEST: bool = False
    EPIC_REFERENCE_CACHE_BY_HASH: bool = True
    EPIC_REFERENCE_EXTRACTION_LANGUAGE: str = "zh-CN"
    EPIC_CONTEXT_WINDOW_TOKENS: int = 0
    EPIC_CONTEXT_PROMPT_BUDGET_RATIO: float = 0.75
    EPIC_CONTINUATION_DONE_MARKER: str = "[[EPIC_CHAPTER_DONE]]"
    EPIC_CONTINUATION_MAX_CHUNKS: int = 8
    REFERENCE_RAW_STORAGE: str = "local"
    REFERENCE_RAW_PATH: str = ".local/novel_ref_corpus/raw"
    REFERENCE_PROCESSED_PATH: str = ".local/novel_ref_corpus/processed"
    REFERENCE_DOWNLOAD_PATH: str = ".local/novel_ref_corpus/downloads"
    EPIC_EXPORT_PATH: str = ".local/epic_novel_exports"
    EPIC_RUN_ARTIFACT_PATH: str = ".local/epic_novel_runs"
    REFERENCE_DOWNLOADER_PROVIDER: str = "sonovel"
    SONOVEL_HOME: str = r"C:\Users\15050\Desktop\SoNovel"
    SONOVEL_DOWNLOAD_PATH: str = r"C:\Users\15050\Desktop\SoNovel\downloads"
    SONOVEL_OUTPUT_FORMAT: str = "epub"
    SONOVEL_WEB_BASE_URL: str = "http://127.0.0.1:7765"

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

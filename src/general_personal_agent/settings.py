"""Settings for General Personal Agent, loaded from the project-local .env."""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env",),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "dev"
    app_host: str = "127.0.0.1"
    app_port: int = 8100
    app_secret_key: str = ""
    app_log_level: str = "INFO"
    app_cors_origins: str = "http://127.0.0.1:8100"

    session_cookie_name: str = "gpa_session"
    session_cookie_secure: bool = False
    session_ttl_min: int = 720
    demo_login_pin: str = ""
    demo_users: str = "alice,bob,admin"

    ark_api_key: str = ""
    ark_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    model_agent_name: str = ""
    model_agent_max_llm_calls: int = 8

    tool_vespeech_app_id: str = ""
    tool_vespeech_api_key: str = ""
    tool_vespeech_speaker: str = ""
    tool_vespeech_speed_ratio: float = 1.0
    asr_ws_url: str = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel"
    asr_sample_rate: int = 16000
    asr_enable_nonstream: bool = False
    tts_ws_url: str = "wss://openspeech.bytedance.com/api/v3/tts/unidirectional/stream"
    tts_ws_fallback_url: str = "wss://openspeech.bytedance.com/api/v3/tts/bidirection"
    tts_resource_id: str = "seed-tts-2.0"
    tts_cluster: str = "volcano_tts"
    tts_sample_rate: int = 24000
    tts_max_sentence_chars: int = 20

    # ---- search ----
    search_provider: str = "veadk_web_search"
    search_count: int = 5
    search_timeout_s: float = 60.0
    search_cache_ttl_s: float = 300.0
    search_summary_max_chars: int = 1500  # Agent tool 回传摘要上限，避免撑爆上下文
    # 主 provider 失败后的兜底（逗号分隔、按序尝试；空字符串关闭兜底）
    search_fallback_providers: str = "ark"
    search_attempt_timeout_s: float = 15.0  # 单次 provider 尝试的超时上限
    search_retry_attempts: int = 2  # 同一 provider 最多尝试次数（含首次）
    search_retry_backoff_s: float = 0.4
    search_budget_s: float = 30.0  # 一轮搜索（含兜底与重试）的总预算

    ov_url: str = "https://api.vikingdb.cn-beijing.volces.com/openviking"
    ov_account: str = "default"
    ov_tenant_id: str = "demo"
    ov_user_key_admin: str = ""
    ov_user_key_alice: str = ""
    ov_user_key_bob: str = ""
    ov_user_alice: str = "Alice"
    ov_user_bob: str = "Bob"
    ov_user_admin: str = "default"
    ov_peer_alice: str = "device_alice"
    ov_peer_bob: str = "device_bob"
    # Directory layout (see gpa/context/layout.py). Empty = search the scope
    # root; writes always land in a typed subdirectory (uploads/, artifacts/).
    # Shared KB is the fixed viking://resources scope: admins write it, every
    # user reads it, and no per-role folder (admin/, shared/) is created.
    ov_personal_kb_indexes: str = ""  # e.g. "uploads,artifacts,docs"
    ov_shared_kb_categories: str = ""  # optional topic folders, e.g. "hr,ops"
    ov_skills_enabled: bool = True  # surface agent/user SKILL.md as context
    ov_timeout_s: float = 30.0  # commit/metadata calls can take several seconds
    ov_search_top_k: int = 8

    context_token_budget: int = 2000  # 渲染硬上限（估算 token）
    context_token_ratio: float = 0.75  # 中文估算：1 字 ≈ 0.75 token（S7 校准前默认）
    context_trace_ttl_days: int = 7

    # ---- A1 稳定会话 / STM ----
    agent_stm_backend: str = "postgresql"  # local / postgresql
    agent_stm_schema: str = "agent_stm"

    # ---- A4 Outbox ----
    outbox_interval_s: float = 1.0
    outbox_batch_size: int = 50
    outbox_max_attempts: int = 8

    memory_enabled: bool = True
    memory_commit_every: int = 2  # N turns; writer commits every N*2 messages

    # ---- GPA-native conversation/agent core (P1) ----
    gpa_agent_mode: str = "auto"  # auto | veadk | stub
    gpa_context_manager: bool = True
    gpa_warmup_enabled: bool = True
    gpa_context_tools_enabled: bool = True
    gpa_redis_url: str = ""
    gpa_rate_limit_messages_per_min: int = 30
    gpa_rate_limit_ws_per_min: int = 10
    gpa_rate_limit_login_per_min: int = 30
    gpa_rate_limit_downloads_per_min: int = 60
    gpa_service_token: str = ""
    gpa_service_token_previous: str = ""
    gpa_service_jwt_secret: str = ""
    gpa_service_jwt_previous_secret: str = ""
    gpa_service_jwt_secret_file: str = ""
    gpa_service_jwt_previous_secret_file: str = ""
    gpa_service_jwt_issuer: str = "gpa"
    gpa_service_jwt_audience: str = "gpa-internal"
    gpa_service_token_ttl_s: int = 900
    gpa_oauth_client_id: str = ""
    gpa_oauth_client_secret: str = ""
    gpa_rate_limit_oauth_per_min: int = 10
    gpa_oauth_allowed_users: str = ""
    gpa_text_budget_ms: int = 800
    gpa_voice_budget_ms: int = 400
    gpa_voice_filler_enabled: bool = True  # 语音轮先播一句缓存 filler，盖住搜索/任务等待
    gpa_draft_ttl_days: int = 7  # 无 turn 的 draft 会话保留天数
    gpa_draft_purge_interval_s: int = 21600  # draft 清理巡检间隔
    gpa_turn_timeout_s: int = 60
    gpa_runtime_ttl_s: int = 1800  # idle agent runtime eviction
    gpa_runtime_max_sessions: int = 200
    gpa_inbox_interval_s: float = 2.0
    gpa_inbox_batch_size: int = 50

    # ---- files / knowledge base (F1-F4) ----
    gpa_files_enabled: bool = True
    gpa_kb_enabled: bool = True
    gpa_kb_shared_write_enabled: bool = True
    gpa_files_rollout: str = "all"  # off | internal | percent | all
    gpa_files_allowlist: str = ""
    gpa_files_rollout_percent: int = 0
    gpa_file_max_mb: int = 50
    gpa_file_max_per_message: int = 10
    gpa_conversation_quota_mb: int = 200
    gpa_file_extract_timeout_s: int = 120
    gpa_file_extract_workers: int = 2  # 0 = parse in a worker thread
    gpa_file_extract_memory_mb: int = 512  # per parse worker address-space cap
    gpa_file_read_wait_ms: int = 1500
    gpa_kb_private_quota_mb: int = 2048
    gpa_kb_shared_quota_mb: int = 10240
    # OV ``add_resource`` is asynchronous; a delete that lands while the import
    # task is still running must not treat the not-yet-created target as gone.
    kb_import_timeout_s: int = 180
    kb_import_poll_interval_s: float = 2.0
    gpa_file_gc_retention_days: int = 7
    gpa_file_orphan_hours: int = 24
    gpa_file_job_interval_s: float = 1.0
    gpa_file_job_batch_size: int = 10

    database_url: str = "postgresql+asyncpg://demo:demo@127.0.0.1:5432/general_personal_agent"

    ma_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    ma_api_key: str = ""
    ma_agent_id: str = ""
    ma_environment_id: str = ""
    # 0 = use the agent's current version reported by MA. A fixed value pins
    # sessions to a specific agent build (old builds may lack newer tools).
    ma_agent_version: int = 0
    ma_poll_interval_s: float = 2.0
    # 总时长护栏：只用来兜底失控会话的计费，不用于打断仍在推进的任务。
    task_timeout_s: int = 1800
    # 活跃度预算：连续这么久没有任何新 MA 事件才判定卡死（有新事件会续期）。
    task_idle_timeout_s: int = 300
    # 可重试错误（上游 5xx / 会话失败）的额外重试次数。
    task_max_retry: int = 2
    # 重试的指数退避基数与上限（秒）；0 表示立即重试。
    task_retry_backoff_s: float = 30.0
    task_retry_backoff_max_s: float = 300.0
    # 进度事件最小间隔，避免每个 poll 都写一条 task_event。
    task_progress_interval_s: float = 20.0
    # MA 报 idle 后产物注册有延迟，空结果继续等待的窗口。
    task_artifact_grace_s: int = 15
    task_artifact_ttl_days: int = 7
    gpa_artifact_cleanup_enabled: bool = True
    gpa_artifact_cleanup_interval_s: int = 3600
    a2a_enabled: bool = False
    a2a_agent_url: str = ""
    a2a_token: str = ""
    a2a_timeout_s: int = 300
    a2a_poll_interval_s: float = 2.0

    volcengine_access_key: str = ""
    volcengine_secret_key: str = ""
    volcengine_session_token: str = ""
    database_tos_bucket_name: str = ""
    ma_tos_bucket: str = ""
    database_tos_region: str = "cn-beijing"
    tos_endpoint: str = "https://tos-cn-beijing.volces.com"
    tos_signed_url_ttl_s: int = 3600
    tos_prefix: str = "general-personal-agent"

    # ------------------------------------------------------------------ helpers
    @property
    def ma_key(self) -> str:
        return self.ma_api_key or self.ark_api_key

    @property
    def db_dsn(self) -> str:
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    def ov_key(self, ref: str) -> str:
        return getattr(self, ref.lower(), "") or ""

    def require(self, *names: str) -> None:
        missing = [n for n in names if not getattr(self, n, "")]
        if missing:
            raise RuntimeError(f"missing required settings: {', '.join(missing)}")

    def apply_veadk_env(self) -> None:
        """VeADK reads MODEL_AGENT_* at import/construction time."""
        os.environ.setdefault("MODEL_AGENT_API_KEY", self.ark_api_key)
        os.environ.setdefault("MODEL_AGENT_API_BASE", self.ark_base_url)
        os.environ.setdefault("MODEL_AGENT_NAME", self.model_agent_name)
        os.environ.setdefault("MODEL_AGENT_PROVIDER", "openai")
        os.environ.setdefault("MODEL_AGENT_MAX_LLM_CALLS", str(self.model_agent_max_llm_calls))


@lru_cache
def get_settings() -> Settings:
    return Settings()

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

ProviderName = Literal["siliconflow", "deepseek"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)


class GeneralSettings(StrictModel):
    system_name: str = Field(default="境智 · 出入境业务平台", min_length=1, max_length=80)
    organization: str = Field(default="出入境业务中心", min_length=1, max_length=100)

    @field_validator("system_name", "organization")
    @classmethod
    def trim_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("不可为空")
        return value.strip()


class PostgresSettings(StrictModel):
    host: str = Field(default="127.0.0.1", min_length=1, max_length=253)
    port: int = Field(default=5432, ge=1, le=65535)
    database: str = Field(default="next_nia", min_length=1, max_length=63)
    username: str = Field(default="postgres", min_length=1, max_length=63)
    password: SecretStr = SecretStr("")

    @field_validator("host")
    @classmethod
    def valid_host(cls, value: str) -> str:
        value = value.strip()
        if not value or any(c in value for c in "/\\@?#\x00\r\n\t "):
            raise ValueError("请填写主机名或 IP，不要填写连接 URL")
        return value

    @field_validator("database", "username")
    @classmethod
    def valid_database_field(cls, value: str) -> str:
        if not value.strip() or any(c in value for c in "\x00\r\n"):
            raise ValueError("数据库参数无效")
        return value.strip()


class PostgresUpdate(PostgresSettings):
    clear_password: bool = False


class EnvironmentSettings(StrictModel):
    mode: Literal["test", "production"] = "test"
    postgres: PostgresSettings = Field(default_factory=PostgresSettings)


class EnvironmentUpdate(StrictModel):
    mode: Literal["test", "production"] = "test"
    postgres: PostgresUpdate = Field(default_factory=PostgresUpdate)


class ProviderSettings(StrictModel):
    enabled: bool
    chat_model: str = Field(min_length=1, max_length=200)
    api_key: SecretStr = SecretStr("")

    @field_validator("api_key")
    @classmethod
    def valid_api_key(cls, value: SecretStr) -> SecretStr:
        key = value.get_secret_value().strip()
        if len(key) > 4096 or any(character in key for character in "\r\n\x00"):
            raise ValueError("密钥格式无效")
        return SecretStr(key)

    @field_validator("chat_model")
    @classmethod
    def valid_model(cls, value: str) -> str:
        if not value.strip() or any(c in value for c in "\x00\r\n"):
            raise ValueError("模型名无效")
        return value.strip()


class SiliconFlowSettings(ProviderSettings):
    enabled: bool = True
    base_url: Literal["https://api.siliconflow.cn/v1"] = "https://api.siliconflow.cn/v1"
    chat_model: str = "Qwen/Qwen3-8B"
    asr_model: str = Field(default="FunAudioLLM/SenseVoiceSmall", min_length=1, max_length=200)

    @field_validator("asr_model")
    @classmethod
    def valid_asr_model(cls, value: str) -> str:
        return cls.valid_model(value)


class DeepSeekSettings(ProviderSettings):
    enabled: bool = False
    base_url: Literal["https://api.deepseek.com"] = "https://api.deepseek.com"
    chat_model: str = "deepseek-flash"


class SiliconFlowUpdate(SiliconFlowSettings):
    clear_api_key: bool = False


class DeepSeekUpdate(DeepSeekSettings):
    clear_api_key: bool = False


class Providers(StrictModel):
    siliconflow: SiliconFlowSettings = Field(default_factory=SiliconFlowSettings)
    deepseek: DeepSeekSettings = Field(default_factory=DeepSeekSettings)


class ProvidersUpdate(StrictModel):
    siliconflow: SiliconFlowUpdate = Field(default_factory=SiliconFlowUpdate)
    deepseek: DeepSeekUpdate = Field(default_factory=DeepSeekUpdate)


class AISettings(StrictModel):
    enabled: bool = False
    default_provider: ProviderName = "siliconflow"
    providers: Providers = Field(default_factory=Providers)


class AIUpdate(StrictModel):
    enabled: bool = False
    default_provider: ProviderName = "siliconflow"
    providers: ProvidersUpdate = Field(default_factory=ProvidersUpdate)


class Settings(StrictModel):
    general: GeneralSettings = Field(default_factory=GeneralSettings)
    environment: EnvironmentSettings = Field(default_factory=EnvironmentSettings)
    ai: AISettings = Field(default_factory=AISettings)


class SettingsUpdate(StrictModel):
    general: GeneralSettings
    environment: EnvironmentUpdate
    ai: AIUpdate


class AITestRequest(StrictModel):
    provider: ProviderName
    api_key: SecretStr = SecretStr("")
    chat_model: str | None = Field(default=None, min_length=1, max_length=200)


def reveal(secret: SecretStr) -> str:
    return secret.get_secret_value()


def persisted_settings(settings: Settings) -> dict:
    value = settings.model_dump(mode="json")
    value["environment"]["postgres"]["password"] = reveal(settings.environment.postgres.password)
    for name in ("siliconflow", "deepseek"):
        value["ai"]["providers"][name]["api_key"] = reveal(getattr(settings.ai.providers, name).api_key)
    return value


def public_settings(settings: Settings) -> dict:
    value = settings.model_dump(mode="json")
    postgres = value["environment"]["postgres"]
    postgres.pop("password")
    postgres["password_configured"] = bool(reveal(settings.environment.postgres.password))
    for name in ("siliconflow", "deepseek"):
        provider = value["ai"]["providers"][name]
        provider.pop("api_key")
        provider["api_key_configured"] = bool(reveal(getattr(settings.ai.providers, name).api_key))
    return value


def merge_postgres(update: PostgresUpdate, current: PostgresSettings) -> PostgresSettings:
    data = update.model_dump(exclude={"clear_password"})
    if update.clear_password:
        data["password"] = ""
    elif not reveal(update.password):
        data["password"] = current.password
    return PostgresSettings.model_validate(data)


def merge_settings(update: SettingsUpdate, current: Settings) -> Settings:
    data = update.model_dump()
    data["environment"]["postgres"] = merge_postgres(update.environment.postgres, current.environment.postgres)
    for name in ("siliconflow", "deepseek"):
        incoming = getattr(update.ai.providers, name)
        existing = getattr(current.ai.providers, name)
        provider = data["ai"]["providers"][name]
        provider.pop("clear_api_key")
        if name == "siliconflow" and "asr_model" not in incoming.model_fields_set:
            provider["asr_model"] = existing.asr_model
        if incoming.clear_api_key:
            provider["api_key"] = ""
        elif not reveal(incoming.api_key):
            provider["api_key"] = existing.api_key
    return Settings.model_validate(data)


def database_identity(settings: Settings) -> tuple:
    if settings.environment.mode == "test":
        return ("test",)
    pg = settings.environment.postgres
    return ("production", pg.host, pg.port, pg.database, pg.username, reveal(pg.password))

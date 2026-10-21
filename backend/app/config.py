import json
import os
from pathlib import Path
import tempfile
from threading import RLock

from pydantic import ValidationError

from .schemas import Settings, persisted_settings


class ConfigurationError(RuntimeError):
    """Configuration errors deliberately omit file contents and credentials."""


class SettingsStore:
    def __init__(self, directory: Path):
        self.directory = directory
        self.path = directory / "settings.json"
        self.lock = RLock()
        self.current = Settings()

    def load(self) -> Settings:
        with self.lock:
            try:
                self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                if not self.path.exists():
                    self.save(Settings())
                else:
                    if self.path.is_symlink() or self.path.stat().st_size > 128 * 1024:
                        raise ConfigurationError("设置文件无效，请检查服务端配置；未自动覆盖。")
                    content = json.loads(self.path.read_text(encoding="utf-8"))
                    if not isinstance(content, dict) or content.get("schema_version") != 1:
                        raise ConfigurationError("设置文件版本不受支持；未自动覆盖。")
                    self.current = Settings.model_validate(content["settings"])
                    self.path.chmod(0o600)
                return self.current.model_copy(deep=True)
            except ConfigurationError:
                raise
            except (OSError, ValueError, KeyError, ValidationError):
                raise ConfigurationError("无法读取设置文件，请检查文件格式和权限；未自动覆盖。") from None

    def save(self, settings: Settings) -> None:
        with self.lock:
            temporary = None
            try:
                self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                descriptor, temporary = tempfile.mkstemp(prefix=".settings-", suffix=".tmp", dir=self.directory)
                os.fchmod(descriptor, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump({"schema_version": 1, "settings": persisted_settings(settings)}, stream, ensure_ascii=False, indent=2)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
                self.current = settings.model_copy(deep=True)
            except OSError:
                raise ConfigurationError("无法保存设置，请检查服务端目录权限和磁盘空间。") from None
            finally:
                if temporary and os.path.exists(temporary):
                    os.unlink(temporary)

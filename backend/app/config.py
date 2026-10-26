import json
from contextlib import closing
import os
import sqlite3
from pathlib import Path
from threading import RLock

from pydantic import ValidationError

from .schemas import Settings, persisted_settings


class ConfigurationError(RuntimeError):
    """Configuration errors deliberately omit file contents and credentials."""


class SettingsStore:
    def __init__(self, directory: Path):
        self.directory = directory
        self.path = directory / "settings.sqlite3"
        self.legacy_path = directory / "settings.json"
        self.lock = RLock()
        self.current = Settings()

    def _connect(self):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink():
            raise ConfigurationError("配置数据库无效，请检查服务端配置；未自动覆盖。")
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        self.path.chmod(0o600)
        return sqlite3.connect(self.path)

    def load(self) -> Settings:
        with self.lock:
            try:
                if self.path.exists():
                    with closing(self._connect()) as connection, connection:
                        row = connection.execute("SELECT payload FROM configuration WHERE id=1").fetchone()
                    if not row:
                        raise ConfigurationError("配置数据库内容缺失；未自动覆盖。")
                    content = json.loads(row[0])
                elif self.legacy_path.exists():
                    if self.legacy_path.is_symlink() or self.legacy_path.stat().st_size > 128 * 1024:
                        raise ConfigurationError("设置文件无效；未自动覆盖。")
                    content = json.loads(self.legacy_path.read_text(encoding="utf-8"))
                else:
                    self.save(Settings())
                    return self.current.model_copy(deep=True)
                if not isinstance(content, dict) or content.get("schema_version") != 1:
                    raise ConfigurationError("配置版本不受支持；未自动覆盖。")
                settings = Settings.model_validate(content["settings"])
                if not self.path.exists():
                    self.save(settings)
                    # Remove the legacy secret-bearing file only after the DB commit succeeds.
                    self.legacy_path.unlink()
                self.current = settings
                return self.current.model_copy(deep=True)
            except ConfigurationError:
                raise
            except (OSError, sqlite3.Error, ValueError, KeyError, ValidationError):
                raise ConfigurationError("无法读取配置数据库，请检查配置格式和权限；未自动覆盖。") from None

    def save(self, settings: Settings) -> None:
        with self.lock:
            try:
                payload = json.dumps({"schema_version": 1, "settings": persisted_settings(settings)}, ensure_ascii=False)
                with closing(self._connect()) as connection, connection:
                    connection.execute("CREATE TABLE IF NOT EXISTS configuration (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)")
                    connection.execute("INSERT INTO configuration(id,payload) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload", (payload,))
                self.current = settings.model_copy(deep=True)
            except (OSError, sqlite3.Error):
                raise ConfigurationError("无法保存配置数据库，请检查目录权限和磁盘空间。") from None

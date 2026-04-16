import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config.yaml"
DEFAULT_ENV_PATH = BASE_DIR / ".env"
ENV_PLACEHOLDER_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)(?::([^}]*))?\}")


class ConfigValidationError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


class ConfigManager:
    def __init__(
        self,
        config_path: Optional[Path] = None,
        env_path: Optional[Path] = None,
    ) -> None:
        self.config_path = Path(config_path or CONFIG_PATH)
        self.env_path = Path(env_path or DEFAULT_ENV_PATH)
        self._config: Dict[str, Any] = {}
        self._env_values: Dict[str, str] = {}
        self.reload()

    def reload(self) -> None:
        raw_config = self._read_yaml()
        self._env_values = self._load_env_values()
        self._config = self._resolve_env_placeholders(raw_config)
        self._normalize_model_config()
        self._validate_model_config()
        self._export_runtime_model_env()

    def _read_yaml(self) -> Dict[str, Any]:
        if not self.config_path.exists():
            return {}
        with open(self.config_path, "r", encoding="utf-8") as file:
            return yaml.safe_load(file) or {}

    def _load_env_values(self) -> Dict[str, str]:
        env_values: Dict[str, str] = {}
        for path in self._resolve_env_files():
            env_values.update(self._parse_dotenv(path))
        return env_values

    def _resolve_env_files(self) -> list[Path]:
        env_files = []
        if self.env_path.exists():
            env_files.append(self.env_path)

        base_env = self._parse_dotenv(self.env_path) if self.env_path.exists() else {}
        app_env = (
            os.environ.get("APP_ENV")
            or base_env.get("APP_ENV")
            or os.environ.get("ENV")
            or base_env.get("ENV")
        )
        if app_env:
            candidate = self.env_path.with_name(f".env.{app_env}")
            if candidate.exists():
                env_files.append(candidate)

        custom_env_file = os.environ.get("CONFIG_ENV_FILE") or base_env.get("CONFIG_ENV_FILE")
        if custom_env_file:
            custom_path = Path(custom_env_file)
            if not custom_path.is_absolute():
                custom_path = BASE_DIR / custom_path
            if custom_path.exists():
                env_files.append(custom_path)

        return env_files

    def _parse_dotenv(self, dotenv_path: Path) -> Dict[str, str]:
        if not dotenv_path.exists():
            return {}

        env_values: Dict[str, str] = {}
        for line in dotenv_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue

            key, value = stripped.split("=", 1)
            key = key.strip()
            value = value.strip()

            if value and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]

            env_values[key] = value
        return env_values

    def _resolve_env_placeholders(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: self._resolve_env_placeholders(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._resolve_env_placeholders(item) for item in value]
        if not isinstance(value, str):
            return value

        full_match = ENV_PLACEHOLDER_PATTERN.fullmatch(value)
        if full_match:
            return self._resolve_placeholder(full_match.group(1), full_match.group(2))

        def replacer(match: re.Match[str]) -> str:
            resolved = self._resolve_placeholder(match.group(1), match.group(2))
            return "" if resolved is None else str(resolved)

        return ENV_PLACEHOLDER_PATTERN.sub(replacer, value)

    def _resolve_placeholder(self, env_name: str, default_value: Optional[str]) -> Any:
        if env_name in self._env_values:
            raw_value = self._env_values[env_name]
        elif env_name in os.environ:
            raw_value = os.environ[env_name]
        else:
            raw_value = default_value

        if raw_value in (None, ""):
            return None

        return self._coerce_scalar(raw_value)

    def _coerce_scalar(self, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        try:
            return yaml.safe_load(value)
        except yaml.YAMLError:
            return value

    def _normalize_model_config(self) -> None:
        model_defaults = deepcopy(self.get("model.agent", {}) or {})
        if "name" in model_defaults and "model_name" not in model_defaults:
            model_defaults["model_name"] = model_defaults["name"]

        models = self._config.setdefault("models", {})
        for role, role_config in list(models.items()):
            normalized_role = deepcopy(model_defaults)
            for key, value in (role_config or {}).items():
                if value not in (None, ""):
                    normalized_role[key] = value

            normalized_role["model_name"] = (
                normalized_role.get("model_name")
                or normalized_role.get("name")
                or model_defaults.get("model_name")
            )
            normalized_role.pop("name", None)
            models[role] = normalized_role

        self._config.setdefault("model", {})
        self._config["model"].setdefault("agent", model_defaults)

    def _validate_model_config(self) -> None:
        missing_messages = []

        agent_defaults = self.get("model.agent", {}) or {}
        global_requirements = {
            "provider": ["MODEL_AGENT_PROVIDER"],
            "name": ["MODEL_AGENT_NAME"],
            "api_base": ["MODEL_AGENT_API_BASE"],
            "api_key": ["MODEL_AGENT_API_KEY"],
        }
        for field, env_names in global_requirements.items():
            if agent_defaults.get(field) in (None, ""):
                missing_messages.append(
                    f"`model.agent.{field}` 缺失，请在 `.env` 中设置 {' 或 '.join(f'`{env}`' for env in env_names)}。"
                )

        numeric_fields = {
            "timeout": int,
            "max_retries": int,
        }
        for field, expected_type in numeric_fields.items():
            value = agent_defaults.get(field)
            if value is not None and not isinstance(value, expected_type):
                missing_messages.append(
                    f"`model.agent.{field}` 必须是 {expected_type.__name__}，当前值为 `{value}`。"
                )

        for role, role_config in (self.get("models", {}) or {}).items():
            if role_config.get("model_name") in (None, ""):
                missing_messages.append(
                    f"`models.{role}.model_name` 缺失，请在 `.env` 中设置 `MODEL_{role.upper()}_NAME` 或 `MODEL_AGENT_NAME`。"
                )

            temperature = role_config.get("temperature")
            if temperature is not None and not isinstance(temperature, (int, float)):
                missing_messages.append(
                    f"`models.{role}.temperature` 必须是数字，当前值为 `{temperature}`。"
                )

            for field, expected_type in numeric_fields.items():
                value = role_config.get(field)
                if value is not None and not isinstance(value, expected_type):
                    missing_messages.append(
                        f"`models.{role}.{field}` 必须是 {expected_type.__name__}，当前值为 `{value}`。"
                    )

            for field, env_names in {
                "provider": [f"MODEL_{role.upper()}_PROVIDER", "MODEL_AGENT_PROVIDER"],
                "api_base": [f"MODEL_{role.upper()}_API_BASE", "MODEL_AGENT_API_BASE"],
                "api_key": [f"MODEL_{role.upper()}_API_KEY", "MODEL_AGENT_API_KEY"],
            }.items():
                if role_config.get(field) in (None, ""):
                    missing_messages.append(
                        f"`models.{role}.{field}` 缺失，请在 `.env` 中设置 {' 或 '.join(f'`{env}`' for env in env_names)}。"
                    )

        if missing_messages:
            error_lines = "\n".join(f"- {message}" for message in missing_messages)
            raise ConfigValidationError(f"配置校验失败:\n{error_lines}")

    def _export_runtime_model_env(self) -> None:
        agent_defaults = self.get("model.agent", {}) or {}
        runtime_env = {
            "MODEL_AGENT_PROVIDER": agent_defaults.get("provider"),
            "MODEL_AGENT_NAME": agent_defaults.get("name") or agent_defaults.get("model_name"),
            "MODEL_AGENT_API_BASE": agent_defaults.get("api_base"),
            "MODEL_AGENT_API_KEY": agent_defaults.get("api_key"),
            "MODEL_AGENT_TIMEOUT": agent_defaults.get("timeout"),
            "MODEL_AGENT_MAX_RETRIES": agent_defaults.get("max_retries"),
        }

        for key, value in runtime_env.items():
            if value not in (None, ""):
                os.environ[key] = str(value)

    def get(self, key: str, default: Any = None) -> Any:
        keys = key.split(".")
        value: Any = self._config
        for item in keys:
            if isinstance(value, dict) and item in value:
                value = value[item]
            else:
                return default
        return value

    def get_model_config(self, role: str) -> Dict[str, Any]:
        return deepcopy(self.get(f"models.{role}", {}) or {})

    @property
    def data(self) -> Dict[str, Any]:
        return deepcopy(self._config)


config = ConfigManager()

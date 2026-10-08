#!/usr/bin/env python3
"""Provider-neutral, read-only communications bridge.

This project intentionally keeps provider-specific authentication and protocol
details in the upstream CLIs:

* Himalaya handles mail over IMAP, Gmail, or Microsoft Graph.
* Calendula handles Google Calendar/CalDAV calendars.
* PnP CLI for Microsoft 365 handles Outlook Calendar and Microsoft Teams.

The bridge only invokes a fixed, read-only command set and converts the
different JSON payloads into one small, stable shape for scripts and Open
WebUI.  It never accepts an arbitrary command string.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = PROJECT_DIR / "config.json"
PROVIDERS = ("all", "outlook", "gmail", "yahoo")
PROVIDER_ALIASES = {
    "microsoft": "outlook",
    "m365": "outlook",
    "office365": "outlook",
    "google": "gmail",
}
PROVIDER_CHOICES = tuple(dict.fromkeys((*PROVIDERS, *PROVIDER_ALIASES)))
CALENDAR_BACKENDS = ("m365", "calendula")
MAX_ID_LENGTH = 300
MAX_QUERY_LENGTH = 1000


class BridgeError(Exception):
    """A user-actionable configuration or input error."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def as_dict(self) -> dict[str, Any]:
        result = {"ok": False, "error": self.code, "message": self.message}
        result.update(self.details)
        return result


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _canonical_provider(value: Any) -> str:
    provider = _text(value).strip().lower()
    provider = PROVIDER_ALIASES.get(provider, provider)
    if provider not in PROVIDERS[1:]:
        raise BridgeError(
            "invalid_provider",
            f"Provider must be one of {', '.join(PROVIDERS[1:])}; received {value!r}.",
        )
    return provider


def _canonical_selector(value: Any) -> str:
    selector = _text(value, "all").strip().lower() or "all"
    if selector == "all":
        return selector
    return _canonical_provider(selector)


def _bounded_int(value: Any, name: str, minimum: int, maximum: int, default: int) -> int:
    if value in (None, ""):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise BridgeError("invalid_number", f"{name} must be an integer.") from exc
    if not minimum <= number <= maximum:
        raise BridgeError("invalid_number", f"{name} must be between {minimum} and {maximum}.")
    return number


def _safe_scalar(
    value: Any,
    name: str,
    max_length: int = MAX_ID_LENGTH,
    *,
    reject_leading_dash: bool = False,
) -> str:
    text = _text(value).strip()
    if not text:
        raise BridgeError("missing_value", f"{name} is required.")
    if len(text) > max_length:
        raise BridgeError("invalid_value", f"{name} is too long.")
    if any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise BridgeError("invalid_value", f"{name} contains control characters.")
    # Values passed as standalone argv entries must not be allowed to become
    # another option when a provider CLI parses the command line.
    if reject_leading_dash and text.startswith("-"):
        raise BridgeError("invalid_value", f"{name} cannot start with '-'.")
    return text


def _optional_scalar(
    value: Any,
    name: str,
    max_length: int = MAX_ID_LENGTH,
    *,
    reject_leading_dash: bool = False,
) -> str:
    if value in (None, ""):
        return ""
    return _safe_scalar(value, name, max_length, reject_leading_dash=reject_leading_dash)


def _parse_iso_date(value: Any, name: str, *, date_only: bool = False) -> str:
    text = _safe_scalar(value, name, 80)
    candidate = text.replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(candidate)
    except ValueError:
        try:
            parsed_date = dt.date.fromisoformat(text)
        except ValueError as exc:
            raise BridgeError("invalid_date", f"{name} must be ISO-8601 (for example 2026-09-11).") from exc
        return parsed_date.isoformat()
    if date_only:
        return parsed.date().isoformat()
    return parsed.isoformat().replace("+00:00", "Z")


def _calendar_range(start: Any, end: Any) -> tuple[str, str]:
    today = dt.date.today()
    start_text = _parse_iso_date(start, "start", date_only=True) if start else today.isoformat()
    end_text = _parse_iso_date(end, "end", date_only=True) if end else (today + dt.timedelta(days=7)).isoformat()
    if end_text <= start_text:
        raise BridgeError("invalid_date_range", "end must be after start.")
    return start_text, end_text


def _clip(value: Any, limit: int = 8000) -> str:
    text = _text(value)
    return text[:limit] + ("\n[truncated]" if len(text) > limit else "")


_SECRET_RE = re.compile(
    r"(?ix)(bearer\s+)[^\s,;]+|"
    r"((?:password|passwd|token|secret|authorization|client[_-]?secret|access[_-]?token|refresh[_-]?token)\s*[=:]\s*)[^\s,;]+"
)
_SENSITIVE_JSON_KEYS = {
    re.sub(r"[^a-z]", "", key)
    for key in {
        "access_token",
        "refresh_token",
        "client_secret",
        "password",
        "passwd",
        "token",
        "authorization",
        "secret",
    }
}


def _redact_text(value: Any) -> str:
    return _SECRET_RE.sub(lambda match: f"{match.group(1) or match.group(2)}[REDACTED]", _text(value))


def _redact_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]"
            if re.sub(r"[^a-z]", "", str(key).lower()) in _SENSITIVE_JSON_KEYS
            else _redact_json(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_json(item) for item in value]
    return value


def _parse_json_output(stdout: str) -> Any:
    """Parse JSON even if a CLI prepended a harmless informational line."""
    text = stdout.strip().lstrip("\ufeff")
    if not text:
        raise ValueError("empty output")
    try:
        return json.loads(text)
    except json.JSONDecodeError as first_error:
        decoder = json.JSONDecoder()
        for index, char in enumerate(text):
            if char not in "[{":
                continue
            try:
                value, _ = decoder.raw_decode(text[index:])
                return value
            except json.JSONDecodeError:
                continue
        raise first_error


def _records(payload: Any) -> list[Any]:
    """Extract a list from the common JSON envelopes used by the CLIs."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, Mapping):
        for key in (
            "items",
            "data",
            "value",
            "results",
            "messages",
            "envelopes",
            "events",
            "calendars",
            "teams",
            "channels",
            "chats",
        ):
            if key in payload:
                nested = payload[key]
                if isinstance(nested, list):
                    return nested
                if isinstance(nested, Mapping):
                    return _records(nested)
        return [payload]
    return []


def _first(record: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in record and record[key] not in (None, ""):
            return record[key]
    return None


def _address(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        addresses = [_address(item) for item in value]
        joined = ", ".join(item for item in addresses if item)
        return joined or None
    if isinstance(value, Mapping):
        nested = value.get("emailAddress")
        if isinstance(nested, Mapping):
            name = _text(nested.get("name")).strip()
            address = _text(nested.get("address")).strip()
            return f"{name} <{address}>" if name and address else address or name or None
        name = _text(value.get("name")).strip()
        address = _text(value.get("address")).strip() or _text(value.get("email")).strip()
        return f"{name} <{address}>" if name and address else address or name or None
    return None


def _normalize_email(record: Any, account: "Account") -> dict[str, Any]:
    raw = record if isinstance(record, Mapping) else {"value": record}
    flags = _first(raw, "flags", "labels")
    flags_list = [str(flag) for flag in flags] if isinstance(flags, list) else []
    is_read = _first(raw, "is_read", "isRead", "read")
    if is_read is None and flags_list:
        is_read = "seen" in {flag.lower() for flag in flags_list}
    return {
        "provider": account.provider,
        "account": account.name,
        "id": _first(raw, "id", "messageId", "message_id", "uid", "uidvalidity"),
        "subject": _first(raw, "subject", "title"),
        "from": _address(_first(raw, "from", "sender", "author")),
        "to": [_address(item) for item in (_first(raw, "to", "toRecipients") or [])]
        if isinstance(_first(raw, "to", "toRecipients"), list)
        else _address(_first(raw, "to", "toRecipients")),
        "received_at": _first(raw, "received_at", "receivedAt", "receivedDateTime", "date", "datetime"),
        "sent_at": _first(raw, "sent_at", "sentAt", "sentDateTime"),
        "is_read": is_read,
        "has_attachments": _first(raw, "has_attachments", "has_attachment", "hasAttachments", "attachments")
        if not isinstance(_first(raw, "attachments"), list)
        else bool(_first(raw, "attachments")),
        "preview": _first(raw, "preview", "bodyPreview", "snippet", "body"),
        "flags": flags_list,
        "raw": _redact_json(raw),
    }


def _normalize_event(record: Any, account: "Account") -> dict[str, Any]:
    raw = record if isinstance(record, Mapping) else {"value": record}
    start = _first(raw, "start", "startDateTime", "start_at", "startTime")
    end = _first(raw, "end", "endDateTime", "end_at", "endTime")
    if isinstance(start, Mapping):
        start = _first(start, "dateTime", "date", "value")
    if isinstance(end, Mapping):
        end = _first(end, "dateTime", "date", "value")
    return {
        "provider": account.provider,
        "account": account.name,
        "id": _first(raw, "id", "uid", "iCalUId", "ical_uid"),
        "subject": _first(raw, "subject", "summary", "title", "name"),
        "start": start,
        "end": end,
        "location": _first(raw, "location", "where"),
        "organizer": _address(_first(raw, "organizer")),
        "web_link": _first(raw, "webLink", "htmlLink", "url"),
        "is_all_day": _first(raw, "isAllDay", "all_day"),
        "raw": _redact_json(raw),
    }


def _sort_key(value: Any) -> str:
    return _text(value).lower()


@dataclass(frozen=True)
class Account:
    name: str
    provider: str
    email_account: str
    calendar_account: str
    calendar_backend: str
    calendar_name: str
    username: str
    enabled: bool = True


@dataclass(frozen=True)
class Settings:
    config_path: Path
    accounts: tuple[Account, ...]
    executables: Mapping[str, str]
    himalaya_config: str = ""
    calendula_config: str = ""
    timezone: str = "UTC"
    m365_account: str = ""
    timeout_seconds: int = 45

    def executable(self, name: str) -> str:
        return _text(self.executables.get(name)).strip()


def load_settings(path: str | os.PathLike[str] | None = None) -> Settings:
    configured_path = _text(path).strip() or os.environ.get("P4_COMMS_CONFIG", "").strip()
    config_path = Path(configured_path).expanduser() if configured_path else DEFAULT_CONFIG
    if not config_path.is_file():
        raise BridgeError(
            "config_not_found",
            f"Configuration file not found: {config_path}",
            instruction=f"Copy {PROJECT_DIR / 'config.example.json'} to {config_path} and edit the account names.",
        )
    try:
        document = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BridgeError("invalid_config", f"Could not read JSON configuration: {config_path}", details=str(exc)) from exc
    if not isinstance(document, Mapping):
        raise BridgeError("invalid_config", "The configuration root must be a JSON object.")

    raw_accounts = document.get("accounts", [])
    if not isinstance(raw_accounts, list):
        raise BridgeError("invalid_config", "The accounts field must be a JSON array.")
    accounts: list[Account] = []
    names: set[str] = set()
    for index, raw in enumerate(raw_accounts):
        if not isinstance(raw, Mapping):
            raise BridgeError("invalid_config", f"Account #{index + 1} must be an object.")
        name = _safe_scalar(raw.get("name"), f"accounts[{index}].name", 100)
        if name in names:
            raise BridgeError("invalid_config", f"Duplicate account name: {name}")
        names.add(name)
        provider = _canonical_provider(raw.get("provider"))
        email_account = _safe_scalar(
            raw.get("email_account", raw.get("himalaya_account", name)),
            f"accounts[{index}].email_account",
            150,
        )
        calendar_account = _safe_scalar(
            raw.get("calendar_account", raw.get("calendula_account", name)),
            f"accounts[{index}].calendar_account",
            150,
        )
        calendar_backend = _text(
            raw.get("calendar_backend", "m365" if provider == "outlook" else "calendula")
        ).strip().lower()
        if calendar_backend not in CALENDAR_BACKENDS:
            raise BridgeError(
                "invalid_config",
                f"Account {name} calendar_backend must be m365 or calendula.",
            )
        accounts.append(
            Account(
                name=name,
                provider=provider,
                email_account=email_account,
                calendar_account=calendar_account,
                calendar_backend=calendar_backend,
                calendar_name=_optional_scalar(raw.get("calendar_name"), f"accounts[{index}].calendar_name", 200),
                username=_optional_scalar(
                    raw.get("username", raw.get("user_name")),
                    f"accounts[{index}].username",
                    320,
                    reject_leading_dash=True,
                ),
                enabled=bool(raw.get("enabled", True)),
            )
        )
    executables = document.get("executables", {})
    if not isinstance(executables, Mapping):
        raise BridgeError("invalid_config", "The executables field must be a JSON object.")
    m365_account = _text(document.get("m365_account")).strip()
    if not m365_account:
        m365_account = next((account.name for account in accounts if account.provider == "outlook"), "")
    timeout_seconds = _bounded_int(document.get("timeout_seconds"), "timeout_seconds", 5, 180, 45)
    return Settings(
        config_path=config_path,
        accounts=tuple(accounts),
        executables={str(key): _text(value) for key, value in executables.items()},
        himalaya_config=_text(document.get("himalaya_config")).strip(),
        calendula_config=_text(document.get("calendula_config")).strip(),
        timezone=_text(document.get("timezone"), "UTC").strip() or "UTC",
        m365_account=m365_account,
        timeout_seconds=timeout_seconds,
    )


class CommandRunner:
    """Run allow-listed external CLIs without invoking a shell."""

    DEFAULT_CANDIDATES = {
        "himalaya": ("himalaya", "himalaya.exe"),
        "calendula": ("calendula", "calendula.exe"),
        "m365": ("m365", "m365.cmd", "microsoft365", "microsoft365.cmd"),
    }

    def __init__(self, settings: Settings, executor: Callable[..., subprocess.CompletedProcess[str]] | None = None) -> None:
        self.settings = settings
        self.executor = executor or subprocess.run

    def resolve(self, name: str) -> tuple[str | None, dict[str, Any] | None]:
        configured = self.settings.executable(name)
        candidates = [configured] if configured else list(self.DEFAULT_CANDIDATES[name])
        if os.name == "nt" and name == "m365" and not configured:
            npm_dir = Path.home() / "AppData" / "Roaming" / "npm"
            candidates.extend([str(npm_dir / "m365.cmd"), str(npm_dir / "microsoft365.cmd")])
        for candidate in candidates:
            if not candidate:
                continue
            resolved = shutil.which(candidate)
            if resolved:
                return resolved, None
            path = Path(candidate).expanduser()
            if path.is_file():
                return str(path), None
        return None, {
            "ok": False,
            "error": "dependency_not_found",
            "dependency": name,
            "message": f"Could not find {name} on PATH.",
            "instruction": {
                "himalaya": "Install Himalaya and put its executable on PATH.",
                "calendula": "Install Calendula and put its executable on PATH.",
                "m365": "Install @pnp/cli-microsoft365 with npm and log in with m365 login.",
            }[name],
        }

    @staticmethod
    def _invocation(executable: str) -> list[str]:
        path = Path(executable)
        if os.name == "nt" and path.suffix.lower() == ".cmd":
            node = shutil.which("node")
            entry = path.parent / "node_modules" / "@pnp" / "cli-microsoft365" / "dist" / "index.js"
            if node and entry.is_file():
                return [node, str(entry)]
            powershell = shutil.which("pwsh") or shutil.which("powershell")
            script = path.with_suffix(".ps1")
            if powershell and script.is_file():
                return [powershell, "-NoProfile", "-File", str(script)]
        if os.name == "nt" and path.suffix.lower() == ".ps1":
            powershell = shutil.which("pwsh") or shutil.which("powershell")
            if powershell:
                return [powershell, "-NoProfile", "-File", str(path)]
        return [executable]

    def available(self, name: str) -> bool:
        executable, _ = self.resolve(name)
        return executable is not None

    def run_json(self, operation: str, name: str, arguments: Sequence[str]) -> dict[str, Any]:
        executable, error = self.resolve(name)
        if error:
            return {**error, "operation": operation}
        argv = [*self._invocation(executable), *arguments]
        environment = os.environ.copy()
        if name == "m365":
            environment["CLIMICROSOFT365_NOUPDATE"] = "1"
        try:
            completed = self.executor(
                argv,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=self.settings.timeout_seconds,
                shell=False,
                env=environment,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "error": "cli_timeout",
                "operation": operation,
                "dependency": name,
                "message": f"{name} did not finish in {self.settings.timeout_seconds} seconds.",
            }
        except OSError as exc:
            return {
                "ok": False,
                "error": "cli_start_failed",
                "operation": operation,
                "dependency": name,
                "message": _redact_text(str(exc)),
            }
        stdout = _text(getattr(completed, "stdout", ""))
        stderr = _text(getattr(completed, "stderr", ""))
        return_code = int(getattr(completed, "returncode", 0))
        if return_code != 0:
            return {
                "ok": False,
                "error": "cli_command_failed",
                "operation": operation,
                "dependency": name,
                "exit_code": return_code,
                "message": _redact_text(_clip(stderr or stdout, 5000)),
                "hint": "Check the provider CLI login and its own configuration file.",
            }
        if not stdout.strip():
            return {
                "ok": False,
                "error": "cli_empty_output",
                "operation": operation,
                "dependency": name,
                "message": _redact_text(_clip(stderr, 2000)),
            }
        try:
            payload = _parse_json_output(stdout)
        except ValueError:
            return {
                "ok": False,
                "error": "cli_invalid_json",
                "operation": operation,
                "dependency": name,
                "message": "The provider CLI did not return valid JSON.",
                "output": _redact_text(_clip(stdout, 5000)),
            }
        return {"ok": True, "data": _redact_json(payload)}


class HimalayaAdapter:
    def __init__(self, runner: CommandRunner) -> None:
        self.runner = runner

    def _prefix(self, account: Account) -> list[str]:
        args = ["--account", account.email_account]
        if self.runner.settings.himalaya_config:
            args.extend(["-c", self.runner.settings.himalaya_config])
        # Pimalaya v2 exposes the machine-readable switch as --json (the
        # older v1 spelling --output json is intentionally not used).
        args.extend(["--json"])
        return args

    @staticmethod
    def _mailbox_args(mailbox: str) -> list[str]:
        if not mailbox:
            return []
        return ["--mailbox", _safe_scalar(mailbox, "mailbox", 200, reject_leading_dash=True)]

    def recent(self, account: Account, mailbox: str = "") -> dict[str, Any]:
        return self.runner.run_json(
            "email.recent",
            "himalaya",
            [*self._prefix(account), "envelope", "list", *self._mailbox_args(mailbox)],
        )

    def search(self, account: Account, query: str, mailbox: str = "") -> dict[str, Any]:
        query_text = _safe_scalar(query, "query", MAX_QUERY_LENGTH)
        try:
            tokens = shlex.split(query_text, posix=True)
        except ValueError as exc:
            raise BridgeError("invalid_query", f"Could not parse query: {exc}") from exc
        if not tokens or any(token.startswith("-") for token in tokens):
            raise BridgeError("invalid_query", "Query must contain search terms and cannot contain CLI options.")
        return self.runner.run_json(
            "email.search",
            "himalaya",
            [*self._prefix(account), "envelope", "search", *self._mailbox_args(mailbox), *tokens],
        )

    def read(self, account: Account, message_id: str) -> dict[str, Any]:
        return self.runner.run_json(
            "email.read",
            "himalaya",
            [
                *self._prefix(account),
                "message",
                "read",
                _safe_scalar(message_id, "message_id", reject_leading_dash=True),
            ],
        )

    def attachments(self, account: Account, message_id: str) -> dict[str, Any]:
        return self.runner.run_json(
            "email.attachments",
            "himalaya",
            [
                *self._prefix(account),
                "attachment",
                "list",
                _safe_scalar(message_id, "message_id", reject_leading_dash=True),
            ],
        )

    def check(self, account: Account) -> dict[str, Any]:
        return self.runner.run_json(
            "email.check",
            "himalaya",
            [*self._prefix(account), "account", "check"],
        )


class CalendulaAdapter:
    def __init__(self, runner: CommandRunner) -> None:
        self.runner = runner

    def _prefix(self, account: Account) -> list[str]:
        args = ["--account", account.calendar_account]
        if self.runner.settings.calendula_config:
            args.extend(["-c", self.runner.settings.calendula_config])
        args.extend(["--json"])
        return args

    def calendars(self, account: Account) -> dict[str, Any]:
        return self.runner.run_json("calendar.list", "calendula", [*self._prefix(account), "calendar", "list"])

    def events(self, account: Account, start: str, end: str) -> dict[str, Any]:
        arguments = [*self._prefix(account), "event", "list", "--from", start, "--to", end]
        if account.calendar_name:
            arguments.extend(["--calendar", account.calendar_name])
        return self.runner.run_json("calendar.upcoming", "calendula", arguments)

    def check(self, account: Account) -> dict[str, Any]:
        return self.runner.run_json("calendar.check", "calendula", [*self._prefix(account), "calendar", "list"])


class MicrosoftAdapter:
    def __init__(self, runner: CommandRunner) -> None:
        self.runner = runner

    @staticmethod
    def _user_args(account: Account) -> list[str]:
        return ["--userName", account.username] if account.username else []

    def calendars(self, account: Account) -> dict[str, Any]:
        return self.runner.run_json(
            "calendar.list",
            "m365",
            ["outlook", "calendar", "list", *self._user_args(account), "--output", "json"],
        )

    def events(self, account: Account, start: str, end: str) -> dict[str, Any]:
        arguments = [
            "outlook",
            "event",
            "list",
            *self._user_args(account),
            "--startDateTime",
            f"{start}T00:00:00Z",
            "--endDateTime",
            f"{end}T00:00:00Z",
            "--timeZone",
            self.runner.settings.timezone,
        ]
        if account.calendar_name:
            arguments.extend(["--calendarName", account.calendar_name])
        arguments.extend(["--output", "json"])
        return self.runner.run_json("calendar.upcoming", "m365", arguments)

    def teams(self, operation: str, arguments: Sequence[str] = ()) -> dict[str, Any]:
        return self.runner.run_json(operation, "m365", [*arguments, "--output", "json"])

    def check(self) -> dict[str, Any]:
        return self.runner.run_json("m365.status", "m365", ["status", "--output", "json"])


def _error_for_account(account: Account, result: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "provider": account.provider,
        "account": account.name,
        "error": result.get("error", "provider_error"),
        "message": result.get("message", "Provider command failed."),
        "details": {
            key: value
            for key, value in result.items()
            if key not in {"ok", "error", "message"}
        },
    }


def _missing_m365_username(account: Account) -> dict[str, Any]:
    return {
        "provider": account.provider,
        "account": account.name,
        "error": "m365_username_required",
        "message": (
            "PnP CLI requires the Microsoft 365 user principal name for Outlook calendar calls. "
            "Set the account's username field in the bridge config."
        ),
    }


class UnifiedComms:
    """High-level operations exposed to the standalone CLI and WebUI tool."""

    def __init__(self, settings: Settings, runner: CommandRunner | None = None) -> None:
        self.settings = settings
        self.runner = runner or CommandRunner(settings)
        self.mail = HimalayaAdapter(self.runner)
        self.calendula = CalendulaAdapter(self.runner)
        self.microsoft = MicrosoftAdapter(self.runner)

    def accounts(self) -> dict[str, Any]:
        return {
            "ok": True,
            "accounts": [
                {
                    "name": account.name,
                    "provider": account.provider,
                    "email_account": account.email_account,
                    "calendar_account": account.calendar_account,
                    "calendar_backend": account.calendar_backend,
                    "calendar_name": account.calendar_name or None,
                    "username": account.username or None,
                    "enabled": account.enabled,
                }
                for account in self.settings.accounts
            ],
            "teams_account": self.settings.m365_account or None,
        }

    def _selected(self, selector: str, account_name: str = "") -> list[Account]:
        provider = _canonical_selector(selector)
        account_name = _text(account_name).strip()
        if account_name and any(ord(char) < 32 for char in account_name):
            raise BridgeError("invalid_account", "account contains control characters.")
        selected = [
            account
            for account in self.settings.accounts
            if account.enabled
            and (provider == "all" or account.provider == provider)
            and (not account_name or account.name == account_name)
        ]
        if account_name and not selected:
            raise BridgeError("account_not_found", f"No enabled account named {account_name!r} matches provider {provider}.")
        return selected

    @staticmethod
    def _collection(kind: str, selector: str, items: list[Any], errors: list[Any], limit: int | None = None) -> dict[str, Any]:
        if limit is not None:
            items = items[:limit]
        return {
            "ok": not errors,
            "partial": bool(items and errors),
            "kind": kind,
            "provider": _canonical_selector(selector),
            "items": items,
            "errors": errors,
            "count": len(items),
        }

    def recent_email(self, selector: str = "all", limit: int = 20, account_name: str = "", mailbox: str = "") -> dict[str, Any]:
        limit = _bounded_int(limit, "limit", 1, 100, 20)
        items: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for account in self._selected(selector, account_name):
            result = self.mail.recent(account, mailbox)
            if result.get("ok"):
                items.extend(_normalize_email(item, account) for item in _records(result.get("data")))
            else:
                errors.append(_error_for_account(account, result))
        items.sort(key=lambda item: _sort_key(item.get("received_at")), reverse=True)
        return self._collection("email.recent", selector, items, errors, limit)

    def search_email(
        self,
        query: str,
        selector: str = "all",
        limit: int = 20,
        account_name: str = "",
        mailbox: str = "",
    ) -> dict[str, Any]:
        limit = _bounded_int(limit, "limit", 1, 100, 20)
        items: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for account in self._selected(selector, account_name):
            result = self.mail.search(account, query, mailbox)
            if result.get("ok"):
                items.extend(_normalize_email(item, account) for item in _records(result.get("data")))
            else:
                errors.append(_error_for_account(account, result))
        items.sort(key=lambda item: _sort_key(item.get("received_at")), reverse=True)
        return self._collection("email.search", selector, items, errors, limit)

    def _one_account(self, selector: str, account_name: str) -> Account:
        selected = self._selected(selector, account_name)
        if len(selected) != 1:
            raise BridgeError(
                "account_required",
                "Specify an account name when reading a message or attachment because message IDs are account-local.",
            )
        return selected[0]

    def read_email(self, selector: str, message_id: str, account_name: str = "") -> dict[str, Any]:
        account = self._one_account(selector, account_name)
        result = self.mail.read(account, message_id)
        if not result.get("ok"):
            return _error_for_account(account, result)
        payload = result.get("data")
        if isinstance(payload, Mapping):
            message = _normalize_email(payload, account)
            message["raw"] = _redact_json(payload)
        else:
            message = {"provider": account.provider, "account": account.name, "id": message_id, "raw": _redact_json(payload)}
        return {"ok": True, "kind": "email.read", "message": message}

    def attachments(self, selector: str, message_id: str, account_name: str = "") -> dict[str, Any]:
        account = self._one_account(selector, account_name)
        result = self.mail.attachments(account, message_id)
        if not result.get("ok"):
            return _error_for_account(account, result)
        return {
            "ok": True,
            "kind": "email.attachments",
            "provider": account.provider,
            "account": account.name,
            "message_id": message_id,
            "items": _records(result.get("data")),
        }

    def list_calendars(self, selector: str = "all", account_name: str = "") -> dict[str, Any]:
        items: list[Any] = []
        errors: list[dict[str, Any]] = []
        for account in self._selected(selector, account_name):
            if account.calendar_backend == "m365" and not account.username:
                errors.append(_missing_m365_username(account))
                continue
            if account.calendar_backend == "m365":
                result = self.microsoft.calendars(account)
            else:
                result = self.calendula.calendars(account)
            if result.get("ok"):
                for record in _records(result.get("data")):
                    if isinstance(record, Mapping):
                        value = dict(_redact_json(record))
                        value.update({"provider": account.provider, "account": account.name})
                        items.append(value)
                    else:
                        items.append({"provider": account.provider, "account": account.name, "value": record})
            else:
                errors.append(_error_for_account(account, result))
        return self._collection("calendar.list", selector, items, errors)

    def upcoming_calendar(
        self,
        selector: str = "all",
        start: str = "",
        end: str = "",
        limit: int = 50,
        account_name: str = "",
    ) -> dict[str, Any]:
        start_text, end_text = _calendar_range(start, end)
        limit = _bounded_int(limit, "limit", 1, 200, 50)
        items: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for account in self._selected(selector, account_name):
            if account.calendar_backend == "m365" and not account.username:
                errors.append(_missing_m365_username(account))
                continue
            if account.calendar_backend == "m365":
                result = self.microsoft.events(account, start_text, end_text)
            else:
                result = self.calendula.events(account, start_text, end_text)
            if result.get("ok"):
                items.extend(_normalize_event(item, account) for item in _records(result.get("data")))
            else:
                errors.append(_error_for_account(account, result))
        items.sort(key=lambda item: _sort_key(item.get("start")))
        return self._collection("calendar.upcoming", selector, items, errors, limit)

    def teams_list(self) -> dict[str, Any]:
        result = self.microsoft.teams("teams.list", ["teams", "team", "list"])
        if not result.get("ok"):
            return result
        return {"ok": True, "kind": "teams.list", "items": _records(result.get("data"))}

    def teams_channels(self, team_id: str = "", team_name: str = "") -> dict[str, Any]:
        if bool(team_id) == bool(team_name):
            raise BridgeError("team_selector_required", "Specify exactly one of team_id or team_name.")
        option = "--teamId" if team_id else "--teamName"
        value = _safe_scalar(
            team_id or team_name,
            "team_id" if team_id else "team_name",
            reject_leading_dash=True,
        )
        result = self.microsoft.teams("teams.channels", ["teams", "channel", "list", option, value])
        if not result.get("ok"):
            return result
        return {"ok": True, "kind": "teams.channels", "items": _records(result.get("data"))}

    def teams_messages(self, team_id: str, channel_id: str, since: str = "") -> dict[str, Any]:
        team_id = _safe_scalar(team_id, "team_id", reject_leading_dash=True)
        channel_id = _safe_scalar(channel_id, "channel_id", reject_leading_dash=True)
        arguments = ["teams", "message", "list", "--teamId", team_id, "--channelId", channel_id]
        if since:
            arguments.extend(["--since", _parse_iso_date(since, "since", date_only=True)])
        result = self.microsoft.teams("teams.messages", arguments)
        if not result.get("ok"):
            return result
        return {"ok": True, "kind": "teams.messages", "items": _records(result.get("data"))}

    def teams_chats(self, chat_type: str = "") -> dict[str, Any]:
        allowed = {"oneOnOne", "group", "meeting"}
        arguments = ["teams", "chat", "list"]
        if chat_type:
            chat_type = _safe_scalar(chat_type, "chat_type", 30)
            if chat_type not in allowed:
                raise BridgeError("invalid_chat_type", "chat_type must be oneOnOne, group, or meeting.")
            arguments.extend(["--type", chat_type])
        result = self.microsoft.teams("teams.chats", arguments)
        if not result.get("ok"):
            return result
        return {"ok": True, "kind": "teams.chats", "items": _records(result.get("data"))}

    def chat_messages(self, chat_id: str) -> dict[str, Any]:
        chat_id = _safe_scalar(chat_id, "chat_id", reject_leading_dash=True)
        result = self.microsoft.teams("teams.chat_messages", ["teams", "chat", "message", "list", "--chatId", chat_id])
        if not result.get("ok"):
            return result
        return {"ok": True, "kind": "teams.chat_messages", "items": _records(result.get("data"))}

    def health(self, probe: bool = False) -> dict[str, Any]:
        dependencies = {name: self.runner.available(name) for name in ("himalaya", "calendula", "m365")}
        required: set[str] = set()
        configuration_issues: list[dict[str, str]] = []
        for account in self._selected("all"):
            required.add("himalaya")
            if account.calendar_backend == "calendula":
                required.add("calendula")
            if account.calendar_backend == "m365":
                required.add("m365")
                if not account.username:
                    configuration_issues.append(_missing_m365_username(account))
        if self.settings.m365_account:
            # Teams uses the same PnP CLI login as the Outlook calendar.
            required.add("m365")
        missing_dependencies = sorted(name for name in required if not dependencies[name])
        result: dict[str, Any] = {
            "ok": True,
            "kind": "health",
            "config": str(self.settings.config_path),
            "dependencies": dependencies,
            "required_dependencies": sorted(required),
            "missing_dependencies": missing_dependencies,
            "configuration_issues": configuration_issues,
            "ready": not missing_dependencies and not configuration_issues,
            "accounts": self.accounts()["accounts"],
            "probe": {},
        }
        if not probe:
            return result
        probe_results: dict[str, Any] = {}
        for account in self._selected("all"):
            if account.email_account:
                probe_results[f"email:{account.name}"] = self.mail.check(account)
            if account.calendar_backend == "calendula":
                probe_results[f"calendar:{account.name}"] = self.calendula.check(account)
        if self.settings.m365_account:
            probe_results["m365"] = self.microsoft.check()
        result["probe"] = {
            key: {"ok": bool(value.get("ok")), **({"error": value.get("error")} if not value.get("ok") else {})}
            for key, value in probe_results.items()
        }
        result["ok"] = all(value.get("ok", False) for value in probe_results.values()) if probe_results else result["ok"]
        result["details"] = _redact_json(probe_results)
        return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Unified read-only mail, calendar, and Teams bridge.")
    parser.add_argument("--config", default="", help="Path to bridge config JSON (or use P4_COMMS_CONFIG).")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("accounts", help="Show configured provider accounts.")
    health = subparsers.add_parser("health", help="Show dependency/config status.")
    health.add_argument("--probe", action="store_true", help="Run provider connection checks.")

    email = subparsers.add_parser("email", help="Read-only email operations.")
    email_sub = email.add_subparsers(dest="email_command", required=True)
    for name, help_text in (("recent", "List recent messages."), ("search", "Search messages.")):
        sub = email_sub.add_parser(name, help=help_text)
        sub.add_argument("--provider", default="all", choices=PROVIDER_CHOICES)
        sub.add_argument("--account", default="")
        sub.add_argument("--mailbox", default="")
        sub.add_argument("--limit", default=20, type=int)
        if name == "search":
            sub.add_argument("query")
    for name, help_text in (("read", "Read one message."), ("attachments", "List message attachments.")):
        sub = email_sub.add_parser(name, help=help_text)
        sub.add_argument("--provider", required=True, choices=PROVIDERS[1:])
        sub.add_argument("--account", required=True)
        sub.add_argument("message_id")

    calendar = subparsers.add_parser("calendar", help="Read-only calendar operations.")
    calendar_sub = calendar.add_subparsers(dest="calendar_command", required=True)
    for name in ("list", "upcoming"):
        sub = calendar_sub.add_parser(name)
        sub.add_argument("--provider", default="all", choices=PROVIDER_CHOICES)
        sub.add_argument("--account", default="")
        if name == "upcoming":
            sub.add_argument("--start", default="")
            sub.add_argument("--end", default="")
            sub.add_argument("--limit", default=50, type=int)

    teams = subparsers.add_parser("teams", help="Read-only Microsoft Teams operations.")
    teams_sub = teams.add_subparsers(dest="teams_command", required=True)
    teams_sub.add_parser("list")
    channels = teams_sub.add_parser("channels")
    channels.add_argument("--team-id", default="")
    channels.add_argument("--team-name", default="")
    messages = teams_sub.add_parser("messages")
    messages.add_argument("--team-id", required=True)
    messages.add_argument("--channel-id", required=True)
    messages.add_argument("--since", default="")
    chats = teams_sub.add_parser("chats")
    chats.add_argument("--type", default="")
    chat_messages = teams_sub.add_parser("chat-messages")
    chat_messages.add_argument("--chat-id", required=True)
    return parser


def dispatch(arguments: argparse.Namespace, comms: UnifiedComms) -> dict[str, Any]:
    if arguments.command == "accounts":
        return comms.accounts()
    if arguments.command == "health":
        return comms.health(arguments.probe)
    if arguments.command == "email":
        if arguments.email_command == "recent":
            return comms.recent_email(arguments.provider, arguments.limit, arguments.account, arguments.mailbox)
        if arguments.email_command == "search":
            return comms.search_email(arguments.query, arguments.provider, arguments.limit, arguments.account, arguments.mailbox)
        if arguments.email_command == "read":
            return comms.read_email(arguments.provider, arguments.message_id, arguments.account)
        if arguments.email_command == "attachments":
            return comms.attachments(arguments.provider, arguments.message_id, arguments.account)
    if arguments.command == "calendar":
        if arguments.calendar_command == "list":
            return comms.list_calendars(arguments.provider, arguments.account)
        return comms.upcoming_calendar(arguments.provider, arguments.start, arguments.end, arguments.limit, arguments.account)
    if arguments.command == "teams":
        if arguments.teams_command == "list":
            return comms.teams_list()
        if arguments.teams_command == "channels":
            return comms.teams_channels(arguments.team_id, arguments.team_name)
        if arguments.teams_command == "messages":
            return comms.teams_messages(arguments.team_id, arguments.channel_id, arguments.since)
        if arguments.teams_command == "chats":
            return comms.teams_chats(arguments.type)
        return comms.chat_messages(arguments.chat_id)
    raise BridgeError("invalid_command", "Unknown command.")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    arguments = parser.parse_args(argv)
    try:
        settings = load_settings(arguments.config)
        result = dispatch(arguments, UnifiedComms(settings))
    except BridgeError as exc:
        result = exc.as_dict()
    except Exception as exc:  # Keep the bridge protocol JSON-safe for callers.
        result = {"ok": False, "error": "unexpected_error", "message": _redact_text(str(exc))}
    print(json.dumps(result, ensure_ascii=False, indent=2 if arguments.pretty else None, default=str))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

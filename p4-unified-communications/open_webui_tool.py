"""
title: Unified Communications (Mail, Calendar, Teams)
description: Read-only multi-provider mail and calendar lookup plus Microsoft Teams information through the standalone P4 communications bridge.
version: 1.0.0
requirements: pydantic
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


PROVIDERS = {"all", "outlook", "gmail", "yahoo"}
BRIDGE_NAME = "p4_comms.py"


class Tools:
    """Thin Open WebUI adapter; provider logic lives in p4_comms.py."""

    class UserValves(BaseModel):
        BRIDGE_SCRIPT: str = Field(
            default="",
            description="Absolute path to p4_comms.py. Leave blank when this file is beside p4_comms.py.",
        )
        CONFIG_PATH: str = Field(
            default="",
            description="Absolute path to config.json. Leave blank to use P4_COMMS_CONFIG or the bridge folder.",
        )
        PYTHON_PATH: str = Field(
            default="",
            description="Optional Python executable. Leave blank to use the current Open WebUI Python.",
        )
        TIMEOUT_SECONDS: int = Field(default=60, ge=10, le=180)

    @staticmethod
    def _valve(user: Any, name: str, default: str = "") -> str:
        valves = (user or {}).get("valves") if isinstance(user, dict) else None
        if isinstance(valves, dict):
            return str(valves.get(name, default)).strip()
        return str(getattr(valves, name, default)).strip()

    def _script(self, user: Any) -> Path:
        configured = self._valve(user, "BRIDGE_SCRIPT")
        script = Path(configured).expanduser() if configured else Path(__file__).with_name(BRIDGE_NAME)
        if not script.is_file():
            raise ValueError(f"Unified communications bridge not found: {script}")
        return script

    def _python(self, user: Any) -> str:
        configured = self._valve(user, "PYTHON_PATH")
        return configured or sys.executable

    @staticmethod
    def _provider(provider: str) -> str:
        provider = str(provider or "all").strip().lower()
        if provider not in PROVIDERS:
            raise ValueError("provider must be all, outlook, gmail, or yahoo")
        return provider

    async def _call(self, user: Any, *arguments: str) -> dict[str, Any]:
        script = self._script(user)
        command = [self._python(user), str(script)]
        config_path = self._valve(user, "CONFIG_PATH")
        if config_path:
            command.extend(["--config", config_path])
        command.extend(arguments)
        timeout = int(self._valve(user, "TIMEOUT_SECONDS", "60") or "60")

        def run() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=max(10, min(timeout, 180)),
                shell=False,
                env={**os.environ, "P4_COMMS_CONFIG": config_path} if config_path else os.environ.copy(),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

        try:
            completed = await asyncio.to_thread(run)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "bridge_timeout", "message": f"Bridge did not finish in {timeout} seconds."}
        except OSError as exc:
            return {"ok": False, "error": "bridge_start_failed", "message": str(exc)}
        output = (completed.stdout or "").strip()
        if not output:
            return {
                "ok": False,
                "error": "bridge_empty_output",
                "message": (completed.stderr or "Bridge returned no output.")[-3000:],
            }
        try:
            result = json.loads(output)
        except json.JSONDecodeError:
            return {"ok": False, "error": "bridge_invalid_json", "message": output[-3000:]}
        if not isinstance(result, dict):
            return {"ok": False, "error": "bridge_invalid_response", "message": "Bridge response was not an object."}
        if completed.returncode != 0 and result.get("ok") is not False:
            result["ok"] = False
        return result

    async def list_email_accounts(self, __user__: Any = None) -> dict[str, Any]:
        """Show configured mail/calendar accounts without contacting providers."""
        return await self._call(__user__, "accounts")

    async def recent_email(
        self,
        provider: str = "all",
        limit: int = 20,
        account: str = "",
        mailbox: str = "",
        __user__: Any = None,
    ) -> dict[str, Any]:
        """List recent messages from one provider or all configured providers."""
        args = ["email", "recent", "--provider", self._provider(provider), "--limit", str(limit)]
        if account:
            args.extend(["--account", account])
        if mailbox:
            args.extend(["--mailbox", mailbox])
        return await self._call(__user__, *args)

    async def search_email(
        self,
        query: str,
        provider: str = "all",
        limit: int = 20,
        account: str = "",
        mailbox: str = "",
        __user__: Any = None,
    ) -> dict[str, Any]:
        """Search mail using Himalaya's query syntax, across one or all accounts."""
        args = ["email", "search", "--provider", self._provider(provider), "--limit", str(limit)]
        if account:
            args.extend(["--account", account])
        if mailbox:
            args.extend(["--mailbox", mailbox])
        args.append(query)
        return await self._call(__user__, *args)

    async def read_email(
        self,
        provider: str,
        message_id: str,
        account: str,
        __user__: Any = None,
    ) -> dict[str, Any]:
        """Read one message. Account is required because message IDs are local to each account."""
        return await self._call(
            __user__,
            "email",
            "read",
            "--provider",
            self._provider(provider),
            "--account",
            account,
            message_id,
        )

    async def list_calendars(self, provider: str = "all", account: str = "", __user__: Any = None) -> dict[str, Any]:
        """List calendars from Microsoft, Google, Yahoo, or all configured accounts."""
        args = ["calendar", "list", "--provider", self._provider(provider)]
        if account:
            args.extend(["--account", account])
        return await self._call(__user__, *args)

    async def upcoming_calendar(
        self,
        provider: str = "all",
        start: str = "",
        end: str = "",
        limit: int = 50,
        account: str = "",
        __user__: Any = None,
    ) -> dict[str, Any]:
        """List upcoming events across Microsoft, Google, Yahoo, or all calendars."""
        args = ["calendar", "upcoming", "--provider", self._provider(provider), "--limit", str(limit)]
        if start:
            args.extend(["--start", start])
        if end:
            args.extend(["--end", end])
        if account:
            args.extend(["--account", account])
        return await self._call(__user__, *args)

    async def list_teams(self, __user__: Any = None) -> dict[str, Any]:
        """List Microsoft Teams the signed-in Microsoft 365 account can access."""
        return await self._call(__user__, "teams", "list")

    async def list_team_channels(
        self,
        team_id: str = "",
        team_name: str = "",
        __user__: Any = None,
    ) -> dict[str, Any]:
        """List channels for a Microsoft Teams team by ID or display name."""
        args = ["teams", "channels"]
        if team_id:
            args.extend(["--team-id", team_id])
        if team_name:
            args.extend(["--team-name", team_name])
        return await self._call(__user__, *args)

    async def list_team_messages(
        self,
        team_id: str,
        channel_id: str,
        since: str = "",
        __user__: Any = None,
    ) -> dict[str, Any]:
        """List messages from a Teams channel; this operation is read-only."""
        args = ["teams", "messages", "--team-id", team_id, "--channel-id", channel_id]
        if since:
            args.extend(["--since", since])
        return await self._call(__user__, *args)

    async def list_teams_chats(self, chat_type: str = "", __user__: Any = None) -> dict[str, Any]:
        """List Microsoft Teams chats, optionally filtered to oneOnOne, group, or meeting."""
        args = ["teams", "chats"]
        if chat_type:
            args.extend(["--type", chat_type])
        return await self._call(__user__, *args)

    async def list_chat_messages(self, chat_id: str, __user__: Any = None) -> dict[str, Any]:
        """List messages in a Microsoft Teams chat; this operation is read-only."""
        return await self._call(__user__, "teams", "chat-messages", "--chat-id", chat_id)

    async def communications_health(self, probe: bool = False, __user__: Any = None) -> dict[str, Any]:
        """Show local dependency/config status, optionally checking provider logins."""
        args = ["health"]
        if probe:
            args.append("--probe")
        return await self._call(__user__, *args)

"""
title: Local Perforce
description: Run read-only Perforce commands on this Windows computer.
version: 1.0.0
"""

import asyncio
import json
import os
from pathlib import Path
import re
import subprocess


class Tools:
    async def inspect_perforce(
        self,
        operation: str = "info",
        target: str = "",
        directory: str = "",
        limit: int = 20,
    ) -> str:
        """Execute a real read-only p4 command on the local Windows machine.

        :param operation: One of version, info, settings, login_status, where, opened, pending_changes, submitted_changes, clients, client, describe, files, filelog, help.
        :param target: Optional single file/depot path, client name for client, numeric changelist for describe, or help topic for help. Use directory to select workspace settings.
        :param directory: Existing absolute working directory for P4CONFIG and workspace mappings. Leave empty to use the repository grandparent (the Perforce workspace in P4/AI/open-webui).
        :param limit: Maximum result count for changes, clients, files and filelog, from 1 to 100.
        :return: JSON with the actual command, directory, exit code and output. Report errors honestly; never infer success from an error.
        """
        executable = Path(r"C:\Program Files\Perforce\p4.exe")
        if directory:
            cwd = Path(directory)
        else:
            cwd = Path.cwd().parent.parent
        if not executable.is_file():
            return json.dumps({"error": "p4.exe was not found at the configured path."})
        if not cwd.is_absolute() or not cwd.is_dir():
            return json.dumps({"error": "directory must be an existing absolute local path."})
        if target.startswith("-") or any(c in target for c in "\r\n\x00"):
            return json.dumps({"error": "target must be one name or path, not command options."})
        count = str(max(1, min(int(limit), 100)))
        commands = {
            "version": ["-V"],
            "info": ["info"],
            "settings": ["set"],
            "login_status": ["login", "-s"],
            "where": ["where"],
            "opened": ["opened"],
            "pending_changes": ["changes", "-m", count, "-s", "pending"],
            "submitted_changes": ["changes", "-m", count, "-s", "submitted"],
            "clients": ["clients", "-m", count],
            "client": ["client", "-o"],
            "describe": ["describe", "-s"],
            "files": ["files", "-m", count],
            "filelog": ["filelog", "-m", count],
            "help": ["help"],
        }
        if operation not in commands:
            return json.dumps({"error": "Unsupported operation", "allowed": list(commands)})
        if operation == "describe" and not target.isdigit():
            return json.dumps({"error": "describe requires a numeric changelist target."})
        if operation in ("files", "filelog") and not target:
            return json.dumps({"error": "Supply a specific file or depot path as target."})
        args = commands[operation].copy()
        if target:
            if operation in ("version", "info", "settings", "login_status", "clients"):
                return json.dumps({"error": "This operation does not accept a target."})
            args.append(target)
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                [str(executable), *args],
                cwd=str(cwd),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=30,
                shell=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired:
            return json.dumps({"error": "p4 timed out after 30 seconds; check VPN/server access."})
        except OSError as error:
            return json.dumps({"error": str(error)})
        output = result.stdout + result.stderr
        output = re.sub(r"(?im)^(P4PASSWD\s*=).*?$", r"\1[REDACTED]", output)
        password = os.environ.get("P4PASSWD")
        if password:
            output = output.replace(password, "[REDACTED]")
        return json.dumps({
            "command": subprocess.list2cmdline([str(executable), *args]),
            "directory": str(cwd),
            "exit_code": result.returncode,
            "output": output[:20000],
            "truncated": len(output) > 20000,
        })

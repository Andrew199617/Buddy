"""
title: Reasoning Level
author: Andrew Velez
description: Sends the chat's reasoning effort (the inline thinking chip or Chat Controls) in the format each connection expects.
version: 2.0.0
required_open_webui_version: 0.11.0
"""

import logging
from typing import Optional

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)


class Filter:
    """
    Open WebUI forwards the `reasoning_effort` param unchanged, which is right
    for Chat Completions servers (llama.cpp maps it to a thinking budget) but
    not for connections set to the Responses API, which expect
    `reasoning: {"effort": ...}`. For those models this moves the value there,
    overriding any effort a preset put in its custom params (e.g. luna's max).
    Requests without `reasoning_effort` are left untouched.
    """

    class Valves(BaseModel):
        priority: int = Field(default=0, description="Filter order; lower runs first.")

    def __init__(self):
        self.valves = self.Valves()

    async def inlet(self, body: dict, __model__: Optional[dict] = None, __request__=None) -> dict:
        effort = body.get("reasoning_effort")
        if not effort or not await self._uses_responses_api(__model__ or {}, __request__):
            return body

        reasoning = body.get("reasoning")
        body["reasoning"] = {**(reasoning if isinstance(reasoning, dict) else {}), "effort": effort}
        del body["reasoning_effort"]
        return body

    async def _uses_responses_api(self, model: dict, request) -> bool:
        if model.get("owned_by") != "openai" or model.get("pipe"):
            return False

        url_idx = model.get("urlIdx")
        if url_idx is None:
            # Workspace presets (e.g. "luna") don't carry the connection; their base model does.
            base_id = (model.get("info") or {}).get("base_model_id")
            state = getattr(getattr(request, "app", None), "state", None)
            models = getattr(state, "MODELS", None) or {}
            if base_id:
                url_idx = (models.get(base_id) or {}).get("urlIdx")
        if url_idx is None:
            return False

        try:
            from open_webui.routers.openai import get_openai_connection

            _, _, api_config = await get_openai_connection(url_idx)
        except Exception as e:
            log.warning("Reasoning Level: could not read connection %s: %s", url_idx, e)
            return False
        return (api_config or {}).get("api_type") == "responses"

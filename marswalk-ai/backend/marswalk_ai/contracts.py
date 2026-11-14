"""HTTP request/response models (also used for the OpenAPI docs at /docs)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class UIItem(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str = Field(..., max_length=64)
    label: str | None = None
    keywords: list[str] = Field(default_factory=list)
    kind: str | None = None


class FrontendHandshake(BaseModel):
    """What the connected UI can do. Only these frontend capabilities are offered to the assistant."""
    client: str = Field("unknown", max_length=64, description="e.g. 'marswalk-ai-demo' or 'marswalk-2d'")
    capabilities: list[str] = Field(default_factory=list, description="Frontend capability ids the UI can execute")
    sections: list[UIItem] = Field(default_factory=list)
    layers: list[UIItem] = Field(default_factory=list)


class SessionCreate(BaseModel):
    frontend: FrontendHandshake = Field(default_factory=FrontendHandshake)


class ChatRequest(BaseModel):
    session_id: str = Field(..., max_length=64)
    message: str = Field(..., min_length=1, max_length=2000)
    client_context: dict[str, Any] | None = Field(None, description="Latest UI state: section, visible_layers, map {center, zoom}, selected {lat, lon, name}, displayed_routes")


class CommandRequest(BaseModel):
    session_id: str = Field(..., max_length=64)
    capability_id: str = Field(..., max_length=64)
    arguments: dict[str, Any] = Field(default_factory=dict)
    client_context: dict[str, Any] | None = None


class ConfirmRequest(BaseModel):
    session_id: str = Field(..., max_length=64)
    confirmation_id: str = Field(..., max_length=64)
    approve: bool


class ActionResult(BaseModel):
    session_id: str = Field(..., max_length=64)
    action_id: str = Field(..., max_length=64)
    ok: bool
    detail: str | None = Field(None, max_length=500)
    type: str | None = Field(None, max_length=40)


class LegacyChatRequest(BaseModel):
    """The contract the existing Marswalk drawer already sends (js/map.js askAssistant)."""
    question: str = Field(..., min_length=1, max_length=2000)
    context: dict[str, Any] | None = None


class FrontendAction(BaseModel):
    action_id: str
    type: Literal["display_route", "highlight_location", "zoom_to_location", "change_layer", "clear_route", "navigate_to_section"]
    capability_id: str
    payload: dict[str, Any]
    summary: str
    status: Literal["pending", "succeeded", "failed"] = "pending"
    requires_ack: bool = True


class AssistantResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    response_id: str
    session_id: str
    message: str
    intent: dict[str, Any]
    status: Literal["success", "partial", "needs_confirmation", "needs_clarification", "error"]
    tool_calls: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    frontend_actions: list[FrontendAction]
    cards: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    limitations: list[str]
    errors: list[dict[str, Any]]
    pending_confirmations: list[dict[str, Any]]
    suggestions: list[str]
    provider: dict[str, Any]
    data_modes: list[str]
    simulated: bool
    mock_components: list[str]
    timing_ms: int

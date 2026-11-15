"""
Marswalk AI Assistant - Pydantic Schemas
Request/response models for the API.
"""
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from datetime import datetime
from enum import Enum


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class ToolCallRequest(BaseModel):
    name: str
    arguments: Dict[str, Any]
    id: Optional[str] = None


class ToolResultResponse(BaseModel):
    success: bool
    data: Any = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = {}


class ChatMessage(BaseModel):
    role: MessageRole
    content: str
    tool_calls: Optional[List[ToolCallRequest]] = None
    tool_results: Optional[List[ToolResultResponse]] = None
    metadata: Optional[Dict[str, Any]] = None
    timestamp: Optional[datetime] = None


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    conversation_id: Optional[int] = None
    session_id: Optional[str] = None
    use_tools: bool = True
    stream: bool = False
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    # State of the Marswalk page sent with each message: view, selected point, routes A/B, visible layers, layer catalogue, journey
    client_context: Optional[Dict[str, Any]] = None


class ActionResult(BaseModel):
    """The page reports whether it executed a frontend action."""
    conversation_id: int
    action_id: str
    ok: bool
    detail: Optional[str] = None
    type: Optional[str] = None


class ChatResponse(BaseModel):
    message: str
    conversation_id: int
    session_id: str
    tool_calls: List[ToolCallRequest] = []
    tool_results: List[ToolResultResponse] = []
    actions: List[Dict[str, Any]] = []  # Frontend actions to execute
    metadata: Dict[str, Any] = {}


class ConversationCreate(BaseModel):
    title: Optional[str] = "New Conversation"
    session_id: Optional[str] = None


class ConversationResponse(BaseModel):
    id: int
    session_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    is_active: bool
    message_count: int = 0


class MessageResponse(BaseModel):
    id: int
    conversation_id: int
    role: str
    content: str
    tool_calls: Optional[List[Dict]] = None
    tool_results: Optional[List[Dict]] = None
    metadata: Optional[Dict] = None
    created_at: datetime


class MissionContextResponse(BaseModel):
    current_mission: str
    selected_area: Optional[Dict] = None
    current_route: Optional[Dict] = None
    route_comparisons: Optional[List] = None
    selected_layers: Optional[List] = None
    scientific_analyses: Optional[List] = None
    mission_objectives: Optional[List] = None
    map_view: str
    map_center: Optional[Dict] = None
    selected_point: Optional[Dict] = None


class MissionContextUpdate(BaseModel):
    current_mission: Optional[str] = None
    selected_area: Optional[Dict] = None
    current_route: Optional[Dict] = None
    route_comparisons: Optional[List] = None
    selected_layers: Optional[List] = None
    scientific_analyses: Optional[List] = None
    mission_objectives: Optional[List] = None
    map_view: Optional[str] = None
    map_center: Optional[Dict] = None
    selected_point: Optional[Dict] = None


class DocumentIngestRequest(BaseModel):
    filepath: str
    title: Optional[str] = None
    source_type: Optional[str] = None
    metadata: Optional[Dict] = None


class DocumentResponse(BaseModel):
    id: int
    filename: str
    filepath: str
    title: Optional[str] = None
    source_type: Optional[str] = None
    chunk_count: int
    is_processed: bool
    created_at: datetime


class SearchRequest(BaseModel):
    query: str
    top_k: int = 5
    filter: Optional[Dict] = None


class SearchResult(BaseModel):
    id: str
    content: str
    metadata: Dict
    distance: float
    relevance: float


class SearchResponse(BaseModel):
    query: str
    results: List[SearchResult]


class ModelInfo(BaseModel):
    name: str
    size: Optional[str] = None
    modified: Optional[str] = None


class ModelSwitchRequest(BaseModel):
    model: str


class HealthResponse(BaseModel):
    status: str
    version: str
    ollama_connected: bool
    model: str
    model_installed: bool = False
    knowledge_base_ready: bool
    knowledge_documents: int = 0
    knowledge_mode: str = ""
    database_connected: bool
    marswalk_backend: bool = False
    tools: int = 0


class ToolDefinitionResponse(BaseModel):
    name: str
    description: str
    parameters: Dict[str, Any]
    returns: str
    requires_permission: bool
    category: str


class ToolExecuteRequest(BaseModel):
    name: str
    arguments: Dict[str, Any]


class ToolExecuteResponse(BaseModel):
    success: bool
    data: Any = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = {}
/**
 * Marswalk AI Assistant - Frontend Application
 * JARVIS-style mission control interface
 */

// ============================================================================
// Configuration & State
// ============================================================================

// Served by the assistant backend at /app → same origin; opened as a file → the default local port.
const API_BASE = location.protocol.startsWith('http') && location.pathname.startsWith('/app') ? location.origin : 'http://127.0.0.1:8095';

let state = {
    conversationId: null,
    sessionId: null,
    messages: [],
    missionContext: {},
    pendingActions: [],
    currentTool: null,
    isStreaming: false,
    model: 'qwen3:1.7b',
    settings: {
        temperature: 0.7,
        maxTokens: 4000,
        enableTools: true,
        autoExecute: true,
        showThinking: false
    },
    connectionStatus: 'connecting'
};

// DOM Elements
const els = {};

// ============================================================================
// Initialization
// ============================================================================

document.addEventListener('DOMContentLoaded', async () => {
    cacheElements();
    setupEventListeners();
    createParticles();
    await checkConnection();
    await loadOrCreateConversation();
    updateUI();
    startStatusUpdates();
});

function cacheElements() {
    // Panels
    els.contextPanel = document.getElementById('contextPanel');
    els.actionsPanel = document.getElementById('actionsPanel');
    els.chatMessages = document.getElementById('chatMessages');
    els.chatInput = document.getElementById('chatInput');
    els.chatForm = document.getElementById('chatForm');
    els.sendBtn = document.getElementById('sendBtn');

    // Context
    els.routeInfo = document.getElementById('routeInfo');
    els.locationInfo = document.getElementById('locationInfo');
    els.layersList = document.getElementById('layersList');
    els.objectivesList = document.getElementById('objectivesList');
    els.evaStatus = document.getElementById('evaStatus');
    els.evaState = document.getElementById('evaState');
    els.evaTime = document.getElementById('evaTime');
    els.evaRad = document.getElementById('evaRad');

    // Header
    els.missionName = document.getElementById('missionName');
    els.connectionDot = document.getElementById('connectionDot');
    els.connectionText = document.getElementById('connectionText');
    els.chatModel = document.getElementById('chatModel');
    els.aiIndicator = document.getElementById('aiIndicator');
    els.footerModel = document.getElementById('footerModel');
    els.footerMemory = document.getElementById('footerMemory');
    els.footerTools = document.getElementById('footerTools');
    els.footerLatency = document.getElementById('footerLatency');

    // Modals
    els.settingsModal = document.getElementById('settingsModal');
    els.toolModal = document.getElementById('toolModal');
    els.modelSelect = document.getElementById('modelSelect');
    els.temperature = document.getElementById('temperature');
    els.tempValue = document.getElementById('tempValue');
    els.maxTokens = document.getElementById('maxTokens');
    els.enableTools = document.getElementById('enableTools');
    els.autoExecute = document.getElementById('autoExecute');
    els.showThinking = document.getElementById('showThinking');

    // Actions
    els.actionsList = document.getElementById('actionsList');
    els.toolInfo = document.getElementById('toolInfo');
    els.toolResult = document.getElementById('toolResult');
    els.toolStatus = document.getElementById('toolStatus');
}

function setupEventListeners() {
    // Chat form
    els.chatForm.addEventListener('submit', handleSendMessage);
    els.chatInput.addEventListener('input', autoResizeTextarea);
    els.chatInput.addEventListener('keydown', handleKeydown);

    // Header buttons
    document.getElementById('btnSettings').addEventListener('click', () => openModal('settingsModal'));
    document.getElementById('btnClearChat').addEventListener('click', clearConversation);
    document.getElementById('closeSettings').addEventListener('click', () => closeModal('settingsModal'));
    document.getElementById('closeToolModal').addEventListener('click', () => closeModal('toolModal'));

    // Modal backdrops
    document.querySelectorAll('.modal-backdrop').forEach(bg => {
        bg.addEventListener('click', (e) => {
            if (e.target === bg) closeAllModals();
        });
    });

    // Panel toggles
    document.getElementById('toggleContext').addEventListener('click', () => togglePanel('contextPanel'));
    document.getElementById('toggleActions').addEventListener('click', () => togglePanel('actionsPanel'));

    // Settings
    els.modelSelect.addEventListener('change', handleModelChange);
    els.temperature.addEventListener('input', (e) => {
        els.tempValue.textContent = e.target.value;
        state.settings.temperature = parseFloat(e.target.value);
    });
    els.maxTokens.addEventListener('change', (e) => {
        state.settings.maxTokens = parseInt(e.target.value);
    });
    els.enableTools.addEventListener('change', (e) => {
        state.settings.enableTools = e.target.checked;
    });
    els.autoExecute.addEventListener('change', (e) => {
        state.settings.autoExecute = e.target.checked;
    });
    els.showThinking.addEventListener('change', (e) => {
        state.settings.showThinking = e.target.checked;
    });

    // Settings actions
    document.getElementById('btnReindex').addEventListener('click', reindexKnowledgeBase);
    document.getElementById('btnIngestDocs').addEventListener('click', ingestDocuments);

    // Suggested commands (delegated)
    els.chatMessages.addEventListener('click', (e) => {
        const suggestion = e.target.closest('.suggestion');
        if (suggestion) {
            els.chatInput.value = suggestion.dataset.cmd;
            autoResizeTextarea({ target: els.chatInput });
            handleSendMessage(new Event('submit'));
        }
    });

    // Keyboard shortcuts
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closeAllModals();
        if (e.ctrlKey && e.key === 'k') {
            e.preventDefault();
            els.chatInput.focus();
        }
    });
}

// ============================================================================
// Connection & Conversation
// ============================================================================

async function checkConnection() {
    try {
        const response = await fetch(`${API_BASE}/health`);
        const data = await response.json();

        state.connectionStatus = data.status === 'healthy' ? 'connected' : 'degraded';
        updateConnectionUI(data);
    } catch (error) {
        state.connectionStatus = 'error';
        updateConnectionUI({ ollama_connected: false, model: 'unknown' });
        console.error('Connection check failed:', error);
    }
}

function updateConnectionUI(data) {
    const connected = state.connectionStatus === 'connected';

    els.connectionDot.className = 'status-dot' + (connected ? ' connected' : ' error');
    els.connectionText.textContent = connected ? 'Connected' : 'Disconnected';
    els.aiIndicator.classList.remove('thinking', 'tool-active');

    if (data.model) {
        state.model = data.model;
        els.chatModel.textContent = data.model;
        els.footerModel.textContent = data.model;
    }
    els.footerTools.textContent = String(data.tools ?? 0);
}

async function loadOrCreateConversation() {
    // Try to restore from localStorage
    const savedSessionId = localStorage.getItem('marswalk_session_id');

    if (savedSessionId) {
        try {
            const response = await fetch(`${API_BASE}/conversations`);
            const conversations = await response.json();
            const existing = conversations.find(c => c.session_id === savedSessionId);

            if (existing) {
                state.conversationId = existing.id;
                state.sessionId = existing.session_id;
                await loadMessages();
                await loadMissionContext();
                return;
            }
        } catch (error) {
            console.warn('Failed to restore conversation:', error);
        }
    }

    // Create new conversation
    await createConversation();
}

async function createConversation() {
    try {
        const response = await fetch(`${API_BASE}/conversations`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title: 'New Mission Session' })
        });
        const data = await response.json();

        state.conversationId = data.id;
        state.sessionId = data.session_id;
        localStorage.setItem('marswalk_session_id', state.sessionId);
        state.messages = [];
        updateUI();
    } catch (error) {
        console.error('Failed to create conversation:', error);
        addSystemMessage('Failed to initialize conversation. Please refresh.');
    }
}

async function loadMessages() {
    try {
        const response = await fetch(`${API_BASE}/conversations/${state.conversationId}/messages`);
        const messages = await response.json();
        state.messages = messages;
        renderMessages();
    } catch (error) {
        console.error('Failed to load messages:', error);
    }
}

async function loadMissionContext() {
    try {
        const response = await fetch(`${API_BASE}/conversations/${state.conversationId}/context`);
        const context = await response.json();
        state.missionContext = context;
        updateContextPanel();
    } catch (error) {
        console.error('Failed to load mission context:', error);
    }
}

// ============================================================================
// Chat Handling
// ============================================================================

async function handleSendMessage(event) {
    event.preventDefault();

    const message = els.chatInput.value.trim();
    if (!message || state.isStreaming) return;

    // Clear input
    els.chatInput.value = '';
    autoResizeTextarea({ target: els.chatInput });

    // Add user message to UI immediately
    addMessage('user', message);
    state.messages.push({ role: 'user', content: message });

    // Show thinking indicator
    setAIState('thinking');

    try {
        const startTime = performance.now();

        const response = await fetch(`${API_BASE}/chat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message,
                conversation_id: state.conversationId,
                use_tools: state.settings.enableTools,
                temperature: state.settings.temperature,
                max_tokens: state.settings.maxTokens
            })
        });

        const latency = Math.round(performance.now() - startTime);
        els.footerLatency.textContent = `${latency} ms`;

        const data = await response.json();

        // Update conversation ID if new
        if (data.conversation_id !== state.conversationId) {
            state.conversationId = data.conversation_id;
            state.sessionId = data.session_id;
            localStorage.setItem('marswalk_session_id', state.sessionId);
        }

        // Process tool calls
        if (data.tool_calls && data.tool_calls.length > 0) {
            await processToolCalls(data.tool_calls, data.tool_results);
        }

        // Add assistant response
        if (data.message) {
            addMessage('assistant', data.message);
            state.messages.push({ role: 'assistant', content: data.message });
        }

        // Execute frontend actions
        if (data.actions && data.actions.length > 0) {
            for (const action of data.actions) {
                if (state.settings.autoExecute) {
                    executeFrontendAction(action);
                } else {
                    queueAction(action);
                }
            }
        }

        // Update mission context
        await loadMissionContext();

    } catch (error) {
        console.error('Chat error:', error);
        addMessage('assistant', `Communication error: ${error.message}. Check connection and try again.`);
    } finally {
        setAIState('idle');
        scrollToBottom();
    }
}

async function processToolCalls(toolCalls, toolResults) {
    for (let i = 0; i < toolCalls.length; i++) {
        const toolCall = toolCalls[i];
        const toolResult = toolResults[i];

        // Show tool execution in chat
        addToolExecution(toolCall, toolResult);

        // If there's a frontend action in the result, queue it
        if (toolResult.metadata?.frontend_action && toolResult.data) {
            if (state.settings.autoExecute) {
                executeFrontendAction(toolResult.data);
            } else {
                queueAction(toolResult.data);
            }
        }
    }
}

function executeFrontendAction(action) {
    console.log('Executing frontend action:', action);

    // Standalone page: there is no Marswalk map here, so map actions only show a notice.
    // Inside Marswalk, frontend/embed/marswalk-embed.js executes them and reports the result.
    switch (action.action || action.type) {
        case 'highlight_location':
            highlightMapLocation(action);
            break;
        case 'zoom_to':
        case 'set_view':
            zoomMap(action);
            break;
        case 'fit_bounds':
            fitMapBounds(action);
            break;
        case 'display_route':
            displayRouteOnMap(action);
            break;
        case 'change_layer':
            changeMapLayer(action);
            break;
        case 'navigate_open':
        case 'navigate_toggle':
            navigateApplication(action.target, action.action.replace('navigate_', ''));
            break;
        default:
            showNotification(`Map action "${action.summary || action.type}" needs the Marswalk page (open http://127.0.0.1:8085)`, 'warning');
    }
}

function queueAction(action) {
    state.pendingActions.push({
        ...action,
        id: Date.now() + Math.random(),
        status: 'pending',
        timestamp: new Date().toISOString()
    });
    renderActions();
}

// ============================================================================
// Map Integration (Marswalk Leaflet)
// ============================================================================

// These functions communicate with the Marswalk map (window.map, window.Mars3D, etc.)
// They will work when the assistant is embedded in the Marswalk application

function highlightMapLocation(action) {
    if (typeof window.selectPoint2D === 'function') {
        window.selectPoint2D(action.lat, action.lon);
        showNotification(`Highlighted ${action.label} at ${action.lat.toFixed(4)}, ${action.lon.toFixed(4)}`);
    } else if (typeof window.Mars3D !== 'undefined' && window.Mars3D.goToLocation) {
        window.Mars3D.goToLocation(action.lat, action.lon, { altitude: 3000, select: true });
        showNotification(`Highlighted ${action.label} in 3D view`);
    } else {
        showNotification('Map integration not available - running standalone', 'warning');
    }
}

function zoomMap(action) {
    if (typeof window.map !== 'undefined') {
        window.map.setView([action.lat, action.lon], action.zoom || 15);
        showNotification(`Zoomed to ${action.lat.toFixed(4)}, ${action.lon.toFixed(4)}`);
    } else if (typeof window.Mars3D !== 'undefined') {
        window.Mars3D.goToLocation(action.lat, action.lon, { altitude: 5000 });
        showNotification(`3D view: ${action.lat.toFixed(4)}, ${action.lon.toFixed(4)}`);
    } else {
        showNotification('Map not available', 'warning');
    }
}

function fitMapBounds(action) {
    if (typeof window.map !== 'undefined' && action.bounds) {
        const bounds = L.latLngBounds(action.bounds);
        window.map.fitBounds(bounds, { padding: [50, 50] });
        showNotification('Map fitted to route bounds');
    }
}

function displayRouteOnMap(action) {
    if (typeof window.drawRoute === 'function' && action.route_data) {
        window.drawRoute(action.route_id, action.route_data);
        if (action.show_profile && typeof window.setState === 'function') {
            window.setState({ [`route${action.route_id}`]: action.route_data });
        }
        showNotification(`Route ${action.route_id} displayed on map`);
    } else {
        showNotification('Route display requires Marswalk map integration', 'warning');
    }
}

function changeMapLayer(action) {
    if (typeof window.nasaLayers !== 'undefined' && window.nasaLayers[action.layer_id]) {
        const layer = window.nasaLayers[action.layer_id];
        if (action.visible) {
            layer.addTo(window.map);
            layer.setOpacity(action.opacity || 1);
        } else {
            window.map.removeLayer(layer);
        }
        showNotification(`Layer ${action.layer_id} ${action.visible ? 'enabled' : 'disabled'}`);
    } else if (typeof window.overlays !== 'undefined' && window.overlays[action.layer_id]) {
        const overlay = window.overlays[action.layer_id];
        if (action.visible) {
            overlay.addTo(window.map);
            overlay.setOpacity(action.opacity || 1);
        } else {
            window.map.removeLayer(overlay);
        }
        showNotification(`Overlay ${action.layer_id} ${action.visible ? 'enabled' : 'disabled'}`);
    } else if (typeof window.Mars3D !== 'undefined') {
        window.Mars3D.setVisibleLayer(action.layer_id, action.visible);
        showNotification(`3D layer ${action.layer_id} ${action.visible ? 'enabled' : 'disabled'}`);
    } else {
        showNotification('Layer control requires Marswalk integration', 'warning');
    }
}

function navigateApplication(target, action = 'open') {
    if (target === '2d' || target === '3d') {
        if (typeof window.setView === 'function') {
            window.setView(target);
            showNotification(`Switched to ${target.toUpperCase()} view`);
        }
    } else if (target === 'chat') {
        const chat = document.getElementById('chat');
        if (chat) chat.hidden = action !== 'close';
    } else {
        const tab = document.querySelector(`.tab[data-tab="${target}"]`);
        if (tab) {
            tab.click();
            showNotification(`Opened ${target} tab`);
        }
    }
}

// ============================================================================
// UI Rendering
// ============================================================================

function addMessage(role, content, metadata = {}) {
    const messageEl = document.createElement('div');
    messageEl.className = `message ${role}`;

    const avatarIcon = getAvatarIcon(role);
    const senderName = getSenderName(role);
    const time = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

    messageEl.innerHTML = `
        <div class="message-avatar">
            ${avatarIcon}
        </div>
        <div class="message-content">
            <div class="message-header">
                <span class="message-sender">${senderName}</span>
                <span class="message-time">${time}</span>
            </div>
            <div class="message-text">${escapeHtml(content)}</div>
        </div>
    `;

    els.chatMessages.appendChild(messageEl);
    scrollToBottom();
    return messageEl;
}

function addSystemMessage(content) {
    return addMessage('system', content);
}

function addToolExecution(toolCall, toolResult) {
    const toolEl = document.createElement('div');
    toolEl.className = 'message tool';

    const statusClass = toolResult.success ? 'success' : 'error';
    const statusText = toolResult.success ? 'COMPLETED' : 'FAILED';

    toolEl.innerHTML = `
        <div class="message-avatar">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <rect x="3" y="3" width="7" height="7" rx="1"/>
                <rect x="14" y="3" width="7" height="7" rx="1"/>
                <rect x="3" y="14" width="7" height="7" rx="1"/>
                <rect x="14" y="14" width="7" height="7" rx="1"/>
            </svg>
        </div>
        <div class="message-content">
            <div class="tool-execution">
                <div class="tool-execution-header">
                    <span class="tool-name">${toolCall.name}</span>
                    <span class="tool-status ${statusClass}">${statusText}</span>
                </div>
                <div class="tool-args">Arguments: ${escapeHtml(JSON.stringify(toolCall.arguments, null, 2))}</div>
                ${toolResult.data ? `<div class="tool-result ${statusClass}">Result: ${escapeHtml(JSON.stringify(toolResult.data, null, 2))}</div>` : ''}
                ${toolResult.error ? `<div class="tool-result error">Error: ${escapeHtml(toolResult.error)}</div>` : ''}
            </div>
        </div>
    `;

    els.chatMessages.appendChild(toolEl);
    scrollToBottom();
}

function renderMessages() {
    els.chatMessages.innerHTML = '';

    if (state.messages.length === 0) {
        // Show welcome message
        addSystemMessage(`Marswalk AI Assistant initialized. Standing by for mission commands.`);
        return;
    }

    for (const msg of state.messages) {
        if (msg.role === 'tool') continue; // Tool messages are shown with their parent
        addMessage(msg.role, msg.content);
    }
}

function updateContextPanel() {
    const ctx = state.missionContext;

    // Route info
    if (ctx.current_route) {
        const r = ctx.current_route;
        els.routeInfo.innerHTML = `
            <div class="route-card">
                <div class="route-card-header">
                    <span class="route-label">Route ${r.route_id || 'A'}</span>
                    <span class="route-status ${r.over_limit ? 'warning' : 'planning'}">
                        ${r.over_limit ? 'OVER LIMIT' : 'PLANNING'}
                    </span>
                </div>
                <div class="route-metrics">
                    <div class="metric">
                        <span class="metric-label">Distance</span>
                        <span class="metric-value">${formatDistance(r.distance_m)}</span>
                    </div>
                    <div class="metric">
                        <span class="metric-label">Max Slope</span>
                        <span class="metric-value">${r.max_slope_deg?.toFixed(1) || '?'}°</span>
                    </div>
                    <div class="metric">
                        <span class="metric-label">Elevation Gain</span>
                        <span class="metric-value">${r.elevation_gain_m?.toFixed(0) || '?'}m</span>
                    </div>
                    <div class="metric">
                        <span class="metric-label">Coverage</span>
                        <span class="metric-value">${r.data_coverage_pct?.toFixed(0) || '?'}%</span>
                    </div>
                </div>
            </div>
        `;
    } else {
        els.routeInfo.innerHTML = '<div class="route-placeholder">No active route</div>';
    }

    // Location info
    if (ctx.selected_point) {
        const p = ctx.selected_point;
        els.locationInfo.innerHTML = `
            <div class="location-card">
                <div class="location-header">
                    <span class="location-name">${p.name || 'Selected Point'}</span>
                    <span class="location-coords">${p.lat.toFixed(6)}°N, ${p.lon.toFixed(6)}°E</span>
                </div>
                <div class="location-details">
                    <div class="metric">
                        <span class="metric-label">Elevation</span>
                        <span class="metric-value">${p.elevation_m?.toFixed(1) || '?'}m</span>
                    </div>
                    <div class="metric">
                        <span class="metric-label">Slope</span>
                        <span class="metric-value">${p.slope_deg?.toFixed(1) || '?'}°</span>
                    </div>
                    <div class="metric">
                        <span class="metric-label">Source</span>
                        <span class="metric-value">${p.source_dataset || 'Unknown'}</span>
                    </div>
                    <div class="metric">
                        <span class="metric-label">Data</span>
                        <span class="metric-value">${p.data_available ? 'Available' : 'Unavailable'}</span>
                    </div>
                </div>
            </div>
        `;
    } else {
        els.locationInfo.innerHTML = '<div class="location-placeholder">No location selected</div>';
    }

    // EVA Status
    if (ctx.eva_status) {
        els.evaState.textContent = ctx.eva_status.toUpperCase();
        els.evaState.className = `eva-value ${ctx.eva_status}`;
    }
}

function renderActions() {
    if (state.pendingActions.length === 0) {
        els.actionsList.innerHTML = '<div class="actions-placeholder">No pending actions</div>';
        return;
    }

    els.actionsList.innerHTML = state.pendingActions.map(a => `
        <div class="action-item" data-action-id="${a.id}">
            <div class="action-icon">
                ${getActionIcon(a.action)}
            </div>
            <div class="action-info">
                <div class="action-name">${formatActionName(a.action)}</div>
                <div class="action-desc">${formatActionDesc(a)}</div>
            </div>
            <div class="action-status ${a.status}">${a.status.toUpperCase()}</div>
        </div>
    `).join('');
}

function updateUI() {
    // Update footer
    els.footerMemory.textContent = `${Math.round(performance.memory?.usedJSHeapSize / 1024 / 1024 || 0)} MB`;

    // Update context panel
    updateContextPanel();
    renderActions();
}

// ============================================================================
// Utility Functions
// ============================================================================

function getAvatarIcon(role) {
    const icons = {
        user: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>',
        assistant: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>',
        system: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>',
        tool: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>'
    };
    return icons[role] || icons.assistant;
}

function getSenderName(role) {
    const names = { user: 'COMMAND', assistant: 'MARSWALK AI', system: 'SYSTEM', tool: 'TOOL' };
    return names[role] || 'UNKNOWN';
}

function getActionIcon(action) {
    const icons = {
        highlight_location: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><circle cx="12" cy="12" r="10"/><line x1="12" y1="23" x2="12" y2="23"/></svg>',
        set_view: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>',
        display_route: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>',
        change_layer: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/></svg>'
    };
    return icons[action] || '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/></svg>';
}

function formatActionName(action) {
    return action.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function formatActionDesc(action) {
    const descs = {
        highlight_location: `Mark location at ${action.lat?.toFixed(4)}, ${action.lon?.toFixed(4)}`,
        set_view: `Zoom to ${action.lat?.toFixed(4)}, ${action.lon?.toFixed(4)} @ z${action.zoom}`,
        display_route: `Show Route ${action.route_id} on map`,
        change_layer: `Layer ${action.layer_id} ${action.visible ? 'on' : 'off'}`
    };
    return descs[action.action] || 'Map action';
}

function formatDistance(meters) {
    if (meters >= 1000) return `${(meters / 1000).toFixed(2)} km`;
    return `${meters.toFixed(0)} m`;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function autoResizeTextarea(event) {
    const textarea = event.target;
    textarea.style.height = 'auto';
    textarea.style.height = Math.min(textarea.scrollHeight, 150) + 'px';
}

function handleKeydown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        els.chatForm.dispatchEvent(new Event('submit'));
    }
}

function scrollToBottom() {
    els.chatMessages.scrollTop = els.chatMessages.scrollHeight;
}

function setAIState(stateName) {
    els.aiIndicator.classList.remove('thinking', 'tool-active');

    if (stateName === 'thinking') {
        els.aiIndicator.classList.add('thinking');
    } else if (stateName === 'tool') {
        els.aiIndicator.classList.add('tool-active');
    }
}

function togglePanel(panelId) {
    const panel = document.getElementById(panelId);
    panel.classList.toggle('collapsed');
}

function openModal(modalId) {
    document.getElementById(modalId).classList.add('active');
    document.body.style.overflow = 'hidden';
}

function closeModal(modalId) {
    document.getElementById(modalId).classList.remove('active');
    document.body.style.overflow = '';
}

function closeAllModals() {
    document.querySelectorAll('.modal.active').forEach(m => m.classList.remove('active'));
    document.body.style.overflow = '';
}

function showNotification(message, type = 'info') {
    // Create temporary notification
    const notification = document.createElement('div');
    notification.className = `toast toast-${type}`;
    notification.textContent = message;
    notification.style.cssText = `
        position: fixed;
        bottom: 80px;
        right: 20px;
        padding: 12px 20px;
        background: var(--bg-panel);
        border: 1px solid var(--border-primary);
        border-radius: var(--radius-md);
        color: var(--text-primary);
        font-size: 0.8rem;
        z-index: 2000;
        animation: slideIn 0.3s ease;
        box-shadow: var(--shadow-lg);
    `;

    const colors = {
        info: 'var(--status-info)',
        warning: 'var(--status-warning)',
        error: 'var(--status-error)',
        success: 'var(--status-success)'
    };
    notification.style.borderLeftColor = colors[type] || colors.info;
    notification.style.borderLeftWidth = '3px';

    document.body.appendChild(notification);

    setTimeout(() => {
        notification.style.animation = 'fadeOut 0.3s ease forwards';
        setTimeout(() => notification.remove(), 300);
    }, 4000);
}

// Add toast animations
const style = document.createElement('style');
style.textContent = `
    @keyframes slideIn { from { opacity: 0; transform: translateX(100px); } to { opacity: 1; transform: translateX(0); } }
    @keyframes fadeOut { from { opacity: 1; } to { opacity: 0; transform: translateX(100px); } }
`;
document.head.appendChild(style);

// ============================================================================
// Settings Actions
// ============================================================================

async function handleModelChange(event) {
    const model = event.target.value;
    try {
        const response = await fetch(`${API_BASE}/models/switch`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model })
        });
        const data = await response.json();
        state.model = data.name;
        els.chatModel.textContent = data.name;
        els.footerModel.textContent = data.name;
        showNotification(`Model switched to ${data.name}`, 'success');
    } catch (error) {
        showNotification('Failed to switch model', 'error');
    }
}

async function reindexKnowledgeBase() {
    showNotification('Re-indexing knowledge base...', 'info');
    try {
        const response = await fetch(`${API_BASE}/knowledge/ingest-directory`, {
            method: 'POST'
        });
        const data = await response.json();
        showNotification(`Re-indexed ${data.length} documents`, 'success');
    } catch (error) {
        showNotification('Re-index failed', 'error');
    }
}

async function ingestDocuments() {
    showNotification('Ingesting documents from folder...', 'info');
    try {
        const response = await fetch(`${API_BASE}/knowledge/ingest-directory`, {
            method: 'POST'
        });
        const data = await response.json();
        showNotification(`Ingested ${data.length} new documents`, 'success');
    } catch (error) {
        showNotification('Ingest failed', 'error');
    }
}

async function clearConversation() {
    if (!confirm('Clear current conversation? This cannot be undone.')) return;

    try {
        await fetch(`${API_BASE}/conversations/${state.conversationId}`, {
            method: 'DELETE'
        });
        await createConversation();
        showNotification('Conversation cleared', 'success');
    } catch (error) {
        showNotification('Failed to clear conversation', 'error');
    }
}

// ============================================================================
// Background Effects
// ============================================================================

function createParticles() {
    const container = document.getElementById('particles');
    const particleCount = 30;

    for (let i = 0; i < particleCount; i++) {
        const particle = document.createElement('div');
        particle.className = 'particle';
        particle.style.left = Math.random() * 100 + '%';
        particle.style.animationDelay = Math.random() * 15 + 's';
        particle.style.animationDuration = (15 + Math.random() * 10) + 's';
        container.appendChild(particle);
    }
}

// ============================================================================
// Status Updates
// ============================================================================

function startStatusUpdates() {
    // Update connection status periodically
    setInterval(checkConnection, 30000);

    // Update memory display
    setInterval(() => {
        if (performance.memory) {
            els.footerMemory.textContent = `${Math.round(performance.memory.usedJSHeapSize / 1024 / 1024)} MB`;
        }
    }, 5000);

    // Save session periodically
    setInterval(() => {
        if (state.sessionId) {
            localStorage.setItem('marswalk_session_id', state.sessionId);
        }
    }, 10000);
}

// ============================================================================
// Export for Marswalk Integration
// ============================================================================

// These functions can be called by the Marswalk application to send context updates
window.MarswalkAI = {
    setMissionContext: (context) => {
        state.missionContext = { ...state.missionContext, ...context };
        updateContextPanel();
    },

    addMessage: (role, content) => addMessage(role, content),

    executeAction: (action) => executeFrontendAction(action),

    getState: () => ({
        conversationId: state.conversationId,
        sessionId: state.sessionId,
        missionContext: state.missionContext
    }),

    sendCommand: async (command) => {
        els.chatInput.value = command;
        await handleSendMessage(new Event('submit'));
    }
};

console.log('Marswalk AI Assistant initialized. Ready for mission.');
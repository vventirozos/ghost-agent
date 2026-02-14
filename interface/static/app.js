import { initSphere, updateSphereColor, triggerSpike, triggerPulse, setWorkingState } from './sphere.js';

// DOM Elements
const chatLog = document.getElementById('chat-log');
const chatInput = document.getElementById('chat-input');
const sendBtn = document.getElementById('send-btn');
const activityIcon = document.getElementById('activity-icon');
const fullscreenBtn = document.getElementById('fullscreen-btn');

// WebSocket connection
let ws;
const wsUrl = `ws://${window.location.host}/ws`;

// Icons that signify "Working" state
const WORKING_ICONS = new Set(['🎬', '⏳', '💭', '📋', '🧩', '🗣️', '🌐', '🔬', '🐍', '🐚', '💾', '📖', '🔍', '⬇️', '📝', '🔎', '📚', '✂️', '🧬']);
// Icons that signify "Idle" state - STRICT: Wait for Finish
const IDLE_ICONS = new Set(['🏁', '🚀', '💤', '🛑']); // Removed ✅ and ⚠️ and ❌ (Error should spike but not necessarily stop if it retries, though typically error stops. Let's keep Error as spike only, or maybe stop on error? User said wait for completion. Let's allow 🛑/🏁/💤 to stop it.)

function connectWebSocket() {
    ws = new WebSocket(wsUrl);
    ws.onopen = () => console.log("System: Connected");
    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            if (data.type === 'log') {
                const logLine = data.content;
                const icon = extractIcon(logLine);

                // Determine Flash Color based on Icon
                const flashColor = getIconColor(icon);
                triggerPulse(flashColor);

                if (icon) {
                    updateActivityIcon(icon);
                    updateStateFromIcon(icon);

                    // FIX: Force Spike for Error Icons even if log level is INFO
                    if (['❌', '⚠️', '🔥', '🚫'].includes(icon)) {
                        triggerSpike();
                    }
                }

                // Keep the flash effect
                flashActivityIcon();

                if (data.is_error) triggerSpike();
            }
        } catch (e) {
            console.error("WebSocket Error:", e);
        }
    };
    ws.onclose = () => {
        setTimeout(connectWebSocket, 3000);
    };
}

function extractIcon(logLine) {
    // Robust Emoji Regex to find the first emoji in the string
    // This matches standard emojis and extended pictographs
    const emojiRegex = /(\p{Extended_Pictographic})/u;
    const match = logLine.match(emojiRegex);

    if (match) {
        return match[0];
    }
    return null;
}

function getIconColor(icon) {
    // Cognitive / Thinking -> Cyan
    if (['💭', '📋', '🧐', '🧠'].includes(icon)) return '#00FFFF';

    // Coding / Execution -> Matrix Green
    if (['🐍', '🛠️', '✂️', '🧩', '⚙️'].includes(icon)) return '#00FF41';

    // I/O / Data -> Orange
    if (['💾', '📂', '📝', '🔍', '🔎', '📚', '📖'].includes(icon)) return '#FF8C00';

    // Network / Web -> Blue
    if (['🌐', '⬇️', '☁️', '📡'].includes(icon)) return '#1E90FF';

    // Error / Critical -> Red
    if (['❌', '⚠️', '🔥', '🚫'].includes(icon)) return '#FF0000';

    // System / Status -> White
    if (['🏁', '🚀', '🎬', '✅', '🛑'].includes(icon)) return '#FFFFFF';

    // Default -> Cyan
    return '#00FFFF';
}

function updateActivityIcon(icon) {
    activityIcon.textContent = icon;
}

function updateStateFromIcon(icon) {
    if (WORKING_ICONS.has(icon)) {
        setWorkingState(true);
        activityIcon.classList.add('working');
        activityIcon.classList.add('active');
    } else if (IDLE_ICONS.has(icon)) {
        setWorkingState(false);
        activityIcon.classList.remove('working');
        activityIcon.classList.remove('active');
    }
}

let iconTimeout;
function flashActivityIcon() {
    // Only flash if NOT working (working state keeps it on)
    if (!activityIcon.classList.contains('working')) {
        activityIcon.classList.add('active');
        clearTimeout(iconTimeout);
        iconTimeout = setTimeout(() => {
            activityIcon.classList.remove('active');
        }, 100);
    }
}

// Chat functions
function addMessage(role, text) {
    const div = document.createElement('div');
    div.className = `message ${role}`;
    div.textContent = text;
    chatLog.appendChild(div);
    chatLog.scrollTop = chatLog.scrollHeight;
    return div;
}

async function sendMessage() {
    const text = chatInput.value.trim();
    if (!text) return;

    chatInput.value = '';
    addMessage('user', text);

    if (text === '/clear') {
        chatLog.innerHTML = '';
        const msg = addMessage('system', 'Context cleared');
        setTimeout(() => { msg.remove(); }, 2000);
        return;
    }

    // Optimistic Start (Logs will confirm/deny)
    setWorkingState(true);
    activityIcon.textContent = '🎬';
    activityIcon.classList.add('working');
    activityIcon.classList.add('active');

    try {
        // FIX: Send OpenAI-compatible 'messages' array, not just 'prompt'
        const payload = {
            model: "Qwen3-4B-Instruct-2507", // Default model
            messages: [
                { role: "user", content: text }
            ]
        };

        const response = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        const data = await response.json();

        // Fix: Ignore empty string errors
        let hasError = false;
        if (data.error && data.error !== "") {
            hasError = true;
        }

        if (hasError) {
            addMessage('system', `Error: ${data.error}`);
            triggerSpike();
        } else {
            let content = "No response";

            if (data.choices && data.choices[0] && data.choices[0].message) {
                content = data.choices[0].message.content;
            } else if (data.message && data.message.content) {
                content = data.message.content;
            } else if (data.response) {
                content = data.response;
            } else if (data.content) {
                content = data.content;
            } else {
                content = JSON.stringify(data);
            }

            addMessage('agent', content);
        }

    } catch (e) {
        addMessage('system', `Network Error: ${e.message}`);
        triggerSpike();
    } finally {
        // We do NOT stop working state here immediately in 'finally' if we rely on logs?
        // Actually, for CHAT events (which might not generate logs if something fails BEFORE log),
        // we should probably ensure cleanup.
        // But if the agent is remote, we should defer to logs.
        // Let's rely on the log '🏁' or '🚀' to stop it.
        // Safety timeout?
        setTimeout(() => {
            // Only stop if we haven't seen an update recently? 
            // For now, let's trust the logs. If logs fail, we might get stuck in working state.
            // As a fallback, we can set it to false if we successfully got a response.
            // But if the agent is doing background work, logs keep coming.
            // Let's leave it to logs, but maybe force '🚀' on response success?
        }, 1000);
    }
}

sendBtn.addEventListener('click', sendMessage);
chatInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
});

if (fullscreenBtn) {
    fullscreenBtn.addEventListener('click', () => {
        console.log("Zen Mode Toggled");
        document.body.classList.toggle('zen-mode');
    });
} else {
    console.error("Fullscreen button not found!");
}

initSphere();
connectWebSocket();

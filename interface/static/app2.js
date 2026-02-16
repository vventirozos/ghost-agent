import { initSphere, triggerSpike, triggerPulse, setWorkingState } from './sphere2.js';

const chatLog = document.getElementById('chat-log');
const chatInput = document.getElementById('chat-input');
const sendBtn = document.getElementById('send-btn');
const statusText = document.getElementById('status-text');
const connectionDot = document.getElementById('connection-dot');
const activityIcon = document.getElementById('activity-icon');

let isProcessingRequest = false;
let ws;
let chatHistory = [];

function connectWebSocket() {
    ws = new WebSocket(`ws://${window.location.host}/ws`);
    ws.onopen = () => {
        statusText.textContent = "SYSTEM ONLINE";
        connectionDot.style.backgroundColor = "#00f3ff";
        connectionDot.style.boxShadow = "0 0 10px #00f3ff";
    };
    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            if (data.type === 'log') {
                triggerPulse();
                if (data.is_error) triggerSpike();
            }
        } catch (e) { console.error(e); }
    };
    ws.onclose = () => {
        statusText.textContent = "DISCONNECTED";
        connectionDot.style.backgroundColor = "#ff2a2a";
        connectionDot.style.boxShadow = "none";
        setTimeout(connectWebSocket, 3000);
    };
}

// Auto-expand textarea
chatInput.addEventListener('input', function () {
    this.style.height = 'auto';
    this.style.height = (this.scrollHeight) + 'px';
    if (this.value === '') this.style.height = 'auto';
});

function addMessage(role, text) {
    const div = document.createElement('div');
    div.className = `message ${role}`;
    div.textContent = text;
    chatLog.appendChild(div);
    scrollToBottom();
    return div;
}

function scrollToBottom() {
    requestAnimationFrame(() => { chatLog.scrollTo({ top: chatLog.scrollHeight, behavior: 'smooth' }); });
}

async function sendMessage() {
    const text = chatInput.value.trim();
    if (!text || isProcessingRequest) return;

    chatInput.value = '';
    chatInput.style.height = 'auto';
    addMessage('user', text);

    if (text === '/clear') {
        chatLog.innerHTML = '';
        chatHistory = [];
        return;
    }

    // Hard-lock visual processing state
    isProcessingRequest = true;
    setWorkingState(true);
    activityIcon.classList.add('working');

    try {
        chatHistory.push({ role: "user", content: text });
        const payload = { model: "Qwen3-4B-Instruct-2507", messages: chatHistory };
        const response = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await response.json();

        if (data.error && data.error !== "") {
            addMessage('system', `Exception: ${data.error}`);
            triggerSpike();
        } else {
            let content = data.choices?.[0]?.message?.content || data.message?.content || data.response || data.content || JSON.stringify(data);
            addMessage('agent', content);
            chatHistory.push({ role: "assistant", content: content });
        }
    } catch (e) {
        chatHistory.pop();
        addMessage('system', `Signal Lost: ${e.message}`);
        triggerSpike();
    } finally {
        isProcessingRequest = false;
        setWorkingState(false);
        activityIcon.classList.remove('working');
        setTimeout(scrollToBottom, 100);
    }
}

sendBtn.addEventListener('click', sendMessage);
chatInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});

initSphere();
connectWebSocket();

setTimeout(() => {
    const sysMsg = document.getElementById('init-msg');
    if (sysMsg) {
        sysMsg.style.transition = 'opacity 1s ease';
        sysMsg.style.opacity = '0';
        setTimeout(() => sysMsg.remove(), 1000);
    }
}, 1000);

// ─── DOM refs ────────────────────────────────────────────────────────────────
const chatBox           = document.getElementById('chat-box');
const userInput         = document.getElementById('user-input');
const sendButton        = document.getElementById('send-button');
const newChatButton     = document.getElementById('new-chat-button');
const conversationsList = document.getElementById('conversations-list');
const sidebarToggle     = document.getElementById('sidebar-toggle');
const sidebar           = document.getElementById('sidebar');
const loadingIndicator  = document.getElementById('loading-indicator');
const agentSteps        = document.getElementById('agent-steps');
const authModal         = document.getElementById('auth-modal');
const authForm          = document.getElementById('auth-form');
const authToggle        = document.getElementById('auth-toggle');
const authTitle         = document.getElementById('auth-title');
const authSubmit        = document.getElementById('auth-submit');
const userEmailDisplay  = document.getElementById('user-email');
const logoutBtn         = document.getElementById('logout-btn');

const API_BASE = 'http://localhost:8000/api';

let currentConversationId = null;
let mermaidDiagramCount   = 0;
let isLoginMode           = true;
let appInitialized        = false;   // guard against double-init

// ─── Auth helpers ─────────────────────────────────────────────────────────────
const getToken   = () => localStorage.getItem('access_token');
const setToken   = t  => localStorage.setItem('access_token', t);
const clearToken = () => localStorage.removeItem('access_token');

function authHeaders() {
    return {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${getToken()}`,
    };
}

async function apiFetch(path, options = {}) {
    const res = await fetch(`${API_BASE}${path}`, {
        ...options,
        headers: { ...authHeaders(), ...(options.headers || {}) },
    });
    if (res.status === 401) {
        clearToken();
        showAuthModal();
        throw new Error('Session expired. Please log in again.');
    }
    return res;
}

// ─── Auth modal ───────────────────────────────────────────────────────────────
function showAuthModal() {
    appInitialized = false;
    authModal.classList.remove('hidden');
    document.getElementById('main-ui').classList.add('hidden');
}

function hideAuthModal() {
    authModal.classList.add('hidden');
    document.getElementById('main-ui').classList.remove('hidden');
}

authToggle.addEventListener('click', () => {
    isLoginMode = !isLoginMode;
    authTitle.textContent  = isLoginMode ? 'Sign In' : 'Create Account';
    authSubmit.textContent = isLoginMode ? 'Sign In' : 'Register';
    authToggle.textContent = isLoginMode
        ? "Don't have an account? Register"
        : 'Already have an account? Sign in';
    document.getElementById('auth-error').textContent = '';
});

authForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const email    = document.getElementById('auth-email').value.trim();
    const password = document.getElementById('auth-password').value;
    const errorEl  = document.getElementById('auth-error');

    errorEl.textContent    = '';
    authSubmit.disabled    = true;
    authSubmit.textContent = 'Please wait...';

    try {
        let res;
        if (isLoginMode) {
            const body = new URLSearchParams({ username: email, password });
            res = await fetch(`${API_BASE}/auth/login`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body,
            });
        } else {
            res = await fetch(`${API_BASE}/auth/register`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email, password }),
            });
        }

        let data;
        try { data = await res.json(); }
        catch { throw new Error(`Server error (HTTP ${res.status})`); }

        if (!res.ok) {
            const detail = Array.isArray(data.detail)
                ? data.detail.map(d => d.msg).join(', ')
                : (data.detail || `HTTP ${res.status}`);
            throw new Error(detail);
        }

        setToken(data.access_token);
        userEmailDisplay.textContent = data.user.email;
        hideAuthModal();
        await init();

    } catch (err) {
        errorEl.textContent = err.message;
    } finally {
        // Always re-enable the button
        authSubmit.disabled    = false;
        authSubmit.textContent = isLoginMode ? 'Sign In' : 'Register';
    }
});

logoutBtn.addEventListener('click', () => {
    clearToken();
    currentConversationId   = null;
    appInitialized          = false;
    chatBox.innerHTML       = '';
    conversationsList.innerHTML = '';
    showAuthModal();
});

// ─── Loading helpers ──────────────────────────────────────────────────────────
function showLoading() {
    loadingIndicator.classList.remove('hidden');
    agentSteps.innerHTML = '';
    chatBox.scrollTop = chatBox.scrollHeight;
}
function hideLoading() {
    loadingIndicator.classList.add('hidden');
    agentSteps.innerHTML = '';
}
function addStep(text) {
    const el = document.createElement('div');
    el.classList.add('agent-step', 'current');
    el.innerHTML = `<span class="step-icon"><i class="fa-solid fa-circle-notch spinner-small"></i></span><span>${text}</span>`;
    agentSteps.appendChild(el);
    loadingIndicator.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

// ─── Init (runs once after login / token validation) ──────────────────────────
async function init() {
    if (appInitialized) return;
    appInitialized = true;

    const conversations = await fetchConversations();
    if (conversations.length === 0) {
        await createNewConversation();
    } else {
        // Render sidebar first, then load the most recent conversation
        renderSidebar(conversations);
        await loadConversationMessages(conversations[0].id);
        currentConversationId = conversations[0].id;
        renderSidebar(conversations);   // re-render to mark active
    }
}

// ─── Conversations ────────────────────────────────────────────────────────────
async function fetchConversations() {
    try {
        const res  = await apiFetch('/conversations');
        const data = await res.json();
        return data.conversations || [];
    } catch {
        return [];
    }
}

function renderSidebar(convs) {
    conversationsList.innerHTML = '';
    convs.forEach(conv => {
        const item = document.createElement('div');
        item.classList.add('conversation-item');
        if (conv.id === currentConversationId) item.classList.add('active');

        const title = document.createElement('div');
        title.classList.add('conversation-title');
        title.textContent = conv.title;

        const meta = document.createElement('div');
        meta.classList.add('conversation-meta');
        meta.textContent = `${conv.message_count} msgs • ${formatDate(new Date(conv.updated_at))}`;

        const del = document.createElement('button');
        del.classList.add('conversation-delete');
        del.innerHTML = '<i class="fa-solid fa-trash"></i>';
        del.onclick = async (e) => { e.stopPropagation(); await deleteConversation(conv.id); };

        item.append(title, meta, del);
        item.onclick = () => switchConversation(conv.id);
        conversationsList.appendChild(item);
    });
}

async function loadConversations() {
    const convs = await fetchConversations();
    renderSidebar(convs);
}

async function createNewConversation() {
    try {
        const res  = await apiFetch('/conversations', {
            method: 'POST',
            body: JSON.stringify({ title: 'New Conversation' }),
        });
        const conv = await res.json();

        currentConversationId = conv.id;
        chatBox.innerHTML     = '';
        mermaidDiagramCount   = 0;
        resetMermaid();

        await loadConversations();   // reload sidebar (new conv will be highlighted)
    } catch (err) {
        console.error('Failed to create conversation:', err);
    }
}

async function loadConversationMessages(id) {
    try {
        const res  = await apiFetch(`/conversations/${id}/messages`);
        const data = await res.json();
        chatBox.innerHTML   = '';
        mermaidDiagramCount = 0;
        resetMermaid();
        data.messages.forEach(m =>
            appendMessage(m.content, m.role === 'user' ? 'user' : 'bot', false)
        );
        // Scroll to bottom after loading history
        chatBox.scrollTop = chatBox.scrollHeight;
    } catch (err) {
        console.error('Failed to load messages:', err);
    }
}

async function switchConversation(id) {
    if (id === currentConversationId) return;   // already active, do nothing
    currentConversationId = id;
    await loadConversationMessages(id);
    await loadConversations();   // re-render sidebar to update active highlight
}

async function deleteConversation(id) {
    if (!confirm('Delete this conversation?')) return;
    try {
        await apiFetch(`/conversations/${id}`, { method: 'DELETE' });
        if (id === currentConversationId) {
            currentConversationId = null;
            await createNewConversation();
        } else {
            await loadConversations();
        }
    } catch (err) {
        console.error('Failed to delete conversation:', err);
    }
}

function resetMermaid() {
    if (window.mermaid) {
        try {
            mermaid.initialize({ startOnLoad: false, theme: 'default', securityLevel: 'loose' });
        } catch (e) { console.warn(e); }
    }
}

// ─── Send message ─────────────────────────────────────────────────────────────
const sendMessage = async () => {
    const message = userInput.value.trim();
    if (!message || !currentConversationId) return;

    appendMessage(message, 'user', true);
    userInput.value     = '';
    sendButton.disabled = true;
    userInput.disabled  = true;
    showLoading();
    addStep('Routing query...');

    try {
        const res = await apiFetch('/prompt', {
            method: 'POST',
            body: JSON.stringify({ message, conversation_id: currentConversationId }),
        });

        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || 'Request failed');
        }

        const data = await res.json();
        hideLoading();
        appendMessage(data.message, 'bot', true);
        await loadConversations();   // refresh sidebar (updated title + count)

    } catch (err) {
        hideLoading();
        appendMessage(`Error: ${err.message}`, 'bot', true);
    } finally {
        sendButton.disabled = false;
        userInput.disabled  = false;
        userInput.focus();
    }
};

// ─── Render message ───────────────────────────────────────────────────────────
const appendMessage = (message, sender, scroll = true) => {
    const el = document.createElement('div');
    el.classList.add(`${sender}-message`);

    const match = message.match(/```mermaid\n([\s\S]*?)\n```/);
    if (match) {
        const code      = match[1].trim();
        const container = document.createElement('div');
        container.classList.add('mermaid-container');

        const diagramId  = `mermaid-${mermaidDiagramCount++}`;
        const mermaidDiv = document.createElement('div');
        mermaidDiv.id    = diagramId;
        mermaidDiv.classList.add('mermaid');
        mermaidDiv.setAttribute('data-mermaid-code', code);
        mermaidDiv.textContent = code;

        const actions = document.createElement('div');
        actions.classList.add('diagram-actions');

        const copyBtn = document.createElement('button');
        copyBtn.classList.add('diagram-action-btn');
        copyBtn.title    = 'Copy Code';
        copyBtn.innerHTML = '<i class="fa-solid fa-copy"></i>';
        copyBtn.onclick  = () => {
            navigator.clipboard.writeText(code);
            copyBtn.innerHTML = '<i class="fa-solid fa-check"></i>';
            setTimeout(() => { copyBtn.innerHTML = '<i class="fa-solid fa-copy"></i>'; }, 1000);
        };

        const dlBtn = document.createElement('button');
        dlBtn.classList.add('diagram-action-btn');
        dlBtn.title    = 'Download PNG';
        dlBtn.innerHTML = '<i class="fa-solid fa-download"></i>';
        dlBtn.onclick  = () => {
            domtoimage.toPng(mermaidDiv).then(url => {
                const a    = document.createElement('a');
                a.href     = url;
                a.download = 'diagram.png';
                a.click();
            });
        };

        actions.append(copyBtn, dlBtn);
        container.append(mermaidDiv, actions);
        el.appendChild(container);
        chatBox.appendChild(el);

        setTimeout(() => {
            mermaid.run({ nodes: [mermaidDiv], suppressErrors: false })
                .catch(err => {
                    const errMsg = document.createElement('div');
                    errMsg.style.color    = '#ef4444';
                    errMsg.style.fontSize = '0.85rem';
                    errMsg.textContent    = `⚠️ ${err.message || 'Render error'}`;
                    container.insertBefore(errMsg, actions);
                });
        }, 100);

    } else {
        el.innerText = message;
        chatBox.appendChild(el);
    }

    if (scroll) chatBox.scrollTop = chatBox.scrollHeight;
};

// ─── Utilities ────────────────────────────────────────────────────────────────
function formatDate(date) {
    const diff = Date.now() - date;
    const days = Math.floor(diff / 86400000);
    if (days === 0) return 'Today';
    if (days === 1) return 'Yesterday';
    if (days < 7)  return `${days}d ago`;
    return date.toLocaleDateString();
}

// ─── Event listeners ──────────────────────────────────────────────────────────
newChatButton.addEventListener('click', createNewConversation);
sendButton.addEventListener('click', sendMessage);
userInput.addEventListener('keypress', e => { if (e.key === 'Enter') sendMessage(); });
sidebarToggle.addEventListener('click', () => sidebar.classList.toggle('hidden'));

// ─── Bootstrap: validate existing token or show login ─────────────────────────
if (getToken()) {
    fetch(`${API_BASE}/auth/me`, { headers: authHeaders() })
        .then(r => {
            if (!r.ok) throw new Error('invalid token');
            return r.json();
        })
        .then(user => {
            userEmailDisplay.textContent = user.email;
            hideAuthModal();
            init();
        })
        .catch(() => {
            clearToken();
            showAuthModal();
        });
} else {
    showAuthModal();
}

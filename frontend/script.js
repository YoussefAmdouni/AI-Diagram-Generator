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

// Auth modal — sign in / register
const authModal        = document.getElementById('auth-modal');
const authForm         = document.getElementById('auth-form');
const authToggle       = document.getElementById('auth-toggle');
const authTitle        = document.getElementById('auth-title');
const authSubmit       = document.getElementById('auth-submit');
const userEmailDisplay = document.getElementById('user-email');
const logoutBtn        = document.getElementById('logout-btn');

// Forgot password
const forgotLink          = document.getElementById('forgot-password-link');
const forgotModal         = document.getElementById('forgot-modal');
const forgotForm          = document.getElementById('forgot-form');
const forgotSubmit        = document.getElementById('forgot-submit');
const forgotBack          = document.getElementById('forgot-back');
const forgotError         = document.getElementById('forgot-error');
const forgotSuccess       = document.getElementById('forgot-success');

// Reset password
const resetModal          = document.getElementById('reset-modal');
const resetForm           = document.getElementById('reset-form');
const resetSubmit         = document.getElementById('reset-submit');
const resetError          = document.getElementById('reset-error');
const resetSuccess        = document.getElementById('reset-success');

const API_BASE = 'http://localhost:8000/api';

let currentConversationId = null;
let mermaidDiagramCount   = 0;
let isLoginMode           = true;
let appInitialized        = false;

// ─── Token storage ────────────────────────────────────────────────────────────
const getToken        = ()      => localStorage.getItem('access_token');
const getRefreshToken = ()      => localStorage.getItem('refresh_token');
const setTokens       = (a, r)  => {
    localStorage.setItem('access_token',  a);
    localStorage.setItem('refresh_token', r);
};
const clearTokens = () => {
    localStorage.removeItem('access_token');
    localStorage.removeItem('refresh_token');
};

function authHeaders() {
    return {
        'Content-Type':  'application/json',
        'Authorization': `Bearer ${getToken()}`,
    };
}

// ─── Silent token refresh ─────────────────────────────────────────────────────
let _refreshPromise = null;

async function refreshAccessToken() {
    if (_refreshPromise) return _refreshPromise;
    _refreshPromise = (async () => {
        const rt = getRefreshToken();
        if (!rt) throw new Error('No refresh token');
        const res = await fetch(`${API_BASE}/auth/refresh`, {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ refresh_token: rt }),
        });
        if (!res.ok) throw new Error('Refresh failed');
        const data = await res.json();
        setTokens(data.access_token, data.refresh_token);
        return data.access_token;
    })();
    try    { return await _refreshPromise; }
    finally { _refreshPromise = null; }
}

async function apiFetch(path, options = {}) {
    let res = await fetch(`${API_BASE}${path}`, {
        ...options,
        headers: { ...authHeaders(), ...(options.headers || {}) },
    });

    if (res.status === 401) {
        try {
            await refreshAccessToken();
            res = await fetch(`${API_BASE}${path}`, {
                ...options,
                headers: { ...authHeaders(), ...(options.headers || {}) },
            });
        } catch {
            clearTokens();
            showAuthModal();
            throw new Error('Session expired. Please log in again.');
        }
    }
    return res;
}

// ─── Auth modal ───────────────────────────────────────────────────────────────
function showAuthModal() {
    appInitialized = false;
    authModal.classList.remove('hidden');
    forgotModal.classList.add('hidden');
    resetModal.classList.add('hidden');
    document.getElementById('main-ui').classList.add('hidden');
}

function hideAuthModal() {
    authModal.classList.add('hidden');
    document.getElementById('main-ui').classList.remove('hidden');
}

authToggle.addEventListener('click', () => {
    isLoginMode = !isLoginMode;
    authTitle.textContent   = isLoginMode ? 'Sign In' : 'Create Account';
    authSubmit.textContent  = isLoginMode ? 'Sign In' : 'Register';
    authToggle.textContent  = isLoginMode
        ? "Don't have an account? Register"
        : 'Already have an account? Sign in';
    document.getElementById('auth-error').textContent = '';
    // Show/hide forgot password link based on mode
    forgotLink.style.display = isLoginMode ? 'block' : 'none';
});

authForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const email    = document.getElementById('auth-email').value.trim();
    const password = document.getElementById('auth-password').value;
    const errorEl  = document.getElementById('auth-error');

    errorEl.textContent   = '';
    authSubmit.disabled   = true;
    authSubmit.textContent = 'Please wait...';

    try {
        let res;
        if (isLoginMode) {
            const body = new URLSearchParams({ username: email, password });
            res = await fetch(`${API_BASE}/auth/login`, {
                method:  'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body,
            });
        } else {
            res = await fetch(`${API_BASE}/auth/register`, {
                method:  'POST',
                headers: { 'Content-Type': 'application/json' },
                body:    JSON.stringify({ email, password }),
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

        setTokens(data.access_token, data.refresh_token);
        userEmailDisplay.textContent = data.user.email;
        hideAuthModal();
        await init();

    } catch (err) {
        errorEl.textContent = err.message;
    } finally {
        authSubmit.disabled   = false;
        authSubmit.textContent = isLoginMode ? 'Sign In' : 'Register';
    }
});

// ─── Forgot password modal ────────────────────────────────────────────────────
forgotLink.addEventListener('click', (e) => {
    e.preventDefault();
    authModal.classList.add('hidden');
    forgotModal.classList.remove('hidden');
    forgotError.textContent   = '';
    forgotSuccess.textContent = '';
    document.getElementById('forgot-email').value = '';
});

forgotBack.addEventListener('click', () => {
    forgotModal.classList.add('hidden');
    authModal.classList.remove('hidden');
});

forgotForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const email = document.getElementById('forgot-email').value.trim();
    forgotError.textContent   = '';
    forgotSuccess.textContent = '';
    forgotSubmit.disabled     = true;
    forgotSubmit.textContent  = 'Sending...';

    try {
        const res = await fetch(`${API_BASE}/auth/forgot-password`, {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ email }),
        });
        // Always show success to avoid email enumeration
        forgotSuccess.textContent = 'If that email exists, a reset link has been sent. Check your inbox.';
        document.getElementById('forgot-email').value = '';
    } catch {
        forgotError.textContent = 'Something went wrong. Please try again.';
    } finally {
        forgotSubmit.disabled    = false;
        forgotSubmit.textContent = 'Send Reset Link';
    }
});

// ─── Reset password modal ─────────────────────────────────────────────────────
function checkForResetToken() {
    const params = new URLSearchParams(window.location.search);
    const token  = params.get('token');
    if (token) {
        authModal.classList.add('hidden');
        forgotModal.classList.add('hidden');
        resetModal.classList.remove('hidden');
        document.getElementById('reset-token-input').value = token;
    }
}

resetForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const token       = document.getElementById('reset-token-input').value;
    const newPassword = document.getElementById('reset-password').value;
    const confirmPwd  = document.getElementById('reset-password-confirm').value;

    resetError.textContent   = '';
    resetSuccess.textContent = '';

    if (newPassword !== confirmPwd) {
        resetError.textContent = 'Passwords do not match.';
        return;
    }
    if (newPassword.length < 8) {
        resetError.textContent = 'Password must be at least 8 characters.';
        return;
    }

    resetSubmit.disabled    = true;
    resetSubmit.textContent = 'Resetting...';

    try {
        const res  = await fetch(`${API_BASE}/auth/reset-password`, {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ token, new_password: newPassword }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Reset failed');

        resetSuccess.textContent = 'Password reset! Redirecting to sign in...';
        setTimeout(() => {
            // Clean URL and show login
            window.history.replaceState({}, '', window.location.pathname);
            resetModal.classList.add('hidden');
            authModal.classList.remove('hidden');
        }, 2000);
    } catch (err) {
        resetError.textContent = err.message;
    } finally {
        resetSubmit.disabled    = false;
        resetSubmit.textContent = 'Reset Password';
    }
});

// ─── Logout ───────────────────────────────────────────────────────────────────
logoutBtn.addEventListener('click', async () => {
    try {
        await apiFetch('/auth/logout', {
            method: 'POST',
            body:   JSON.stringify({ refresh_token: getRefreshToken() }),
        });
    } catch { /* clear tokens regardless */ }
    clearTokens();
    currentConversationId       = null;
    appInitialized              = false;
    chatBox.innerHTML           = '';
    conversationsList.innerHTML = '';
    showAuthModal();
});

// ─── Loading helpers ──────────────────────────────────────────────────────────
function showLoading() {
    loadingIndicator.classList.remove('hidden');
    agentSteps.innerHTML = '';
    chatBox.scrollTop    = chatBox.scrollHeight;
}
function hideLoading() {
    loadingIndicator.classList.add('hidden');
    agentSteps.innerHTML = '';
}

// ─── Init ─────────────────────────────────────────────────────────────────────
async function init() {
    if (appInitialized) return;
    appInitialized = true;

    const conversations = await fetchConversations();
    if (conversations.length === 0) {
        await createNewConversation();
    } else {
        renderSidebar(conversations);
        await loadConversationMessages(conversations[0].id);
        currentConversationId = conversations[0].id;
        renderSidebar(conversations);
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
        meta.textContent = `${conv.message_count} msgs · ${formatDate(new Date(conv.updated_at))}`;

        const del = document.createElement('button');
        del.classList.add('conversation-delete');
        del.innerHTML = '<i class="fa-solid fa-trash"></i>';
        del.onclick   = async (e) => { e.stopPropagation(); await deleteConversation(conv.id); };

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
            body:   JSON.stringify({ title: 'New Conversation' }),
        });
        const conv = await res.json();

        currentConversationId = conv.id;
        chatBox.innerHTML     = '';
        mermaidDiagramCount   = 0;
        resetMermaid();
        await loadConversations();
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
        chatBox.scrollTop = chatBox.scrollHeight;
    } catch (err) {
        console.error('Failed to load messages:', err);
    }
}

async function switchConversation(id) {
    if (id === currentConversationId) return;
    currentConversationId = id;
    await loadConversationMessages(id);
    await loadConversations();
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
        console.error('Failed to delete:', err);
    }
}

function resetMermaid() {
    if (window.mermaid) {
        try { mermaid.initialize({ startOnLoad: false, theme: 'default', securityLevel: 'loose' }); }
        catch (e) { console.warn(e); }
    }
}

// ─── Streaming send message ───────────────────────────────────────────────────
const sendMessage = async () => {
    const message = userInput.value.trim();
    if (!message || !currentConversationId) return;

    appendMessage(message, 'user', true);
    userInput.value     = '';
    sendButton.disabled = true;
    userInput.disabled  = true;
    showLoading();

    // Create streaming bot bubble
    const botBubble = document.createElement('div');
    botBubble.classList.add('bot-message', 'streaming');
    chatBox.appendChild(botBubble);
    let streamBuffer = '';

    try {
        const res = await apiFetch('/prompt/stream', {
            method: 'POST',
            body:   JSON.stringify({ message, conversation_id: currentConversationId }),
        });

        if (!res.ok) {
            const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
            throw new Error(err.detail || 'Request failed');
        }

        const reader  = res.body.getReader();
        const decoder = new TextDecoder();
        let   sseBuffer = '';

        hideLoading();

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            sseBuffer += decoder.decode(value, { stream: true });
            const lines = sseBuffer.split('\n');
            sseBuffer   = lines.pop(); // keep incomplete line

            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                try {
                    const event = JSON.parse(line.slice(6));

                    if (event.type === 'chunk') {
                        streamBuffer += event.content;
                        renderStreamedContent(botBubble, streamBuffer);
                        chatBox.scrollTop = chatBox.scrollHeight;

                    } else if (event.type === 'done') {
                        botBubble.classList.remove('streaming');
                        await loadConversations();

                    } else if (event.type === 'error') {
                        botBubble.innerText = event.message;
                        botBubble.classList.remove('streaming');
                    }
                } catch { /* malformed SSE line */ }
            }
        }

    } catch (err) {
        hideLoading();
        botBubble.innerText = `Error: ${err.message}`;
        botBubble.classList.remove('streaming');
    } finally {
        sendButton.disabled = false;
        userInput.disabled  = false;
        userInput.focus();
    }
};

// ─── Render streamed content ──────────────────────────────────────────────────
function renderStreamedContent(bubble, text) {
    const match = text.match(/```mermaid\n([\s\S]*?)\n```/);
    if (match) {
        // Full mermaid block arrived — render diagram
        if (!bubble.querySelector('.mermaid-container')) {
            bubble.innerHTML = '';
            appendMermaidToBubble(bubble, match[1].trim());
        }
    } else {
        // Plain text — stream directly
        bubble.innerText = text;
    }
}

function appendMermaidToBubble(bubble, code) {
    const container  = document.createElement('div');
    container.classList.add('mermaid-container');

    const diagramId  = `mermaid-${mermaidDiagramCount++}`;
    const mermaidDiv = document.createElement('div');
    mermaidDiv.id    = diagramId;
    mermaidDiv.classList.add('mermaid');
    mermaidDiv.textContent = code;

    const actions = document.createElement('div');
    actions.classList.add('diagram-actions');

    const copyBtn = document.createElement('button');
    copyBtn.classList.add('diagram-action-btn');
    copyBtn.title   = 'Copy Code';
    copyBtn.innerHTML = '<i class="fa-solid fa-copy"></i>';
    copyBtn.onclick = () => {
        navigator.clipboard.writeText(code);
        copyBtn.innerHTML = '<i class="fa-solid fa-check"></i>';
        setTimeout(() => { copyBtn.innerHTML = '<i class="fa-solid fa-copy"></i>'; }, 1000);
    };

    const dlBtn = document.createElement('button');
    dlBtn.classList.add('diagram-action-btn');
    dlBtn.title     = 'Download PNG';
    dlBtn.innerHTML = '<i class="fa-solid fa-download"></i>';
    dlBtn.onclick   = () => {
        domtoimage.toPng(mermaidDiv).then(url => {
            const a = document.createElement('a');
            a.href = url; a.download = 'diagram.png'; a.click();
        });
    };

    actions.append(copyBtn, dlBtn);
    container.append(mermaidDiv, actions);
    bubble.appendChild(container);

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
}

// ─── Append historical message (non-streaming) ────────────────────────────────
const appendMessage = (message, sender, scroll = true) => {
    const el = document.createElement('div');
    el.classList.add(`${sender}-message`);

    const match = message.match(/```mermaid\n([\s\S]*?)\n```/);
    if (match) {
        appendMermaidToBubble(el, match[1].trim());
    } else {
        el.innerText = message;
    }

    chatBox.appendChild(el);
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

// ─── Bootstrap ────────────────────────────────────────────────────────────────
checkForResetToken();   // check URL for ?token= before anything else

if (getToken()) {
    fetch(`${API_BASE}/auth/me`, { headers: authHeaders() })
        .then(r => {
            if (!r.ok) throw new Error('invalid');
            return r.json();
        })
        .then(async user => {
            // Try refresh if token is close to expiry or already expired
            userEmailDisplay.textContent = user.email;
            hideAuthModal();
            await init();
        })
        .catch(async () => {
            // Access token invalid — try refresh before giving up
            try {
                await refreshAccessToken();
                const r    = await fetch(`${API_BASE}/auth/me`, { headers: authHeaders() });
                const user = await r.json();
                userEmailDisplay.textContent = user.email;
                hideAuthModal();
                await init();
            } catch {
                clearTokens();
                showAuthModal();
            }
        });
} else {
    showAuthModal();
}
const chatBox = document.getElementById('chat-box');
const userInput = document.getElementById('user-input');
const sendButton = document.getElementById('send-button');
const newChatButton = document.getElementById('new-chat-button');
const conversationsList = document.getElementById('conversations-list');
const sidebarToggle = document.getElementById('sidebar-toggle');
const sidebar = document.getElementById('sidebar');
const loadingIndicator = document.getElementById('loading-indicator');
const agentSteps = document.getElementById('agent-steps');

let currentConversationId = null;
let currentSessionId = null;

const API_BASE = 'http://localhost:8000/api';

let mermaidDiagramCount = 0;  // Counter for unique diagram IDs

// Loading indicator helper functions
function showLoading() {
    if (loadingIndicator) {
        loadingIndicator.classList.remove('hidden');
        agentSteps.innerHTML = '';
        chatBox.scrollTop = chatBox.scrollHeight;
    }
}

function hideLoading() {
    if (loadingIndicator) {
        loadingIndicator.classList.add('hidden');
        agentSteps.innerHTML = '';
    }
}

function addStep(stepText, isCompleted = false) {
    if (!agentSteps) return;
    
    const stepEl = document.createElement('div');
    stepEl.classList.add('agent-step');
    if (isCompleted) {
        stepEl.classList.add('completed');
    } else {
        stepEl.classList.add('current');
    }
    
    const icon = document.createElement('span');
    icon.classList.add('step-icon');
    if (isCompleted) {
        icon.innerHTML = '<i class="fa-solid fa-check"></i>';
    } else {
        icon.innerHTML = '<i class="fa-solid fa-circle-notch spinner-small"></i>';
    }
    
    const text = document.createElement('span');
    text.textContent = stepText;
    
    stepEl.appendChild(icon);
    stepEl.appendChild(text);
    agentSteps.appendChild(stepEl);
    
    // Auto-scroll loading indicator into view
    loadingIndicator.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

// Initialize app
async function init() {
    await loadConversations();

    // Check if there are any conversations, if not create one
    const conversations = await fetchConversations();
    if (conversations.length === 0) {
        await createNewConversation();
    } else {
        // Load the most recent conversation
        await switchConversation(conversations[0].id);
    }
}

// Fetch all conversations
async function fetchConversations() {
    try {
        const response = await fetch(`${API_BASE}/conversations`);
        const data = await response.json();
        return data.conversations || [];
    } catch (error) {
        console.error('Error fetching conversations:', error);
        return [];
    }
}

// Load conversations into sidebar
async function loadConversations() {
    const conversations = await fetchConversations();
    conversationsList.innerHTML = '';

    conversations.forEach(conv => {
        const convItem = document.createElement('div');
        convItem.classList.add('conversation-item');
        if (conv.id === currentConversationId) {
            convItem.classList.add('active');
        }

        const title = document.createElement('div');
        title.classList.add('conversation-title');
        title.textContent = conv.title;

        const meta = document.createElement('div');
        meta.classList.add('conversation-meta');
        const date = new Date(conv.updated_at);
        meta.textContent = `${conv.message_count || 0} messages • ${formatDate(date)}`;

        const deleteBtn = document.createElement('button');
        deleteBtn.classList.add('conversation-delete');
        deleteBtn.innerHTML = '<i class="fa-solid fa-trash"></i>';
        deleteBtn.onclick = async (e) => {
            e.stopPropagation();
            await deleteConversation(conv.id);
        };

        convItem.appendChild(title);
        convItem.appendChild(meta);
        convItem.appendChild(deleteBtn);

        const sessionId = conv.session_id || conv.sessionId;
        convItem.onclick = () => switchConversation(conv.id, sessionId);

        conversationsList.appendChild(convItem);
    });
}

// Create a new conversation
async function createNewConversation() {
    try {
        const response = await fetch(`${API_BASE}/conversations`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                title: 'New Conversation'
            })
        });

        const newConv = await response.json();
        currentConversationId = newConv.id;
        currentSessionId = newConv.session_id;
        
        // Clear chat and reset mermaid state
        chatBox.innerHTML = '';
        mermaidDiagramCount = 0;  // Reset diagram counter
        
        // Reset Mermaid's internal state
        if (window.mermaid) {
            try {
                mermaid.initialize({ 
                    startOnLoad: false,
                    theme: 'default',
                    securityLevel: 'loose'
                });
            } catch (e) {
                console.warn('Could not reset mermaid:', e);
            }
        }

        await loadConversations();
    } catch (error) {
        console.error('Error creating conversation:', error);
    }
}

// Switch to a different conversation
async function switchConversation(conversationId, sessionId) {
    currentConversationId = conversationId;
    currentSessionId = sessionId;
    
    // Clear chat and reset mermaid state
    chatBox.innerHTML = '';
    mermaidDiagramCount = 0;  // Reset diagram counter
    
    // Reset Mermaid's internal state to prevent diagram conflicts
    if (window.mermaid) {
        try {
            // Reset mermaid's state
            mermaid.initialize({ 
                startOnLoad: false,
                theme: 'default',
                securityLevel: 'loose'
            });
            // Clear any cached diagrams
            if (mermaid.contentLoaded) {
                mermaid.contentLoaded = [];
            }
        } catch (e) {
            console.warn('Could not reset mermaid:', e);
        }
    }

    // Load messages for this conversation
    try {
        const params = new URLSearchParams();
        if (sessionId) params.append('session_id', sessionId);
        const response = await fetch(`${API_BASE}/conversations/${conversationId}/messages?${params}`);
        const data = await response.json();

        data.messages.forEach(msg => {
            appendMessage(msg.content, msg.type, false);
        });
    } catch (error) {
        console.error('Error loading messages:', error);
    }

    await loadConversations();
}

// Delete a conversation
async function deleteConversation(conversationId) {
    if (!confirm('Are you sure you want to delete this conversation?')) {
        return;
    }

    try {
        const response = await fetch(`${API_BASE}/conversations/${conversationId}`, {
            method: 'DELETE'
        });

        if (!response.ok) {
            const errorData = await response.json();
            alert(`Cannot delete conversation: ${errorData.detail || 'Unknown error'}`);
            return;
        }

        // If we deleted the current conversation, create a new one
        if (conversationId === currentConversationId) {
            await createNewConversation();
        } else {
            await loadConversations();
        }
    } catch (error) {
        console.error('Error deleting conversation:', error);
        alert('Error deleting conversation. Please try again.');
    }
}

// Format date for display
function formatDate(date) {
    const now = new Date();
    const diff = now - date;
    const days = Math.floor(diff / (1000 * 60 * 60 * 24));

    if (days === 0) {
        return 'Today';
    } else if (days === 1) {
        return 'Yesterday';
    } else if (days < 7) {
        return `${days} days ago`;
    } else {
        return date.toLocaleDateString();
    }
}

// Send message
const sendMessage = async () => {
    const message = userInput.value;
    if (!message || !currentConversationId) return;

    appendMessage(message, 'user', true);
    userInput.value = '';
    
    // Disable send button while processing
    sendButton.disabled = true;
    userInput.disabled = true;
    
    // Show loading indicator
    showLoading();
    addStep('Routing query...');

    try {
        const response = await fetch(`${API_BASE}/prompt`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                message,
                conversation_id: currentConversationId,
                session_id: currentSessionId
            })
        });

        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || 'Failed to get response from the bot.');
        }

        const data = await response.json();
        const botMessage = data.message;
        
        // Hide loading indicator before showing bot message
        setTimeout(() => {
            hideLoading();
            appendMessage(botMessage, 'bot', true);
            // Reload conversations to update metadata
            loadConversations();
            
            // Re-enable send button
            sendButton.disabled = false;
            userInput.disabled = false;
            userInput.focus();
        }, 500);
    } catch (error) {
        console.error(error);
        hideLoading();
        appendMessage(`Sorry, something went wrong: ${error.message}`, 'bot', true);
        
        // Re-enable send button
        sendButton.disabled = false;
        userInput.disabled = false;
        userInput.focus();
    }
};

// Append message to chat
const appendMessage = (message, sender, scroll = true) => {
    const messageElement = document.createElement('div');
    messageElement.classList.add(`${sender}-message`);

    const mermaidRegex = /```mermaid\n([\s\S]*?)\n```/;
    const match = message.match(mermaidRegex);

    if (match && match[1]) {
        const mermaidCode = match[1].trim();

        const mermaidContainer = document.createElement('div');
        mermaidContainer.classList.add('mermaid-container');

        // Use unique counter instead of timestamp to avoid collisions
        const diagramId = `mermaid-${mermaidDiagramCount++}`;
        const mermaidDiv = document.createElement('div');
        mermaidDiv.id = diagramId;
        mermaidDiv.classList.add('mermaid');
        mermaidDiv.setAttribute('data-mermaid-code', mermaidCode);
        mermaidDiv.textContent = mermaidCode;

        mermaidContainer.appendChild(mermaidDiv);

        // Create action buttons container for this diagram
        const actionsContainer = document.createElement('div');
        actionsContainer.classList.add('diagram-actions');

        const copyBtn = document.createElement('button');
        copyBtn.classList.add('diagram-action-btn');
        copyBtn.title = 'Copy Mermaid Code';
        copyBtn.innerHTML = '<i class="fa-solid fa-copy"></i>';
        copyBtn.onclick = () => {
            navigator.clipboard.writeText(mermaidCode);
            // Optional: Show a brief "Copied!" feedback
            const originalHTML = copyBtn.innerHTML;
            copyBtn.innerHTML = '<i class="fa-solid fa-check"></i>';
            setTimeout(() => {
                copyBtn.innerHTML = originalHTML;
            }, 1000);
        };

        const downloadBtn = document.createElement('button');
        downloadBtn.classList.add('diagram-action-btn');
        downloadBtn.title = 'Download as Image';
        downloadBtn.innerHTML = '<i class="fa-solid fa-download"></i>';
        downloadBtn.onclick = () => {
            domtoimage.toPng(mermaidDiv)
                .then(function (dataUrl) {
                    const link = document.createElement('a');
                    link.download = 'mermaid-diagram.png';
                    link.href = dataUrl;
                    link.click();
                });
        };

        actionsContainer.appendChild(copyBtn);
        actionsContainer.appendChild(downloadBtn);
        mermaidContainer.appendChild(actionsContainer);

        messageElement.appendChild(mermaidContainer);
        chatBox.appendChild(messageElement);

        // Render Mermaid diagram with proper cleanup
        if (window.mermaid) {
            // Use a small delay to ensure DOM is ready
            setTimeout(() => {
                mermaid.run({
                    nodes: [mermaidDiv],
                    suppressErrors: false
                }).then(() => {
                    // Check if the diagram actually rendered (mermaid replaces the text content with SVG)
                    if (!mermaidDiv.querySelector('svg')) {
                        // Rendering failed, show error message
                        const errorMsg = document.createElement('div');
                        errorMsg.style.color = '#ef4444';
                        errorMsg.style.fontSize = '0.85rem';
                        errorMsg.style.marginTop = '8px';
                        errorMsg.textContent = '⚠️ Failed to render diagram';
                        mermaidContainer.insertBefore(errorMsg, actionsContainer);
                    }
                }).catch((error) => {
                    // Handle rendering errors
                    console.error('Mermaid rendering error:', error);
                    const errorMsg = document.createElement('div');
                    errorMsg.style.color = '#ef4444';
                    errorMsg.style.fontSize = '0.85rem';
                    errorMsg.style.marginTop = '8px';
                    errorMsg.textContent = `⚠️ Diagram error: ${error.message || 'Invalid syntax'}`;
                    mermaidContainer.insertBefore(errorMsg, actionsContainer);
                });
            }, 100);
        }

    } else {
        messageElement.innerText = message;
        chatBox.appendChild(messageElement);
    }

    if (scroll) {
        chatBox.scrollTop = chatBox.scrollHeight;
    }
};

// Copy and download functionality is now handled per-diagram in the appendMessage function

// New chat button
newChatButton.addEventListener('click', createNewConversation);

// Send button and enter key
sendButton.addEventListener('click', sendMessage);
userInput.addEventListener('keypress', (event) => {
    if (event.key === 'Enter') {
        sendMessage();
    }
});

// Sidebar toggle for mobile
sidebarToggle.addEventListener('click', () => {
    sidebar.classList.toggle('hidden');
});

// Initialize the app
init();

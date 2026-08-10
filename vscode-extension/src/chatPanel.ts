/**
 * Chat Panel — sidebar webview with streaming chat.
 *
 * Provides a Copilot-like chat experience in the VS Code sidebar.
 * Features:
 * - Streaming responses (SSE → webview postMessage)
 * - Context-aware: includes current file + open tabs
 * - Markdown rendering in webview
 * - Code blocks with "Apply" / "Copy" buttons
 * - Message history (per-session, ephemeral)
 *
 * Architecture: VS Code extension ↔ webview postMessage bridge.
 * The webview is a lightweight HTML page with embedded JS (no React/build step).
 */

import * as vscode from 'vscode';
import * as path from 'path';
import { HttpClient } from './httpClient';
import { ContextProvider } from './contextProvider';

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: number;
}

export class ChatPanelProvider implements vscode.WebviewViewProvider {
  static readonly viewType = 'hermes.chatView';

  private _view?: vscode.WebviewView;
  private _messages: ChatMessage[] = [];
  private _activeRequest: AbortController | null = null;

  constructor(
    private client: HttpClient,
    private ctx: ContextProvider,
    private _extensionUri: vscode.Uri
  ) {}

  // ── Webview lifecycle ─────────────────────────────

  resolveWebviewView(
    webviewView: vscode.WebviewView,
    _resolveCtx: vscode.WebviewViewResolveContext,
    _token: vscode.CancellationToken
  ): void {
    this._view = webviewView;

    const webview = webviewView.webview;
    webview.options = {
      enableScripts: true,
      localResourceRoots: [
        vscode.Uri.joinPath(this._extensionUri, 'dist', 'webview'),
        vscode.Uri.joinPath(this._extensionUri, 'src', 'webview'),
      ],
    };

    webview.html = this._getHtml(webview);

    // Handle messages from webview
    webview.onDidReceiveMessage(async (msg) => {
      switch (msg.type) {
        case 'sendMessage':
          await this._handleUserMessage(msg.text);
          break;
        case 'cancelRequest':
          this._activeRequest?.abort();
          break;
        case 'clearChat':
          this._messages = [];
          this._postMessage({ type: 'clearChat' });
          break;
        case 'applyCode':
          await this._applyCodeToEditor(msg.code);
          break;
        case 'ready':
          // Webview is ready — restore history
          this._postMessage({
            type: 'restoreHistory',
            messages: this._messages,
          });
          break;
      }
    });

    // Handle visibility changes (keep-alive optimization)
    webviewView.onDidChangeVisibility(() => {
      if (webviewView.visible) {
        // Refresh if needed
      }
    });
  }

  // ── Chat handling ─────────────────────────────────

  private async _handleUserMessage(text: string): Promise<void> {
    if (!text.trim()) return;

    const userMsg: ChatMessage = {
      id: this._generateId(),
      role: 'user',
      content: text,
      timestamp: Date.now(),
    };
    this._messages.push(userMsg);
    this._postMessage({ type: 'addMessage', message: userMsg });

    // Cancel any in-flight request
    this._activeRequest?.abort();
    this._activeRequest = new AbortController();
    const signal = this._activeRequest.signal;

    // Build context
    const editor = vscode.window.activeTextEditor;
    let ctx: Awaited<ReturnType<typeof this.ctx.buildChatContext>> = {
      snippets: [],
      diagnostics: '',
    };
    try {
      ctx = await this.ctx.buildChatContext(
        editor?.document,
        editor?.selection
      );
    } catch {
      // Context gathering is best-effort
    }

    // Format context for system prompt
    const contextStr = ctx.snippets
      .map((s) => `// File: ${vscode.workspace.asRelativePath(s.uri)}\n\`\`\`\n${s.content.slice(0, 3000)}\n\`\`\``)
      .join('\n\n');

    const systemPrompt = [
      'You are a helpful AI coding assistant running locally.',
      'You help with code questions, debugging, refactoring, and explanations.',
      'Keep responses concise but thorough.',
      'When providing code, surround it with triple backticks and the language name.',
      '',
      contextStr ? `Current context:\n${contextStr}` : '',
      ctx.diagnostics ? `\nFile errors/warnings:\n${ctx.diagnostics}` : '',
    ]
      .filter(Boolean)
      .join('\n');

    // Build message history for the API (last 20 messages + system)
    const recentMessages = this._messages.slice(-20);
    const apiMessages = [
      { role: 'system' as const, content: systemPrompt },
      ...recentMessages.map((m) => ({
        role: m.role as 'user' | 'assistant',
        content: m.content,
      })),
    ];

    // Create assistant message placeholder
    const assistantMsg: ChatMessage = {
      id: this._generateId(),
      role: 'assistant',
      content: '',
      timestamp: Date.now(),
    };
    this._messages.push(assistantMsg);
    this._postMessage({ type: 'addMessage', message: assistantMsg });

    // Stream the response
    try {
      let fullContent = '';
      const stream = this.client.chatStream(apiMessages, signal);

      for await (const chunk of stream) {
        if (signal.aborted) break;
        fullContent += chunk;
        assistantMsg.content = fullContent;
        this._postMessage({
          type: 'updateMessage',
          messageId: assistantMsg.id,
          content: fullContent,
        });
      }
    } catch (err: any) {
      if (err.name === 'AbortError') {
        assistantMsg.content += '\n\n*[Request cancelled]*';
      } else {
        assistantMsg.content += `\n\n*Error: ${err.message}*`;
      }
      this._postMessage({
        type: 'updateMessage',
        messageId: assistantMsg.id,
        content: assistantMsg.content,
      });
    }
  }

  private async _applyCodeToEditor(code: string): Promise<void> {
    const editor = vscode.window.activeTextEditor;
    if (!editor) {
      vscode.window.showWarningMessage('No active editor to apply code to');
      return;
    }

    const selection = editor.selection;
    if (selection.isEmpty) {
      // Insert at cursor
      await editor.edit((edit) => edit.insert(selection.active, code));
    } else {
      // Replace selection
      await editor.edit((edit) => edit.replace(selection, code));
    }
  }

  // ── Webview communication ─────────────────────────

  private _postMessage(msg: any): void {
    this._view?.webview.postMessage(msg);
  }

  private _generateId(): string {
    return `msg_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  }

  // ── HTML ──────────────────────────────────────────

  private _getHtml(webview: vscode.Webview): string {
    // Inline everything — no build step needed for the webview
    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Hermes Chat</title>
  <style>
    :root {
      --bg: var(--vscode-editor-background, #1e1e1e);
      --fg: var(--vscode-editor-foreground, #d4d4d4);
      --border: var(--vscode-panel-border, #3c3c3c);
      --input-bg: var(--vscode-input-background, #3c3c3c);
      --input-fg: var(--vscode-input-foreground, #cccccc);
      --btn-bg: var(--vscode-button-background, #0078d4);
      --btn-fg: var(--vscode-button-foreground, #ffffff);
      --code-bg: var(--vscode-textCodeBlock-background, #1a1a2e);
      --user-msg-bg: var(--vscode-textBlockQuote-background, #2a2a3e);
      --assistant-msg-bg: transparent;
      --link: var(--vscode-textLink-foreground, #3794ff);
    }

    * { margin: 0; padding: 0; box-sizing: border-box; }

    body {
      background: var(--bg);
      color: var(--fg);
      font-family: var(--vscode-font-family, -apple-system, sans-serif);
      font-size: var(--vscode-font-size, 13px);
      line-height: 1.5;
      height: 100vh;
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }

    #messages {
      flex: 1;
      overflow-y: auto;
      padding: 12px;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }

    .message {
      padding: 10px 14px;
      border-radius: 8px;
      max-width: 100%;
      word-wrap: break-word;
      animation: fadeIn 0.15s ease-in;
    }
    @keyframes fadeIn { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: translateY(0); } }

    .message.user {
      background: var(--user-msg-bg);
      align-self: flex-end;
      max-width: 85%;
    }
    .message.assistant {
      background: var(--assistant-msg-bg);
      align-self: flex-start;
      max-width: 100%;
    }

    .message pre {
      background: var(--code-bg);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 10px;
      overflow-x: auto;
      margin: 8px 0;
      position: relative;
    }
    .message code {
      font-family: var(--vscode-editor-font-family, 'Menlo', 'Monaco', monospace);
      font-size: 12px;
    }

    .code-actions {
      display: flex;
      gap: 6px;
      margin-top: 6px;
    }
    .code-actions button {
      background: var(--btn-bg);
      color: var(--btn-fg);
      border: none;
      border-radius: 4px;
      padding: 4px 10px;
      font-size: 11px;
      cursor: pointer;
    }
    .code-actions button:hover { opacity: 0.85; }
    .code-actions button:active { opacity: 0.7; }

    #input-area {
      border-top: 1px solid var(--border);
      padding: 10px;
      display: flex;
      gap: 8px;
    }
    #input {
      flex: 1;
      background: var(--input-bg);
      color: var(--input-fg);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 8px 12px;
      font-family: inherit;
      font-size: inherit;
      resize: none;
      min-height: 36px;
      max-height: 150px;
      outline: none;
    }
    #input:focus { border-color: var(--btn-bg); }

    #send-btn {
      background: var(--btn-bg);
      color: var(--btn-fg);
      border: none;
      border-radius: 6px;
      padding: 8px 16px;
      font-size: 13px;
      cursor: pointer;
      white-space: nowrap;
    }
    #send-btn:hover { opacity: 0.9; }
    #send-btn:disabled { opacity: 0.4; cursor: default; }

    .status {
      text-align: center;
      color: var(--vscode-descriptionForeground, #888);
      font-size: 11px;
      padding: 4px;
    }

    .typing-indicator {
      display: inline-block;
      width: 8px; height: 8px;
      border-radius: 50%;
      background: var(--btn-bg);
      animation: pulse 0.8s infinite;
      margin-right: 4px;
    }
    @keyframes pulse {
      0%, 100% { opacity: 0.3; }
      50% { opacity: 1; }
    }

    .empty-state {
      text-align: center;
      padding: 40px 20px;
      color: var(--vscode-descriptionForeground, #888);
    }
    .empty-state h2 { margin-bottom: 8px; font-weight: 500; }
    .empty-state .shortcuts { font-size: 12px; margin-top: 16px; }
    .empty-state kbd {
      background: var(--input-bg);
      border: 1px solid var(--border);
      border-radius: 3px;
      padding: 1px 5px;
      font-size: 11px;
    }
  </style>
</head>
<body>
  <div id="messages">
    <div class="empty-state">
      <h2>Hermes Copilot</h2>
      <p>Ask about your code, request refactoring, generate tests, or debug issues.</p>
      <div class="shortcuts">
        <p>Try: <kbd>/explain</kbd> <kbd>/fix</kbd> <kbd>/tests</kbd> <kbd>/refactor</kbd> <kbd>/docs</kbd> <kbd>/optimize</kbd></p>
        <p style="margin-top:8px">Selected code is automatically included as context</p>
      </div>
    </div>
  </div>
  <div id="input-area">
    <textarea id="input" rows="1" placeholder="Ask about your code... (Shift+Enter for newline)"></textarea>
    <button id="send-btn">Send</button>
  </div>
  <script>
    const vscode = acquireVsCodeApi();
    const messagesEl = document.getElementById('messages');
    const inputEl = document.getElementById('input');
    const sendBtn = document.getElementById('send-btn');
    let isStreaming = false;

    // ── Send message ──
    function sendMessage() {
      const text = inputEl.value.trim();
      if (!text || isStreaming) return;
      vscode.postMessage({ type: 'sendMessage', text });
      inputEl.value = '';
      inputEl.style.height = 'auto';
      isStreaming = true;
      sendBtn.disabled = true;
      sendBtn.textContent = '...';
    }

    sendBtn.addEventListener('click', sendMessage);

    inputEl.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    });

    // Auto-resize textarea
    inputEl.addEventListener('input', () => {
      inputEl.style.height = 'auto';
      inputEl.style.height = Math.min(inputEl.scrollHeight, 150) + 'px';
    });

    // ── Receive messages ──
    window.addEventListener('message', (event) => {
      const msg = event.data;
      switch (msg.type) {
        case 'addMessage':
          addMessage(msg.message);
          break;
        case 'updateMessage':
          updateMessage(msg.messageId, msg.content);
          break;
        case 'clearChat':
          clearAll();
          break;
        case 'restoreHistory':
          restoreHistory(msg.messages);
          break;
      }
    });

    function addMessage(message) {
      // Remove empty state
      const empty = messagesEl.querySelector('.empty-state');
      if (empty) empty.remove();

      const div = document.createElement('div');
      div.className = 'message ' + message.role;
      div.id = message.id;
      div.innerHTML = formatContent(message.content);
      messagesEl.appendChild(div);
      scrollToBottom();
    }

    function updateMessage(id, content) {
      const div = document.getElementById(id);
      if (!div) return;
      div.innerHTML = formatContent(content);
      scrollToBottom();

      // Check if streaming is done (content ends with punctuation and no trailing markers)
      if (!content.includes('*[Request cancelled]*') && !content.includes('*Error:')) {
        if (/[.!?)\\x60]\\s*$/.test(content) && content.length > 50) {
          isStreaming = false;
          sendBtn.disabled = false;
          sendBtn.textContent = 'Send';
        }
      } else {
        isStreaming = false;
        sendBtn.disabled = false;
        sendBtn.textContent = 'Send';
      }
    }

    function clearAll() {
      messagesEl.innerHTML = '';
      isStreaming = false;
      sendBtn.disabled = false;
      sendBtn.textContent = 'Send';
    }

    function restoreHistory(messages) {
      clearAll();
      for (const msg of messages) {
        addMessage(msg);
      }
      isStreaming = false;
      sendBtn.disabled = false;
      sendBtn.textContent = 'Send';
    }

    // ── Formatting ──
    function formatContent(text) {
      if (!text) return '<span class="typing-indicator"></span>';

      // Escape HTML
      let html = text
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');

      // Code blocks with actions
      html = html.replace(/\`\`\`(\\w*)\\n([\\s\\S]*?)\`\`\`/g, (match, lang, code) => {
        const escapedCode = code.trim();
        const encoded = encodeURIComponent(escapedCode);
        return '<pre><code class="' + (lang || '') + '">' + escapedCode +
          '</code></pre>' +
          '<div class="code-actions">' +
          '<button onclick="applyCode(\\'' + encoded.replace(/'/g, "\\\\'") + '\\')">Apply</button>' +
          '<button onclick="copyCode(\\'' + encoded.replace(/'/g, "\\\\'") + '\\')">Copy</button>' +
          '</div>';
      });

      // Inline code
      html = html.replace(/\`([^\`]+)\`/g, '<code>$1</code>');

      // Bold / italic
      html = html.replace(/\\*\\*(.+?)\\*\\*/g, '<strong>$1</strong>');
      html = html.replace(/\\*(.+?)\\*/g, '<em>$1</em>');

      // Line breaks
      html = html.replace(/\\n/g, '<br>');

      return html;
    }

    // ── Actions ──
    function applyCode(encoded) {
      const code = decodeURIComponent(encoded);
      vscode.postMessage({ type: 'applyCode', code });
    }

    function copyCode(encoded) {
      const code = decodeURIComponent(encoded);
      navigator.clipboard.writeText(code).then(() => {
        // Brief visual feedback would go here
      });
    }

    function scrollToBottom() {
      messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    // Notify extension that webview is ready
    vscode.postMessage({ type: 'ready' });
  </script>
</body>
</html>`;
  }
}

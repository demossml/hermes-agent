/**
 * Sidebar chat panel — webview with React-based chat UI.
 */
import * as vscode from 'vscode';
import { HermesClient } from './hermesClient';

export class ChatPanelProvider implements vscode.WebviewViewProvider {
  public static readonly viewType = 'hermes.chatView';
  private _view?: vscode.WebviewView;

  constructor(
    private readonly client: HermesClient,
    private readonly _extensionUri: vscode.Uri,
    private projectName: string = ''
  ) {}

  resolveWebviewView(
    webviewView: vscode.WebviewView,
    _context: vscode.WebviewViewResolveContext,
    _token: vscode.CancellationToken
  ): void {
    this._view = webviewView;
    webviewView.webview.options = {
      enableScripts: true,
      localResourceRoots: [this._extensionUri],
    };
    webviewView.webview.html = this._getHtml(webviewView.webview);
    this._setMessageHandler(webviewView);
  }

  updateProject(name: string): void {
    this.projectName = name;
    this._view?.webview.postMessage({ type: 'projectUpdate', name });
  }

  private _setMessageHandler(webviewView: vscode.WebviewView): void {
    webviewView.webview.onDidReceiveMessage(async (msg) => {
      switch (msg.type) {
        case 'chat': {
          const editor = vscode.window.activeTextEditor;
          const response = await this.client.chat({
            message: msg.text,
            context: {
              workspaceFolder: vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? '',
              openFiles: vscode.window.visibleTextEditors.map((e) => e.document.uri.fsPath),
              gitStatus: '',
              selectedCode: editor?.document.getText(editor.selection),
              currentFile: editor?.document.uri.fsPath,
            },
          });
          webviewView.webview.postMessage({
            type: 'response',
            text: response.text,
          });
          break;
        }
        case 'command': {
          await vscode.commands.executeCommand(msg.command);
          break;
        }
      }
    });
  }

  private _getHtml(webview: vscode.Webview): string {
    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Hermes Chat</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: var(--vscode-font-family, -apple-system, sans-serif);
      font-size: var(--vscode-font-size, 13px);
      color: var(--vscode-foreground);
      background: var(--vscode-sideBar-background);
      padding: 0;
      height: 100vh;
      display: flex;
      flex-direction: column;
    }
    .header {
      padding: 8px 12px;
      border-bottom: 1px solid var(--vscode-sideBar-border);
      font-weight: 600;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: var(--vscode-sideBarTitle-foreground);
    }
    .messages {
      flex: 1;
      overflow-y: auto;
      padding: 8px;
    }
    .msg {
      margin-bottom: 8px;
      padding: 6px 10px;
      border-radius: 6px;
      max-width: 90%;
      word-wrap: break-word;
    }
    .msg.user {
      background: var(--vscode-button-background);
      color: var(--vscode-button-foreground);
      margin-left: auto;
    }
    .msg.agent {
      background: var(--vscode-input-background);
      border: 1px solid var(--vscode-input-border);
    }
    .msg.error {
      background: var(--vscode-inputValidation-errorBackground);
      border: 1px solid var(--vscode-inputValidation-errorBorder);
    }
    .input-area {
      padding: 8px;
      border-top: 1px solid var(--vscode-sideBar-border);
      display: flex;
      gap: 4px;
    }
    .input-area textarea {
      flex: 1;
      background: var(--vscode-input-background);
      color: var(--vscode-input-foreground);
      border: 1px solid var(--vscode-input-border);
      border-radius: 4px;
      padding: 6px 8px;
      font-family: inherit;
      font-size: inherit;
      resize: vertical;
      min-height: 32px;
      max-height: 120px;
    }
    .input-area button {
      background: var(--vscode-button-background);
      color: var(--vscode-button-foreground);
      border: none;
      border-radius: 4px;
      padding: 6px 12px;
      cursor: pointer;
      font-size: 13px;
    }
    .quick-actions {
      padding: 4px 8px;
      display: flex;
      flex-wrap: wrap;
      gap: 4px;
      border-top: 1px solid var(--vscode-sideBar-border);
    }
    .quick-actions button {
      background: var(--vscode-button-secondaryBackground);
      color: var(--vscode-button-secondaryForeground);
      border: none;
      border-radius: 3px;
      padding: 3px 8px;
      cursor: pointer;
      font-size: 11px;
    }
    .loading { opacity: 0.6; font-style: italic; padding: 8px; }
  </style>
</head>
<body>
  <div class="header" id="projectHeader">Hermes Agent</div>
  <div class="messages" id="messages"></div>
  <div class="quick-actions" id="quickActions">
    <button onclick="sendCommand('hermes.showStatus')">Status</button>
    <button onclick="sendCommand('hermes.showInsights')">Insights</button>
    <button onclick="sendCommand('hermes.runTesterOnFile')">Test</button>
    <button onclick="sendCommand('hermes.improveCode')">Improve</button>
  </div>
  <div class="input-area">
    <textarea id="userInput" placeholder="Ask Hermes..." rows="1"
              onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();sendMessage()}"></textarea>
    <button onclick="sendMessage()">Send</button>
  </div>
  <script>
    const vscode = acquireVsCodeApi();
    const msgs = document.getElementById('messages');
    const input = document.getElementById('userInput');
    const header = document.getElementById('projectHeader');

    function addMsg(text, role) {
      const div = document.createElement('div');
      div.className = 'msg ' + role;
      div.textContent = text;
      msgs.appendChild(div);
      msgs.scrollTop = msgs.scrollHeight;
    }

    function sendMessage() {
      const text = input.value.trim();
      if (!text) return;
      addMsg(text, 'user');
      input.value = '';
      vscode.postMessage({ type: 'chat', text });
      addMsg('...', 'loading');
    }

    function sendCommand(cmd) {
      vscode.postMessage({ type: 'command', command: cmd });
    }

    window.addEventListener('message', (e) => {
      const msg = e.data;
      if (msg.type === 'response') {
        document.querySelectorAll('.loading').forEach(el => el.remove());
        addMsg(msg.text, 'agent');
      } else if (msg.type === 'projectUpdate') {
        header.textContent = 'Project: ' + msg.name;
      }
    });
  </script>
</body>
</html>`;
  }
}

/**
 * Sidebar chat panel — serves the React webview.
 */
import * as vscode from 'vscode';
import * as path from 'path';
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
    _ctx: vscode.WebviewViewResolveContext,
    _token: vscode.CancellationToken
  ): void {
    this._view = webviewView;
    const webview = webviewView.webview;

    webview.options = {
      enableScripts: true,
      localResourceRoots: [
        vscode.Uri.joinPath(this._extensionUri, 'dist', 'webview'),
      ],
    };

    webview.html = this._getHtml(webview);
    this._setHandler(webviewView);

    // Send initial project info
    if (this.projectName) {
      webview.postMessage({ type: 'projectUpdate', name: this.projectName });
    }
  }

  updateProject(name: string): void {
    this.projectName = name;
    this._view?.webview.postMessage({ type: 'projectUpdate', name });
  }

  private _setHandler(wv: vscode.WebviewView): void {
    wv.webview.onDidReceiveMessage(async (msg) => {
      switch (msg.type) {
        case 'chat': {
          try {
            const editor = vscode.window.activeTextEditor;
            const context = {
              workspaceFolder: vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? '',
              openFiles: vscode.window.visibleTextEditors.map((e) => e.document.uri.fsPath),
              selectedCode: editor?.document.getText(editor.selection),
              currentFile: editor?.document.uri.fsPath,
            };
            const reply = await this.client.chat(msg.text, context);
            wv.webview.postMessage({ type: 'response', text: reply });
          } catch (err: any) {
            wv.webview.postMessage({
              type: 'response',
              text: `Error: ${err.message}`,
            });
          }
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
    const scriptUri = webview.asWebviewUri(
      vscode.Uri.joinPath(this._extensionUri, 'dist', 'webview', 'app.js')
    );
    const cssUri = webview.asWebviewUri(
      vscode.Uri.joinPath(this._extensionUri, 'dist', 'webview', 'index.css')
    );

    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <link rel="stylesheet" href="${cssUri}" />
  <title>Hermes Agent</title>
</head>
<body>
  <div id="root"></div>
  <script src="${scriptUri}"></script>
</body>
</html>`;
  }
}

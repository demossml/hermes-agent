/**
 * Hermes Agent - VS Code Extension v1.1
 *
 * New in v1.1:
 *   - React + Vite webview with dark theme, markdown, code highlighting
 *   - Copilot-style inline edits (InlineCompletionItemProvider)
 *   - Project card with Quality Score and isolation status
 *   - Quick action buttons (Tester, Browser, Insights, Improve)
 *   - Clickable status bar with rich tooltip
 */

import * as vscode from 'vscode';
import { HermesClient } from './hermesClient';
import { ChatPanelProvider } from './chatPanel';
import { HermesInlineProvider } from './inlineCompletionProvider';
import { registerContextActions } from './contextActions';
import { registerCommands } from './commands';
import { createStatusBar, updateStatusBar } from './statusBar';
import { detectProject } from './projectDetector';

let client: HermesClient;
let inlineProvider: HermesInlineProvider;

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  const useTailscale = vscode.workspace.getConfiguration('hermes').get<boolean>('server.tailscale', false);
  if (useTailscale) {
    let host = vscode.workspace.getConfiguration('hermes').get<string>('server.host', '');
    const port = vscode.workspace.getConfiguration('hermes').get<number>('server.port', 8787);
    if (!host) {
      const discovered = await discoverTailscaleServer();
      if (discovered) host = discovered.host;
    }
    if (host) {
      client = new (await import('./tailscaleClient')).TailscaleClient(host, port) as any;
      // Sync manager
  let syncManager: any = null;
  context.subscriptions.push(
    vscode.commands.registerCommand('hermes.syncStart', async () => {
      const { SyncManager } = await import('./syncManager');
      const ws = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
      if (!ws) { vscode.window.showErrorMessage('No workspace open'); return; }
      syncManager = new SyncManager(ws, 'hermes-live');
      await syncManager.start();
    }),
    vscode.commands.registerCommand('hermes.syncStop', () => {
      syncManager?.stop(); syncManager = null;
    }),
    vscode.commands.registerCommand('hermes.syncStatus', () => {
      if (syncManager) {
        const s = syncManager.getStatus();
        vscode.window.showInformationMessage(
          `Sync: ${s.branch} | Pushed: ${s.pushedCommits} | Pulled: ${s.pulledUpdates}`
        );
      } else {
        vscode.window.showInformationMessage('Sync not running. Use Hermes: Start Sync.');
      }
    })
  );

  vscode.window.showInformationMessage(`Hermes: Tailscale ${host}:${port}`);
    }
  }
  if (!client) {
    client = new HermesClient();
    try { await (client as HermesClient).start(); } catch (err) {
      vscode.window.showErrorMessage(`Hermes: ${err}`);
    }
  }

  let projectName = '';
  if (vscode.workspace.getConfiguration('hermes').get<boolean>('autoDetectProject', true)) {
    const detected = detectProject(vscode.workspace.workspaceFolders?.[0]);
    if (detected) { projectName = detected; client.switchProject(detected).catch(() => {}); }
  }

  const chatProvider = new ChatPanelProvider(client, context.extensionUri, projectName);
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider(ChatPanelProvider.viewType, chatProvider)
  );

  if (vscode.workspace.getConfiguration('hermes').get<boolean>('showStatusBar', true)) {
    createStatusBar(); updateStatusBar(projectName);
  }

  inlineProvider = new HermesInlineProvider(client);
  context.subscriptions.push(
    vscode.languages.registerInlineCompletionItemProvider({ pattern: '**' }, inlineProvider)
  );

  const ic = inlineProvider;
  context.subscriptions.push(
    vscode.commands.registerCommand('hermes.inlineImprove', () => ic.handleInlineCommand('improve')),
    vscode.commands.registerCommand('hermes.inlineFix', () => ic.handleInlineCommand('fix')),
    vscode.commands.registerCommand('hermes.inlineAddTests', () => ic.handleInlineCommand('tests')),
    vscode.commands.registerCommand('hermes.inlineAddComments', () => ic.handleInlineCommand('comments')),
    vscode.commands.registerCommand('hermes.inlineMakeFaster', () => ic.handleInlineCommand('faster'))
  );

  registerCommands(client, context);
  registerContextActions(client, context);

  client.onProjectSwitch((name) => { chatProvider.updateProject(name); updateStatusBar(name); });

  vscode.workspace.onDidChangeWorkspaceFolders(() => {
    const d = detectProject(vscode.workspace.workspaceFolders?.[0]);
    if (d && d !== projectName) { projectName = d; client.switchProject(d).catch(() => {}); chatProvider.updateProject(d); updateStatusBar(d); }
  });

  vscode.window.showInformationMessage(`Hermes Agent v1.1${projectName ? ' - ' + projectName : ''}`);
}

export function deactivate(): void { client?.dispose(); }

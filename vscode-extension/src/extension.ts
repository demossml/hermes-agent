import { openSettings, quickSetup, testConnection } from './settingsProvider';
import * as vscode from 'vscode';
import { HermesClient } from './hermesClient';
import { ChatPanelProvider } from './chatPanel';
import { HermesInlineProvider } from './inlineCompletionProvider';
import { registerContextActions } from './contextActions';
import { registerCommands } from './commands';
import { createStatusBar, updateStatusBar } from './statusBar';
import { detectProject } from './projectDetector';

let client: any;
let inlineProvider: HermesInlineProvider;

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  // First launch — offer quick setup
  const firstHost = vscode.workspace.getConfiguration('hermes').get<string>('server.host', '');
  if (!firstHost) {
    const setup = await vscode.window.showInformationMessage(
      'Hermes Agent needs configuration', 'Quick Setup', 'Open Settings', 'Later'
    );
    if (setup === 'Quick Setup') { await quickSetup(); return; }
    if (setup === 'Open Settings') { openSettings(); }
  }
  // Try Tailscale first, fall back to local
  const useTailscale = vscode.workspace.getConfiguration('hermes').get<boolean>('server.tailscale', false);
  if (useTailscale) {
    let host = vscode.workspace.getConfiguration('hermes').get<string>('server.host', '');
    const port = vscode.workspace.getConfiguration('hermes').get<number>('server.port', 8787);
    if (!host) {
      const { discoverTailscaleServer } = await import('./tailscaleClient');
      const discovered = await discoverTailscaleServer();
      if (discovered) host = discovered.host;
    }
    if (host) {
      const { TailscaleClient } = await import('./tailscaleClient');
      client = new TailscaleClient(host, port);
      vscode.window.showInformationMessage(`Hermes: Tailscale ${host}:${port}`);
    }
  }
  if (!client) {
    client = new HermesClient();
    try { await client.start(); } catch (err) {
      vscode.window.showErrorMessage(`Hermes: ${err}`);
    }
  }

  let projectName = '';
  if (vscode.workspace.getConfiguration('hermes').get<boolean>('autoDetectProject', true)) {
    const d = detectProject(vscode.workspace.workspaceFolders?.[0]);
    if (d) { projectName = d; client.switchProject(d).catch(() => {}); }
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
        vscode.window.showInformationMessage(`Sync: ${s.branch} | Pushed: ${s.pushedCommits} | Pulled: ${s.pulledUpdates}`);
      } else {
        vscode.window.showInformationMessage('Sync not running. Use Hermes: Start Sync.');
      }
    })
  );

  registerCommands(client, context);
  context.subscriptions.push(
    vscode.commands.registerCommand('hermes.openSettings', openSettings),
    vscode.commands.registerCommand('hermes.quickSetup', quickSetup),
    vscode.commands.registerCommand('hermes.testConnection', testConnection)
  );
  registerContextActions(client, context);

  client.onProjectSwitch?.((name: string) => { chatProvider.updateProject(name); updateStatusBar(name); });

  vscode.workspace.onDidChangeWorkspaceFolders(() => {
    const d = detectProject(vscode.workspace.workspaceFolders?.[0]);
    if (d && d !== projectName) { projectName = d; client.switchProject(d).catch(() => {}); chatProvider.updateProject(d); updateStatusBar(d); }
  });

  vscode.window.showInformationMessage(`Hermes Agent v1.1${projectName ? ' - ' + projectName : ''}`);
}

export function deactivate(): void { client?.dispose?.(); }

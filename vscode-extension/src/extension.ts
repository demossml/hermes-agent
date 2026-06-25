/**
 * Hermes Agent — VS Code Extension
 *
 * Entry point. Activates:
 *   - Hermes ACP client (spawns hermes acp)
 *   - Sidebar chat panel (webview)
 *   - Status bar with project info
 *   - Context menu actions
 *   - Command palette commands
 */

import * as vscode from 'vscode';
import { HermesClient } from './hermesClient';
import { ChatPanelProvider } from './chatPanel';
import { registerContextActions } from './contextActions';
import { registerCommands } from './commands';
import { createStatusBar, updateStatusBar } from './statusBar';
import { detectProject } from './projectDetector';

let client: HermesClient;

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  // ── 1. Start Hermes ACP client ──────────────────────────
  client = new HermesClient();
  try {
    await client.start();
  } catch (err) {
    vscode.window.showErrorMessage(
      `Hermes Agent: failed to start. Is Hermes installed? Run 'hermes --help' in terminal.\n${err}`
    );
  }

  // ── 2. Detect project ───────────────────────────────────
  const autoDetect = vscode.workspace.getConfiguration('hermes').get<boolean>('autoDetectProject', true);
  let projectName = '';
  if (autoDetect) {
    const detected = detectProject(vscode.workspace.workspaceFolders?.[0]);
    if (detected) {
      projectName = detected;
      client.switchProject(detected).catch(() => {});
    }
  }

  // ── 3. Sidebar chat panel ───────────────────────────────
  const chatProvider = new ChatPanelProvider(client, context.extensionUri, projectName);
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider(ChatPanelProvider.viewType, chatProvider)
  );

  // ── 4. Status bar ───────────────────────────────────────
  const showSb = vscode.workspace.getConfiguration('hermes').get<boolean>('showStatusBar', true);
  if (showSb) {
    createStatusBar();
    updateStatusBar(projectName);
  }

  // ── 5. Register commands & context actions ──────────────
  registerCommands(client, context);
  registerContextActions(client, context);

  // ── 6. React to project switches ────────────────────────
  client.onProjectSwitch((name) => {
    chatProvider.updateProject(name);
    updateStatusBar(name);
  });

  // ── 7. Watch workspace folder changes ───────────────────
  vscode.workspace.onDidChangeWorkspaceFolders(() => {
    const detected = detectProject(vscode.workspace.workspaceFolders?.[0]);
    if (detected && detected !== projectName) {
      projectName = detected;
      client.switchProject(detected).catch(() => {});
      chatProvider.updateProject(detected);
      updateStatusBar(detected);
    }
  });

  vscode.window.showInformationMessage(
    `Hermes Agent activated${projectName ? ` — Project: ${projectName}` : ''}`
  );
}

export function deactivate(): void {
  client?.dispose();
}

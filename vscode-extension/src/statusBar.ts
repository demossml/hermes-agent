/**
 * Interactive status bar — [Project: Name] | [3 wf] | [12 insights]
 * Click to open Hermes sidebar or show status detail.
 */
import * as vscode from 'vscode';

let statusBarItem: vscode.StatusBarItem;

export function createStatusBar(): vscode.StatusBarItem {
  statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
  statusBarItem.name = 'Hermes Agent';
  statusBarItem.command = 'hermes.openChat';
  statusBarItem.tooltip = 'Hermes Agent — click to open chat';
  statusBarItem.text = '$(hubot) Hermes';
  statusBarItem.backgroundColor = undefined;
  statusBarItem.show();
  return statusBarItem;
}

export function updateStatusBar(
  projectName?: string,
  activeWorkflows: number = 0,
  insightsCount: number = 0
): void {
  if (!statusBarItem) return;

  const parts: string[] = [];

  if (projectName) {
    parts.push(`$(folder) ${projectName}`);
  }

  if (activeWorkflows > 0) {
    parts.push(`$(sync~spin) ${activeWorkflows}`);
  }

  if (insightsCount > 0) {
    parts.push(`$(lightbulb) ${insightsCount}`);
  }

  statusBarItem.text = parts.length > 0
    ? `$(hubot) ${parts.join('  ')}`
    : '$(hubot) Hermes';

  statusBarItem.tooltip = [
    projectName ? `Project: ${projectName}` : 'No active project',
    `Active workflows: ${activeWorkflows}`,
    `Shared insights: ${insightsCount}`,
    '',
    'Click to open Hermes chat',
  ].join('\n');

  statusBarItem.command = 'hermes.openChat';
}

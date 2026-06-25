/**
 * Status bar integration — shows [Project: Name] and active agent count.
 */
import * as vscode from 'vscode';

let statusBarItem: vscode.StatusBarItem;

export function createStatusBar(): vscode.StatusBarItem {
  statusBarItem = vscode.window.createStatusBarItem(
    vscode.StatusBarAlignment.Left,
    100
  );
  statusBarItem.command = 'hermes.showStatus';
  statusBarItem.tooltip = 'Hermes Agent — click for status';
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
  if (projectName) parts.push(`Project: ${projectName}`);
  if (activeWorkflows > 0) parts.push(`${activeWorkflows} wf`);
  if (insightsCount > 0) parts.push(`${insightsCount} insights`);
  statusBarItem.text = parts.length > 0 ? parts.join(' | ') : 'Hermes';
}

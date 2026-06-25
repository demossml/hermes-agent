/**
 * Command Palette commands — accessible via Cmd+Shift+P.
 */
import * as vscode from 'vscode';
import { HermesClient } from './hermesClient';

export function registerCommands(
  client: HermesClient,
  context: vscode.ExtensionContext
): void {
  const subs = context.subscriptions;

  subs.push(
    vscode.commands.registerCommand('hermes.openChat', () => {
      vscode.commands.executeCommand('hermes.chatView.focus');
    }),

    vscode.commands.registerCommand('hermes.switchProject', async () => {
      const name = await vscode.window.showInputBox({
        prompt: 'Enter project name to switch to',
        placeHolder: 'my-app',
      });
      if (name) await client.switchProject(name);
    }),

    vscode.commands.registerCommand('hermes.showInsights', async () => {
      const insights = await client.getInsights(20);
      if (insights.length === 0) {
        vscode.window.showInformationMessage('No insights yet. They are auto-generated from completed tasks.');
        return;
      }
      const items = insights.map(
        (i: any) => `[${i.importance ?? 5}/10] ${i.text ?? i}`
      );
      const picked = await vscode.window.showQuickPick(items, {
        placeHolder: `Project Insights (${insights.length} total)`,
        matchOnDescription: true,
      });
    }),

    vscode.commands.registerCommand('hermes.runFullTest', async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) {
        vscode.window.showWarningMessage('Open a file to run tests');
        return;
      }
      const result = await client.runTester(editor.document.uri.fsPath);
      showOutput('Hermes Tester', JSON.stringify(result, null, 2));
    }),

    vscode.commands.registerCommand('hermes.openDashboard', () => {
      vscode.env.openExternal(vscode.Uri.parse('http://localhost:8787'));
    }),

    vscode.commands.registerCommand('hermes.guardStatus', async () => {
      const status = await client.getStatus();
      showOutput('Hermes Guard Status', JSON.stringify(status, null, 2));
    }),

    vscode.commands.registerCommand('hermes.showStatus', async () => {
      const status = await client.getStatus();
      vscode.window.showInformationMessage(
        `Hermes: ${status?.project ?? 'no project'} | ` +
        `Workflows: ${status?.activeWorkflows ?? 0} | ` +
        `Insights: ${status?.insightsCount ?? 0}`
      );
    }),

    vscode.commands.registerCommand('hermes.currentProject', async () => {
      const status = await client.getStatus();
      vscode.window.showInformationMessage(
        `Active project: ${status?.project ?? '(none)'}`
      );
    })
  );
}

function showOutput(channel: string, content: string): void {
  const out = vscode.window.createOutputChannel(channel);
  out.clear();
  out.append(content);
  out.show();
}

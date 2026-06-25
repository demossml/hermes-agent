/**
 * Right-click context menu actions for selected code.
 */
import * as vscode from 'vscode';
import { HermesClient } from './hermesClient';

export function registerContextActions(
  client: HermesClient,
  context: vscode.ExtensionContext
): void {
  context.subscriptions.push(
    vscode.commands.registerCommand('hermes.askAboutCode', async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) return;
      const selection = editor.document.getText(editor.selection);
      const question = await vscode.window.showInputBox({
        prompt: 'Ask Hermes about this code',
        placeHolder: 'What does this code do?',
      });
      if (!question) return;
      const answer = await client.askAboutCode(selection, question);
      showResult(answer, 'Hermes Answer');
    }),

    vscode.commands.registerCommand('hermes.improveCode', async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) return;
      const selection = editor.selection.isEmpty
        ? editor.document.getText()
        : editor.document.getText(editor.selection);
      const improved = await client.improveCode(selection, editor.document.uri.fsPath);
      if (!improved) return;
      await replaceOrShow(improved, editor, 'Hermes Improved Code');
    }),

    vscode.commands.registerCommand('hermes.generateTests', async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) return;
      const code = editor.document.getText();
      const tests = await client.generateTests(code, editor.document.uri.fsPath);
      if (!tests) return;
      const testPath = editor.document.uri.fsPath.replace(/\.(\w+)$/, '_test.$1');
      const testUri = vscode.Uri.file(testPath);
      const doc = await vscode.workspace.openTextDocument({ content: tests, language: 'python' });
      await vscode.window.showTextDocument(doc, { preview: false });
    }),

    vscode.commands.registerCommand('hermes.runTesterOnFile', async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) return;
      const result = await client.runTester(editor.document.uri.fsPath);
      showResult(JSON.stringify(result, null, 2), 'Tester Results');
    }),

    vscode.commands.registerCommand('hermes.analyzeWithBrowser', async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor?.selection) return;
      const url = editor.document.getText(editor.selection).trim();
      if (!url.startsWith('http')) {
        vscode.window.showWarningMessage('Select a URL to analyze with browser tool');
        return;
      }
      const answer = await client.askAboutCode(url, 'Open this URL in the browser and do a full test');
      showResult(answer, 'Browser Analysis');
    })
  );
}

async function showResult(content: string, title: string): Promise<void> {
  const doc = await vscode.workspace.openTextDocument({
    content,
    language: 'markdown',
  });
  await vscode.window.showTextDocument(doc, { preview: true, viewColumn: vscode.ViewColumn.Beside });
}

async function replaceOrShow(improved: string, editor: vscode.TextEditor, title: string): Promise<void> {
  const choice = await vscode.window.showQuickPick(['Apply changes', 'Show diff'], {
    placeHolder: title,
  });
  if (choice === 'Apply changes') {
    const fullRange = editor.selection.isEmpty
      ? new vscode.Range(0, 0, editor.document.lineCount, 0)
      : editor.selection;
    await editor.edit((editBuilder) => editBuilder.replace(fullRange, improved));
  } else {
    await showResult(improved, title);
  }
}

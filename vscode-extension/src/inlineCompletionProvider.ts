/**
 * InlineCompletionItemProvider — Copilot-style inline edits.
 *
 * When the user pauses typing or presses Cmd+I, Hermes suggests
 * improvements / completions / fixes directly in the editor.
 */

import * as vscode from 'vscode';
import { HermesClient } from './hermesClient';

export class HermesInlineProvider implements vscode.InlineCompletionItemProvider {
  constructor(private client: any) {}

  async provideInlineCompletionItems(
    document: vscode.TextDocument,
    position: vscode.Position,
    context: vscode.InlineCompletionContext,
    token: vscode.CancellationToken
  ): Promise<vscode.InlineCompletionItem[]> {
    // Skip if disabled
    if (!vscode.workspace.getConfiguration('hermes').get<boolean>('inlineEnabled', true)) {
      return [];
    }

    // Only trigger on explicit request or pause
    if (context.triggerKind !== 1 as any) {
      return [];
    }

    const range = document.getWordRangeAtPosition(position) ??
      new vscode.Range(position, position);

    const items: vscode.InlineCompletionItem[] = [];

    // Get surrounding context (up to 50 lines)
    const startLine = Math.max(0, position.line - 25);
    const endLine = Math.min(document.lineCount - 1, position.line + 25);
    const surroundingCode = document.getText(
      new vscode.Range(startLine, 0, endLine, document.lineAt(endLine).text.length)
    );

    try {
      const suggestion = await this.client.inlineComplete(
        surroundingCode,
        document.uri.fsPath,
        position.line,
        position.character
      );

      if (suggestion && !token.isCancellationRequested) {
        items.push(
          new vscode.InlineCompletionItem(
            suggestion,
            new vscode.Range(position, position)
          )
        );
      }
    } catch {
      // Silently fail — inline completions are best-effort
    }

    return items;
  }

  /**
   * Handle explicit inline commands: improve, fix, add tests, add comments, make faster.
   */
  async handleInlineCommand(
    command: 'improve' | 'fix' | 'tests' | 'comments' | 'faster'
  ): Promise<void> {
    const editor = vscode.window.activeTextEditor;
    if (!editor) return;

    const selection = editor.selection;
    const code = editor.document.getText(
      selection.isEmpty
        ? new vscode.Range(0, 0, editor.document.lineCount, 0)
        : selection
    );

    const filePath = editor.document.uri.fsPath;
    let result = '';

    try {
      switch (command) {
        case 'improve':
          result = await this.client.improveCode(code, filePath);
          break;
        case 'fix':
          result = await this.client.askAboutCode(
            code,
            'Find and fix all bugs, errors, and edge cases in this code. Return the corrected code only.'
          );
          break;
        case 'tests':
          result = await this.client.generateTests(code, filePath);
          break;
        case 'comments':
          result = await this.client.askAboutCode(
            code,
            'Add comprehensive docstrings and inline comments to this code. Return the commented code only.'
          );
          break;
        case 'faster':
          result = await this.client.askAboutCode(
            code,
            'Optimize this code for maximum performance. Use faster algorithms, reduce allocations, add caching where appropriate. Return the optimized code only.'
          );
          break;
      }
    } catch (err: any) {
      vscode.window.showErrorMessage(`Hermes: ${err.message}`);
      return;
    }

    if (!result) return;

    // Apply as diff or replace
    const choice = await vscode.window.showQuickPick(
      ['Apply inline', 'Show in new tab', 'Cancel'],
      { placeHolder: `Hermes ${command} suggestion ready` }
    );

    if (choice === 'Apply inline') {
      const range = selection.isEmpty
        ? new vscode.Range(0, 0, editor.document.lineCount, 0)
        : selection;
      await editor.edit((edit) => edit.replace(range, result));
    } else if (choice === 'Show in new tab') {
      const doc = await vscode.workspace.openTextDocument({
        content: result,
        language: editor.document.languageId,
      });
      await vscode.window.showTextDocument(doc, { preview: true, viewColumn: vscode.ViewColumn.Beside });
    }
  }
}

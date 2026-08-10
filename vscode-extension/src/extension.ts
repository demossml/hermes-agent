/**
 * Hermes Copilot — VS Code Extension Entry Point
 *
 * Provides a Copilot-like experience using a local LLM with an
 * OpenAI-compatible API endpoint (Hermes, llama.cpp, Ollama, vLLM, etc.).
 *
 * Features:
 * - Inline completions (ghost text + Tab acceptance)
 * - Sidebar chat with streaming
 * - VS Code Chat API participant (native Copilot Chat UI)
 * - Context-aware (current file + open tabs + workspace)
 * - Command palette and context menu commands
 * - Status bar with connection health
 *
 * Requirements: VS Code 1.85+, local LLM server on http://localhost:XXXX/v1
 */

import * as vscode from 'vscode';
import { HttpClient } from './httpClient';
import { ContextProvider } from './contextProvider';
import { InlineProvider } from './inlineProvider';
import { ChatPanelProvider } from './chatPanel';
import { StatusBarManager } from './statusBar';

// ── Singleton references (for deactivate) ──────────

let statusBar: StatusBarManager;
let inlineProvider: InlineProvider;

// ── Activate ────────────────────────────────────────

export async function activate(
  context: vscode.ExtensionContext
): Promise<void> {
  // ── Initialize core services ──

  const client = new HttpClient();
  const ctx = new ContextProvider();
  statusBar = new StatusBarManager(client);

  // Show connecting state immediately
  statusBar.setConnecting();

  // ── Check connection ──

  const health = await client.healthCheck();
  if (health.ok) {
    console.log(
      `[Hermes] Connected: ${health.model || 'auto'} (${health.latencyMs}ms)`
    );
  } else {
    console.log('[Hermes] Not connected — will retry');
    vscode.window.showWarningMessage(
      'Hermes: API server not reachable.\n\n' +
      `Endpoint: ${vscode.workspace.getConfiguration('hermes').get('endpoint', 'http://localhost:8642')}\n\n` +
      'Open Settings to configure the endpoint, then run "Hermes: Check Connection".'
    );
  }

  // Start periodic health checks
  await statusBar.start();

  // ── Inline Completions ────────────────────────

  inlineProvider = new InlineProvider(client, ctx);
  context.subscriptions.push(
    vscode.languages.registerInlineCompletionItemProvider(
      { pattern: '**' },
      inlineProvider
    )
  );

  // ── Chat Sidebar ──────────────────────────────

  const chatProvider = new ChatPanelProvider(
    client,
    ctx,
    context.extensionUri
  );
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider(
      ChatPanelProvider.viewType,
      chatProvider
    )
  );

  // ── VS Code Chat Participant (native Copilot UI) ──

  registerChatParticipant(context, client, ctx);

  // ── Commands ───────────────────────────────────

  registerCommands(context, inlineProvider, statusBar, client);

  // ── Keyboard shortcuts ─────────────────────────

  // Alt+\ triggers inline completion explicitly
  // Ctrl+] cycles to next suggestion
  // (Registered via keybindings in package.json)

  // ── Done ──

  const modelStr = health.model ? ` (${health.model})` : '';
  vscode.window.showInformationMessage(
    `Hermes Copilot activated${modelStr} — ${health.ok ? `${health.latencyMs}ms` : 'check settings'}`
  );
}

// ── Deactivate ──────────────────────────────────────

export function deactivate(): void {
  statusBar?.stop();
}

// ── Chat Participant Registration ───────────────────

function registerChatParticipant(
  context: vscode.ExtensionContext,
  client: HttpClient,
  ctxProvider: ContextProvider
): void {
  // VS Code Chat API is available from 1.86+
  const chatApi = (vscode as any).chat;
  if (typeof chatApi?.createChatParticipant !== 'function') {
    console.log('[Hermes] VS Code Chat API not available (requires 1.86+)');
    return;
  }

  const participant = chatApi.createChatParticipant(
    'hermes-agent.hermes',
    async (
      request: any,
      _chatContext: any,
      responseStream: any,
      token: vscode.CancellationToken
    ) => {
      const command = request.command;
      let prompt = request.prompt;

      // ── Build context ──
      const editor = vscode.window.activeTextEditor;
      let fullCtx: Awaited<ReturnType<typeof ctxProvider.buildChatContext>>;

      try {
        fullCtx = await ctxProvider.buildChatContext(
          editor?.document,
          editor?.selection
        );
      } catch {
        fullCtx = { snippets: [], diagnostics: '' };
      }

      // ── Slash commands ──
      const commandPrompts: Record<string, string> = {
        explain: 'Explain what the following code does in detail. Be thorough but concise.',
        fix: 'Find and fix ALL bugs, errors, edge cases, and potential issues in the following code. Return the corrected code.',
        tests: 'Write comprehensive unit tests for the following code, covering edge cases and error paths.',
        refactor: 'Refactor the following code for maximum readability, maintainability, and adherence to best practices.',
        docs: 'Add thorough docstrings, JSDoc, and inline comments to the following code.',
        optimize: 'Optimize the following code for maximum performance. Use faster algorithms, reduce allocations, add caching.',
      };

      let userMessage = prompt;
      if (command && commandPrompts[command]) {
        userMessage = commandPrompts[command];
        if (prompt) {
          userMessage += `\n\nAdditional context: ${prompt}`;
        }
      }

      // ── Attach selected code ──
      if (editor?.selection && !editor.selection.isEmpty) {
        const selectedCode = editor.document.getText(editor.selection);
        const lang = editor.document.languageId;
        userMessage += `\n\n\`\`\`${lang}\n${selectedCode}\n\`\`\``;
      }

      // ── System prompt with context ──
      let systemPrompt = 'You are a helpful AI coding assistant running locally. ';
      systemPrompt += 'You help with code questions, debugging, refactoring, and explanations. ';
      systemPrompt += 'Keep responses clear and well-structured. Use code blocks with language identifiers when showing code.';

      if (fullCtx.snippets.length > 0) {
        systemPrompt += '\n\nCurrent workspace context:\n';
        for (const s of fullCtx.snippets.slice(0, 5)) {
          const relPath = vscode.workspace.asRelativePath(s.uri);
          systemPrompt += `\nFile: ${relPath} (${s.content.split('\n').length} lines)`;
        }
      }

      if (fullCtx.diagnostics) {
        systemPrompt += `\n\nCurrent file diagnostics:\n${fullCtx.diagnostics}`;
      }

      // ── Stream response ──
      try {
        const stream = client.chatStream(
          [
            { role: 'system', content: systemPrompt },
            { role: 'user', content: userMessage },
          ],
          token
        );

        for await (const chunk of stream) {
          if (token.isCancellationRequested) break;
          responseStream.markdown(chunk);
        }
      } catch (err: any) {
        if (err.name === 'AbortError') return;
        responseStream.markdown(`**Error:** ${err.message}`);
      }
    }
  );

  participant.iconPath = vscode.Uri.joinPath(
    context.extensionUri,
    'resources',
    'icon.png'
  );

  context.subscriptions.push(participant);
}

// ── Command Registration ────────────────────────────

function registerCommands(
  context: vscode.ExtensionContext,
  inline: InlineProvider,
  sb: StatusBarManager,
  client: HttpClient
): void {
  const subs = context.subscriptions;

  // Code commands — delegate to InlineProvider
  const codeCommands = [
    ['hermes.explainCode', 'explain' as const],
    ['hermes.refactorCode', 'refactor' as const],
    ['hermes.generateTests', 'tests' as const],
    ['hermes.generateDocs', 'docs' as const],
    ['hermes.findBugs', 'fix' as const],
    ['hermes.optimizeCode', 'optimize' as const],
  ];

  for (const [cmdId, action] of codeCommands) {
    subs.push(
      vscode.commands.registerCommand(cmdId, () =>
        inline.executeCodeCommand(action)
      )
    );
  }

  // Toggle inline completions
  subs.push(
    vscode.commands.registerCommand('hermes.toggleInline', async () => {
      const cfg = vscode.workspace.getConfiguration('hermes');
      const current = cfg.get<boolean>('inlineEnabled', true);
      await cfg.update('inlineEnabled', !current, true);
      vscode.window.showInformationMessage(
        `Hermes: inline completions ${!current ? 'ENABLED' : 'DISABLED'}`
      );
    })
  );

  // Next suggestion (cycling)
  subs.push(
    vscode.commands.registerCommand('hermes.nextSuggestion', () =>
      inline.showNextSuggestion()
    )
  );

  // Connection check
  subs.push(
    vscode.commands.registerCommand('hermes.checkConnection', () =>
      sb.checkNow()
    )
  );

  // Open settings
  subs.push(
    vscode.commands.registerCommand('hermes.openSettings', () => {
      vscode.commands.executeCommand(
        'workbench.action.openSettings',
        '@ext:hermes-copilot'
      );
    })
  );
}

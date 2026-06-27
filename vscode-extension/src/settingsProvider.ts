/**
 * Settings Provider — opens VS Code Settings UI for Hermes Agent.
 *
 * Uses the native VS Code Settings editor (settings.json) which is
 * the standard and most maintainable approach. Also offers a quick
 * configuration webview panel for first-time setup.
 */
import * as vscode from 'vscode';

const SETTINGS_PREFIX = 'hermes.server';

/**
 * Open the native VS Code Settings UI filtered to Hermes settings.
 */
export function openSettings(): void {
  vscode.commands.executeCommand(
    'workbench.action.openSettings',
    '@ext:hermes-agent hermes.server'
  );
}

/**
 * Quick setup: show InputBox sequence for first-time configuration.
 */
export async function quickSetup(): Promise<void> {
  const tailscale = await vscode.window.showQuickPick(['Yes (Tailscale)', 'No (local)'], {
    placeHolder: 'Connect via Tailscale?',
  });
  if (!tailscale) return;

  const config = vscode.workspace.getConfiguration('hermes');

  if (tailscale.startsWith('Yes')) {
    await config.update('server.tailscale', true, true);

    const host = await vscode.window.showInputBox({
      prompt: 'Hermes server Tailscale IP',
      placeHolder: '100.72.181.16',
      value: '100.72.181.16',
      validateInput: (v) => v.match(/^100\.\d+\.\d+\.\d+$/) ? null : 'Must be a Tailscale IP (100.x.x.x)',
    });
    if (host) {
      await config.update('server.host', host, true);
    } else return;

    const port = await vscode.window.showInputBox({
      prompt: 'Hermes server port',
      placeHolder: '8790',
      value: '8790',
      validateInput: (v) => isNaN(Number(v)) ? 'Must be a number' : null,
    });
    if (port) {
      await config.update('server.port', Number(port), true);
    }
  } else {
    await config.update('server.tailscale', false, true);
  }

  vscode.window.showInformationMessage('Hermes settings saved. Restart VS Code or reload window.');
}

/**
 * Test connection to the configured Hermes server.
 */
export async function testConnection(): Promise<void> {
  const config = vscode.workspace.getConfiguration('hermes');
  const host = config.get<string>('server.host', '');
  const port = config.get<number>('server.port', 8790);
  const url = `http://${host}:${port}/health`;

  vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: 'Testing Hermes connection...' },
    async () => {
      try {
        const resp = await fetch(url, { signal: (() => { const ac = new AbortController(); setTimeout(() => ac.abort(), 5000); return ac.signal; })() });
        const data: any = await resp.json();
        if (data.status === 'ok') {
          vscode.window.showInformationMessage(
            `Connected! Hermes on ${host}:${port} (${data.tailscale || 'OK'})`
          );
        } else {
          vscode.window.showErrorMessage(`Hermes returned: ${JSON.stringify(data)}`);
        }
      } catch (err: any) {
        vscode.window.showErrorMessage(
          `Cannot reach Hermes at ${host}:${port}. Is the server running?\n\nError: ${err.message}`
        );
      }
    }
  );
}

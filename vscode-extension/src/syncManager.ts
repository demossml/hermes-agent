/**
 * Live Sync Manager v2 — Tailscale-only bidirectional code sync.
 *
 * Auto-commits on save, auto-pulls on focus. Shows Tailscale IP in status bar.
 */
import * as vscode from 'vscode';
import * as cp from 'child_process';

export class SyncManager {
  private _watcher: vscode.FileSystemWatcher | null = null;
  private _pullTimer: NodeJS.Timeout | null = null;
  private _statusBar: vscode.StatusBarItem;
  private _running = false;
  private _pushedCommits = 0;
  private _pulledUpdates = 0;
  private _tsIp = '';

  constructor(private _workspaceDir: string, private _branch = 'hermes-live') {
    this._statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 99);
    this._statusBar.command = 'hermes.syncStatus';
  }

  private _exec(cmd: string): Promise<string> {
    return new Promise((resolve) => {
      cp.exec(cmd, { cwd: this._workspaceDir, timeout: 30000 }, (_, stdout) => resolve(stdout?.trim() ?? ''));
    });
  }

  private async _git(args: string[]): Promise<string> {
    return this._exec(`git ${args.join(' ')}`);
  }

  async start(): Promise<void> {
    if (this._running) return;

    // Detect Tailscale IP
    try {
      this._tsIp = await this._exec('tailscale ip -4');
    } catch { this._tsIp = ''; }

    if (!this._tsIp || !this._tsIp.startsWith('100.')) {
      const choice = await vscode.window.showErrorMessage(
        'Hermes Live Sync requires Tailscale. Tailscale not detected.',
        'Install Tailscale', 'Start anyway (unsafe)'
      );
      if (choice === 'Install Tailscale') {
        vscode.env.openExternal(vscode.Uri.parse('https://tailscale.com/download'));
      }
      if (choice !== 'Start anyway (unsafe)') return;
    }

    this._running = true;
    await this._git(['checkout', '-b', this._branch]);
    await this._pull();
    if (await this._hasChanges()) await this._commitPush();

    this._watcher = vscode.workspace.createFileSystemWatcher('**/*');
    let debounce: NodeJS.Timeout | null = null;
    const onFsChange = () => {
      if (debounce) clearTimeout(debounce);
      debounce = setTimeout(async () => {
        if (await this._hasChanges()) {
          await this._commitPush();
          this._updateBar('$(cloud-upload)');
        }
      }, 2000);
    };
    this._watcher.onDidChange(onFsChange);
    this._watcher.onDidCreate(onFsChange);

    this._pullTimer = setInterval(async () => {
      if (await this._pull()) {
        this._pulledUpdates++;
        this._updateBar('$(cloud-download)');
        try { await vscode.commands.executeCommand('workbench.action.files.revert'); } catch {}
      }
    }, 5000);

    vscode.window.onDidChangeWindowState(async (e) => {
      if (e.focused) {
        await this._pull();
        try { await vscode.commands.executeCommand('workbench.action.files.revert'); } catch {}
      }
    });

    this._statusBar.show();
    this._updateBar('$(sync~spin)');
    vscode.window.showInformationMessage(
      `Hermes Live Sync: ${this._branch} via Tailscale (${this._tsIp || 'local'})`
    );
  }

  stop(): void {
    this._running = false;
    this._watcher?.dispose(); this._watcher = null;
    if (this._pullTimer) { clearInterval(this._pullTimer); this._pullTimer = null; }
    this._statusBar.hide();
    vscode.window.showInformationMessage('Hermes Live Sync stopped');
  }

  getStatus() {
    return {
      running: this._running, branch: this._branch,
      tailscaleIp: this._tsIp,
      pushedCommits: this._pushedCommits, pulledUpdates: this._pulledUpdates,
    };
  }

  private _updateBar(icon: string): void {
    const ip = this._tsIp ? ` (${this._tsIp})` : '';
    this._statusBar.text = `$(git-branch) Sync: ${this._branch}${ip} ${icon}`;
    this._statusBar.tooltip = `Hermes Live Sync [${this._branch}]\nTailscale: ${this._tsIp || 'N/A'}\nPushed: ${this._pushedCommits}\nPulled: ${this._pulledUpdates}`;
  }

  private async _hasChanges(): Promise<boolean> {
    const out = await this._git(['status', '--porcelain']);
    return out.length > 0;
  }

  private async _commitPush(): Promise<void> {
    const ts = new Date().toISOString().replace('T', ' ').substring(0, 19);
    const host = require('os').hostname();
    await this._git(['add', '-A']);
    await this._git(['commit', '-m', `sync: ${host} at ${ts}`]);
    await this._git(['push', 'origin', this._branch]);
    this._pushedCommits++;
  }

  private async _pull(): Promise<boolean> {
    const out = await this._git(['pull', '--rebase', 'origin', this._branch]);
    return !out.includes('Already up to date');
  }

  dispose(): void { this.stop(); this._statusBar.dispose(); }
}

/**
 * Live Sync Manager — bidirectional code sync via git.
 *
 * Auto-commits on save, auto-pulls on focus, status bar indicator.
 */
import * as vscode from 'vscode';
import * as cp from 'child_process';
import * as path from 'path';

export class SyncManager {
  private _watcher: vscode.FileSystemWatcher | null = null;
  private _pullTimer: NodeJS.Timeout | null = null;
  private _statusBar: vscode.StatusBarItem;
  private _running = false;
  private _pushedCommits = 0;
  private _pulledUpdates = 0;

  constructor(private _workspaceDir: string, private _branch: string = 'hermes-live') {
    this._statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 99);
    this._statusBar.command = 'hermes.syncStatus';
    this._statusBar.tooltip = 'Hermes Live Sync';
  }

  async start(): Promise<void> {
    if (this._running) return;
    this._running = true;

    // Ensure branch exists
    await this._git(['checkout', '-b', this._branch], true);

    // Initial sync
    await this._pull();
    if (await this._hasChanges()) {
      await this._commitPush();
    }

    // Watch for file saves
    this._watcher = vscode.workspace.createFileSystemWatcher('**/*');
    let debounceTimer: NodeJS.Timeout | null = null;

    this._watcher.onDidChange(async (uri) => {
      if (debounceTimer) clearTimeout(debounceTimer);
      debounceTimer = setTimeout(async () => {
        if (await this._hasChanges()) {
          await this._commitPush();
          this._updateStatus('$(cloud-upload)');
        }
      }, 2000);
    });

    this._watcher.onDidCreate(async () => {
      if (debounceTimer) clearTimeout(debounceTimer);
      debounceTimer = setTimeout(async () => {
        if (await this._hasChanges()) {
          await this._commitPush();
          this._updateStatus('$(cloud-upload)');
        }
      }, 2000);
    });

    // Auto-pull every 5 seconds
    this._pullTimer = setInterval(async () => {
      const pulled = await this._pull();
      if (pulled) {
        this._pulledUpdates++;
        this._updateStatus('$(cloud-download)');
        // Refresh open editors
        await vscode.commands.executeCommand('workbench.action.files.revert');
      }
    }, 5000);

    // Auto-pull on window focus
    vscode.window.onDidChangeWindowState(async (e) => {
      if (e.focused) {
        await this._pull();
        await vscode.commands.executeCommand('workbench.action.files.revert');
      }
    });

    this._statusBar.show();
    this._updateStatus('$(sync~spin)');
    vscode.window.showInformationMessage(`Hermes Live Sync: ${this._branch}`);
  }

  stop(): void {
    this._running = false;
    this._watcher?.dispose();
    this._watcher = null;
    if (this._pullTimer) { clearInterval(this._pullTimer); this._pullTimer = null; }
    this._statusBar.hide();
    vscode.window.showInformationMessage('Hermes Live Sync stopped');
  }

  getStatus(): { running: boolean; branch: string; pushedCommits: number; pulledUpdates: number } {
    return {
      running: this._running,
      branch: this._branch,
      pushedCommits: this._pushedCommits,
      pulledUpdates: this._pulledUpdates,
    };
  }

  private _updateStatus(icon: string): void {
    this._statusBar.text = `$(git-branch) Sync: ${this._branch} ${icon}`;
    this._statusBar.tooltip = `Hermes Live Sync [${this._branch}]\nPushed: ${this._pushedCommits}\nPulled: ${this._pulledUpdates}`;
  }

  private async _git(args: string[], ignoreError = false): Promise<string> {
    return new Promise((resolve) => {
      cp.exec(`git ${args.join(' ')}`, { cwd: this._workspaceDir, timeout: 30000 }, (err, stdout) => {
        if (err && !ignoreError) {
          console.error(`git ${args[0]} error:`, err.message);
        }
        resolve(stdout?.trim() ?? '');
      });
    });
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

  dispose(): void {
    this.stop();
    this._statusBar.dispose();
  }
}

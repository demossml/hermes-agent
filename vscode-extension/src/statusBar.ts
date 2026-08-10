/**
 * Status Bar — shows connection status, model, and latency.
 *
 * Displays in the right side of the VS Code status bar:
 *   ✓ LLM: model-name  12ms    (connected)
 *   ✗ LLM: offline              (disconnected)
 *   ⟳ LLM: connecting...        (starting)
 */

import * as vscode from 'vscode';
import { HttpClient } from './httpClient';

export class StatusBarManager {
  private _item: vscode.StatusBarItem;
  private _healthInterval: NodeJS.Timeout | null = null;

  constructor(private client: HttpClient) {
    this._item = vscode.window.createStatusBarItem(
      vscode.StatusBarAlignment.Right,
      100
    );
    this._item.command = 'hermes.checkConnection';
    this._item.tooltip = 'Hermes — click to check connection';
  }

  // ── Lifecycle ─────────────────────────────────────

  async start(): Promise<void> {
    // Initial check
    const health = await this.client.healthCheck();
    this._update(health);

    // Periodic health checks (every 60 seconds)
    this._healthInterval = setInterval(async () => {
      const h = await this.client.healthCheck();
      this._update(h);
    }, 60_000);
  }

  stop(): void {
    if (this._healthInterval) {
      clearInterval(this._healthInterval);
      this._healthInterval = null;
    }
    this._item.hide();
  }

  // ── Public API ────────────────────────────────────

  setConnecting(): void {
    this._item.text = '$(sync~spin) LLM: connecting...';
    this._item.backgroundColor = undefined;
    this._item.show();
  }

  async checkNow(): Promise<void> {
    this.setConnecting();
    const health = await this.client.healthCheck();
    this._update(health);

    if (health.ok) {
      const info = [
        `Connected to ${health.model || 'auto-detected model'}`,
        `Latency: ${health.latencyMs}ms`,
        `Endpoint: ${vscode.workspace.getConfiguration('hermes').get('endpoint', 'localhost:8642')}`,
        `Total requests: ${this.client.totalRequests}`,
        `Total tokens: ${this.client.totalTokens.toLocaleString()}`,
      ];
      vscode.window.showInformationMessage(info.join('\n'));
    } else {
      vscode.window.showWarningMessage(
        `Not connected to Hermes.\n${health.error || 'Check endpoint in settings.'}\n\nRun "Hermes: Check Connection" to retry.`
      );
    }
  }

  // ── Private ───────────────────────────────────────

  private _update(health: { ok: boolean; model?: string; latencyMs: number }): void {
    const cfg = vscode.workspace.getConfiguration('hermes');

    if (!cfg.get<boolean>('statusBarEnabled', true)) {
      this._item.hide();
      return;
    }

    if (health.ok) {
      const model = health.model || 'LLM';
      const showLatency = cfg.get<boolean>('statusBarLatency', true);
      const latencyStr = showLatency ? ` ${health.latencyMs}ms` : '';
      this._item.text = `$(check) ${model}${latencyStr}`;
      this._item.backgroundColor = undefined;
      this._item.tooltip = [
        `Model: ${health.model || 'auto'}`,
        `Latency: ${health.latencyMs}ms`,
        `Requests: ${this.client.totalRequests}`,
        `Tokens: ${this.client.totalTokens.toLocaleString()}`,
        'Click to check connection',
      ].join('\n');
    } else {
      this._item.text = '$(circle-slash) LLM: offline';
      this._item.backgroundColor = new vscode.ThemeColor(
        'statusBarItem.warningBackground'
      );
      this._item.tooltip = 'Hermes not reachable. Click to check.';
    }

    this._item.show();
  }
}

/**
 * Tailscale HTTP client for Hermes — JSON-RPC 2.0 over HTTP to Tailscale IP.
 */
import * as vscode from 'vscode';

export class TailscaleClient {
  private baseUrl: string;

  constructor(host: string, port: number) {
    this.baseUrl = `http://${host}:${port}`;
  }

  async chat(message: string, context?: any): Promise<string> {
    const r = await this._call('chat/send', { message, context });
    return r?.text ?? '';
  }

  async getStatus(): Promise<any> {
    return this._call('status/get', {});
  }

  async getInsights(limit: number = 10): Promise<any[]> {
    const r = await this._call('insights/list', { limit });
    return r?.insights ?? [];
  }

  async switchProject(name: string): Promise<void> {
    await this._call('project/switch', { name });
  }

  async runTester(filePath: string): Promise<any> {
    return this._call('tester/run', { file: filePath });
  }

  async askAboutCode(code: string, question: string): Promise<string> {
    const r = await this._call('code/ask', { code, question });
    return r?.answer ?? '';
  }

  async improveCode(code: string, filePath: string): Promise<string> {
    const r = await this._call('code/improve', { code });
    return r?.improved ?? '';
  }

  async generateTests(code: string, filePath: string): Promise<string> {
    const r = await this._call('code/generateTests', { code });
    return r?.tests ?? '';
  }

  async healthCheck(): Promise<boolean> {
    try {
      const r = await fetch(`${this.baseUrl}/health`);
      return r.ok;
    } catch {
      return false;
    }
  }

  private async _call(method: string, params: any): Promise<any> {
    const resp = await fetch(`${this.baseUrl}/api/jsonrpc`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', method, params, id: Date.now() }),
    });
    const data = await resp.json();
    if (data.error) throw new Error(data.error.message);
    return data.result;
  }
}

/**
 * Auto-discover Hermes server on Tailscale network.
 * Checks known ports on Tailscale IP.
 */
export async function discoverTailscaleServer(): Promise<{ host: string; port: number } | null> {
  const cp = require('child_process');
  try {
    const ip = cp.execSync('tailscale ip -4', { encoding: 'utf8', timeout: 5000 }).trim();
    if (!ip.startsWith('100.')) return null;

    const ports = [8787, 9119];
    for (const port of ports) {
      try {
        const resp = await fetch(`http://${ip}:${port}/health`);
        if (resp.ok) return { host: ip, port };
      } catch {}
    }
  } catch {}
  return null;
}

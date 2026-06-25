/**
 * Hermes ACP Client — JSON-RPC over stdio.
 */
import * as cp from 'child_process';
import * as vscode from 'vscode';

interface AcpMessage {
  jsonrpc: '2.0';
  id?: number | string;
  method?: string;
  params?: any;
  result?: any;
  error?: { code: number; message: string };
}

export class HermesClient {
  private process: cp.ChildProcess | null = null;
  private requestId = 0;
  private pending = new Map<number, (msg: AcpMessage) => void>();
  private buffer = '';
  private _onProjectSwitch = new vscode.EventEmitter<string>();
  private _onInsight = new vscode.EventEmitter<{ text: string; importance: number }>();

  readonly onProjectSwitch = this._onProjectSwitch.event;
  readonly onInsight = this._onInsight.event;

  async start(): Promise<void> {
    const cliPath = vscode.workspace.getConfiguration('hermes').get<string>('cliPath', 'hermes');
    const workspaceFolder = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;

    this.process = cp.spawn(cliPath, ['acp'], {
      stdio: ['pipe', 'pipe', 'pipe'],
      cwd: workspaceFolder,
      env: { ...process.env, HERMES_SOURCE: 'vscode' },
    });

    this.process.stdout?.on('data', (chunk: Buffer) => this._onData(chunk.toString()));
    this.process.stderr?.on('data', (d: Buffer) => console.error('[hermes]', d.toString()));
    this.process.on('exit', (code) => console.log(`[hermes] exited ${code}`));

    await this._sendRequest('initialize', {
      client: 'vscode-extension',
      version: '1.1.0',
      workspaceFolder,
    });
  }

  async chat(message: string, context?: any): Promise<string> {
    const r = await this._sendRequest('chat/send', { message, context });
    return (r as any)?.text ?? (r as any)?.reply ?? '';
  }

  async getStatus(): Promise<any> {
    return this._sendRequest('status/get', {});
  }

  async getInsights(limit: number = 10): Promise<any[]> {
    const r = await this._sendRequest('insights/list', { limit });
    return (r as any)?.insights ?? [];
  }

  async switchProject(name: string): Promise<void> {
    await this._sendRequest('project/switch', { name });
    this._onProjectSwitch.fire(name);
  }

  async runTester(filePath: string): Promise<any> {
    return this._sendRequest('tester/run', { file: filePath, level: 'full' });
  }

  async askAboutCode(code: string, question: string): Promise<string> {
    const r = await this._sendRequest('code/ask', { code, question });
    return (r as any)?.answer ?? '';
  }

  async improveCode(code: string, filePath: string): Promise<string> {
    const r = await this._sendRequest('code/improve', { code, file: filePath });
    return (r as any)?.improved ?? '';
  }

  async generateTests(code: string, filePath: string): Promise<string> {
    const r = await this._sendRequest('code/generateTests', { code, file: filePath });
    return (r as any)?.tests ?? '';
  }

  async inlineComplete(
    surroundingCode: string,
    filePath: string,
    line: number,
    character: number
  ): Promise<string> {
    const r = await this._sendRequest('code/inlineComplete', {
      code: surroundingCode,
      file: filePath,
      line,
      character,
    });
    return (r as any)?.completion ?? '';
  }

  dispose(): void {
    this.process?.kill();
    this.process = null;
  }

  private _onData(data: string): void {
    this.buffer += data;
    const lines = this.buffer.split('\n');
    this.buffer = lines.pop() ?? '';
    for (const line of lines) {
      if (!line.trim()) continue;
      try {
        const msg: AcpMessage = JSON.parse(line);
        if (msg.id !== undefined && this.pending.has(msg.id as number)) {
          this.pending.get(msg.id as number)!(msg);
          this.pending.delete(msg.id as number);
        }
      } catch {}
    }
  }

  private _sendRequest(method: string, params: any): Promise<any> {
    return new Promise((resolve, reject) => {
      const id = ++this.requestId;
      this.pending.set(id, (response) => {
        response.error ? reject(new Error(response.error.message)) : resolve(response.result);
      });
      this.process?.stdin?.write(JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n');
      setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id);
          reject(new Error(`Request ${method} timed out`));
        }
      }, 120_000);
    });
  }
}

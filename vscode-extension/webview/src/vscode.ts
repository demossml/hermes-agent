/**
 * VS Code API wrapper for the webview.
 */
import type { WebviewApi } from 'vscode-webview';

class VSCodeWrapper {
  private readonly api: WebviewApi<any> | undefined;

  constructor() {
    if (typeof acquireVsCodeApi === 'function') {
      this.api = acquireVsCodeApi();
    }
  }

  postMessage(msg: any): void {
    this.api?.postMessage(msg);
  }

  getState(): any {
    return this.api?.getState();
  }

  setState(state: any): void {
    this.api?.setState(state);
  }
}

export const vscode = new VSCodeWrapper();

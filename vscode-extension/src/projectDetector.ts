/**
 * Auto-detects the current Hermes project from the workspace folder.
 * Looks for .hermes-project marker file or derives from folder structure.
 */
import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';

export function detectProject(workspaceFolder?: vscode.WorkspaceFolder): string | null {
  if (!workspaceFolder) return null;
  const root = workspaceFolder.uri.fsPath;

  // 1. .hermes-project marker file
  const marker = path.join(root, '.hermes-project');
  if (fs.existsSync(marker)) {
    return fs.readFileSync(marker, 'utf8').trim();
  }

  // 2. Inside ~/.hermes/projects/<slug>/
  const hermesHome = process.env.HERMES_HOME ?? path.join(require('os').homedir(), '.hermes');
  const projectsDir = path.join(hermesHome, 'projects');
  if (root.startsWith(projectsDir)) {
    const rel = path.relative(projectsDir, root);
    return rel.split(path.sep)[0] || null;
  }

  // 3. Derive from folder name
  return path.basename(root).toLowerCase().replace(/[^a-z0-9]+/g, '-');
}

import React from 'react';

const actions = [
  { cmd: 'hermes.runTesterOnFile', label: 'Run Tester', icon: '🧪' },
  { cmd: 'hermes.analyzeWithBrowser', label: 'Browser Test', icon: '🌐' },
  { cmd: 'hermes.showInsights', label: 'Get Insights', icon: '💡' },
  { cmd: 'hermes.inlineImprove', label: 'Improve', icon: '✨' },
  { cmd: 'hermes.showStatus', label: 'Status', icon: '📊' },
];

export const QuickActions: React.FC<{ onCommand: (cmd: string) => void }> = ({ onCommand }) => (
  <div style={{ display: 'flex', gap: 4, padding: '4px 8px', flexWrap: 'wrap' }}>
    {actions.map((a) => (
      <button
        key={a.cmd}
        onClick={() => onCommand(a.cmd)}
        title={a.label}
        style={{
          background: 'var(--vscode-button-secondaryBackground)',
          color: 'var(--vscode-button-secondaryForeground)',
          border: 'none',
          borderRadius: 5,
          padding: '4px 10px',
          cursor: 'pointer',
          fontSize: 11,
          fontWeight: 500,
          display: 'flex',
          alignItems: 'center',
          gap: 4,
        }}
      >
        <span>{a.icon}</span>
        <span>{a.label}</span>
      </button>
    ))}
  </div>
);

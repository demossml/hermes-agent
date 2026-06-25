import React, { useEffect, useRef } from 'react';
import { marked } from 'marked';

interface Message {
  id: string;
  role: 'user' | 'agent' | 'system';
  text: string;
  timestamp: number;
}

export const MessageList: React.FC<{ messages: Message[]; loading: boolean }> = ({
  messages,
  loading,
}) => {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, loading]);

  if (messages.length === 0 && !loading) {
    return (
      <div
        style={{
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          padding: 20,
          color: 'var(--vscode-descriptionForeground)',
          fontSize: 12,
          textAlign: 'center',
        }}
      >
        <div style={{ fontSize: 40, marginBottom: 12, opacity: 0.3 }}>☤</div>
        <div style={{ fontWeight: 600, marginBottom: 4 }}>Hermes Agent</div>
        <div>Ask a question, run tests, or improve your code.</div>
      </div>
    );
  }

  return (
    <div
      style={{
        flex: 1,
        overflowY: 'auto',
        padding: '4px 8px',
      }}
    >
      {messages.map((msg) => (
        <div key={msg.id} style={{ marginBottom: 8 }}>
          {/* Role badge */}
          <div
            style={{
              fontSize: 10,
              fontWeight: 600,
              marginBottom: 2,
              color:
                msg.role === 'user'
                  ? 'var(--hermes-blue)'
                  : msg.role === 'agent'
                  ? 'var(--hermes-accent)'
                  : 'var(--vscode-descriptionForeground)',
            }}
          >
            {msg.role === 'user' ? 'You' : msg.role === 'agent' ? 'Hermes' : 'System'}
          </div>
          {/* Content */}
          <div
            style={{
              padding: '8px 12px',
              borderRadius: 8,
              background:
                msg.role === 'user'
                  ? 'var(--vscode-button-background)'
                  : msg.role === 'agent'
                  ? 'var(--vscode-input-background)'
                  : 'transparent',
              color:
                msg.role === 'user'
                  ? 'var(--vscode-button-foreground)'
                  : 'var(--vscode-foreground)',
              border:
                msg.role === 'agent' ? '1px solid var(--vscode-input-border)' : 'none',
              fontSize: 13,
              lineHeight: 1.6,
              maxWidth: '95%',
              wordBreak: 'break-word',
            }}
            dangerouslySetInnerHTML={{
              __html: msg.role === 'agent' ? marked.parse(msg.text) as string : escapeHtml(msg.text),
            }}
          />
        </div>
      ))}
      {loading && (
        <div style={{ padding: '8px 12px', display: 'flex', alignItems: 'center', gap: 8 }}>
          <span className="typing-dot" style={{ animation: 'blink 1.4s infinite', fontSize: 16, color: 'var(--hermes-accent)' }}>●</span>
          <span style={{ color: 'var(--vscode-descriptionForeground)', fontSize: 12 }}>Hermes is thinking...</span>
        </div>
      )}
      <div ref={endRef} />
    </div>
  );
};

function escapeHtml(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

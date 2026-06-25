import React, { useState, useRef, KeyboardEvent } from 'react';

export const ChatInput: React.FC<{ onSend: (text: string) => void; disabled: boolean }> = ({
  onSend,
  disabled,
}) => {
  const [text, setText] = useState('');
  const ref = useRef<HTMLTextAreaElement>(null);

  const send = () => {
    const trimmed = text.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setText('');
    ref.current?.focus();
  };

  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  return (
    <div
      style={{
        padding: 8,
        borderTop: '1px solid var(--vscode-sideBar-border)',
        display: 'flex',
        gap: 6,
      }}
    >
      <textarea
        ref={ref}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder={disabled ? 'Hermes is thinking...' : 'Ask Hermes... (Enter to send, Shift+Enter for newline)'}
        disabled={disabled}
        rows={1}
        style={{
          flex: 1,
          background: 'var(--vscode-input-background)',
          color: 'var(--vscode-input-foreground)',
          border: '1px solid var(--vscode-input-border)',
          borderRadius: 6,
          padding: '8px 10px',
          fontFamily: 'inherit',
          fontSize: 13,
          resize: 'vertical',
          minHeight: 36,
          maxHeight: 120,
          outline: 'none',
        }}
      />
      <button
        onClick={send}
        disabled={disabled || !text.trim()}
        style={{
          background: 'var(--hermes-accent)',
          color: '#fff',
          border: 'none',
          borderRadius: 6,
          padding: '8px 14px',
          cursor: disabled ? 'not-allowed' : 'pointer',
          opacity: disabled ? 0.5 : 1,
          fontSize: 13,
          fontWeight: 600,
          whiteSpace: 'nowrap',
        }}
      >
        Send
      </button>
    </div>
  );
};

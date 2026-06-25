import React, { useState, useEffect, useCallback } from 'react';
import { vscode } from './vscode';
import { ProjectCard } from './components/ProjectCard';
import { QuickActions } from './components/QuickActions';
import { MessageList } from './components/MessageList';
import { ChatInput } from './components/ChatInput';
import { marked } from 'marked';

interface Message {
  id: string;
  role: 'user' | 'agent' | 'system';
  text: string;
  timestamp: number;
}

interface ProjectInfo {
  name: string;
  qualityScore: number;
  activeWorkflows: number;
  insightsCount: number;
  isolation: string;
}

export default function App() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [project, setProject] = useState<ProjectInfo>({
    name: '',
    qualityScore: 0,
    activeWorkflows: 0,
    insightsCount: 0,
    isolation: 'hard',
  });
  const [loading, setLoading] = useState(false);

  const addMessage = useCallback((text: string, role: 'user' | 'agent' | 'system') => {
    const msg: Message = {
      id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
      text,
      role,
      timestamp: Date.now(),
    };
    setMessages((prev) => [...prev, msg]);
  }, []);

  const handleSend = useCallback(
    (text: string) => {
      addMessage(text, 'user');
      setLoading(true);
      vscode.postMessage({ type: 'chat', text });
    },
    [addMessage]
  );

  const handleCommand = useCallback((cmd: string) => {
    vscode.postMessage({ type: 'command', command: cmd });
  }, []);

  useEffect(() => {
    const handler = (e: MessageEvent) => {
      const msg = e.data;
      switch (msg.type) {
        case 'response':
          setLoading(false);
          addMessage(msg.text, 'agent');
          break;
        case 'projectUpdate':
          setProject((prev) => ({ ...prev, name: msg.name }));
          break;
        case 'projectStatus':
          setProject((prev) => ({
            ...prev,
            qualityScore: msg.qualityScore ?? prev.qualityScore,
            activeWorkflows: msg.activeWorkflows ?? prev.activeWorkflows,
            insightsCount: msg.insightsCount ?? prev.insightsCount,
          }));
          break;
      }
    };
    window.addEventListener('message', handler);
    return () => window.removeEventListener('message', handler);
  }, [addMessage]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh' }}>
      {/* Header */}
      <div
        style={{
          padding: '8px 12px',
          borderBottom: '1px solid var(--vscode-sideBar-border)',
          fontWeight: 600,
          fontSize: 11,
          textTransform: 'uppercase',
          letterSpacing: 0.5,
          color: 'var(--vscode-sideBarTitle-foreground)',
          display: 'flex',
          alignItems: 'center',
          gap: 6,
        }}
      >
        <span style={{ color: 'var(--hermes-accent)', fontSize: 16 }}>☤</span>
        Hermes Agent
        {project.name && (
          <span style={{ marginLeft: 'auto', color: 'var(--hermes-accent)', fontWeight: 400, fontSize: 11 }}>
            {project.name}
          </span>
        )}
      </div>

      {/* Project Card */}
      {project.name && <ProjectCard project={project} />}

      {/* Quick Actions */}
      <QuickActions onCommand={handleCommand} />

      {/* Messages */}
      <MessageList messages={messages} loading={loading} />

      {/* Input */}
      <ChatInput onSend={handleSend} disabled={loading} />
    </div>
  );
}

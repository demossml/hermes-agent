import React from 'react';

interface ProjectInfo {
  name: string;
  qualityScore: number;
  activeWorkflows: number;
  insightsCount: number;
  isolation: string;
}

const scoreColor = (s: number) =>
  s >= 8 ? 'var(--hermes-green)' : s >= 5 ? 'var(--hermes-yellow)' : 'var(--hermes-red)';

export const ProjectCard: React.FC<{ project: ProjectInfo }> = ({ project }) => (
  <div
    style={{
      margin: '8px 8px 0',
      padding: '12px',
      borderRadius: 8,
      background: 'var(--vscode-input-background)',
      border: '1px solid var(--vscode-input-border)',
    }}
  >
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
      <span style={{ fontWeight: 600, fontSize: 13, color: 'var(--hermes-accent)' }}>☤ {project.name}</span>
      <span
        style={{
          fontSize: 10,
          padding: '2px 6px',
          borderRadius: 4,
          background: project.isolation === 'hard' ? 'rgba(52, 211, 153, 0.15)' : 'rgba(251, 191, 36, 0.15)',
          color: project.isolation === 'hard' ? 'var(--hermes-green)' : 'var(--hermes-yellow)',
        }}
      >
        {project.isolation === 'hard' ? '🔒 Hard' : '⚠ Soft'}
      </span>
    </div>

    <div style={{ display: 'flex', gap: 16, fontSize: 11 }}>
      <Stat label="Quality" value={project.qualityScore} color={scoreColor(project.qualityScore)} suffix="/10" />
      <Stat label="Workflows" value={project.activeWorkflows} color="var(--hermes-blue)" />
      <Stat label="Insights" value={project.insightsCount} color="var(--hermes-accent)" />
    </div>
  </div>
);

const Stat: React.FC<{ label: string; value: number; color: string; suffix?: string }> = ({
  label,
  value,
  color,
  suffix,
}) => (
  <div style={{ textAlign: 'center' }}>
    <div style={{ fontSize: 18, fontWeight: 700, color }}>{value}{suffix ?? ''}</div>
    <div style={{ color: 'var(--vscode-descriptionForeground)', marginTop: 1 }}>{label}</div>
  </div>
);

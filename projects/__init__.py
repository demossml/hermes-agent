"""Project Manager — multi-project isolation for Hermes.

Projects provide:
- Isolated session trees (``subtree_session_id = "project-{project_id}"``)
- Per-project ChromaDB collections (``project_{project_id}``)
- DuckDB records tagged with ``project_id``
- Project-scoped memory and conversation history.

Usage::

    from projects.project_manager import ProjectManager

    pm = ProjectManager()
    pm.create_project("My Web App")
    pm.switch_project("my-web-app")
    proj = pm.get_current_project()
"""

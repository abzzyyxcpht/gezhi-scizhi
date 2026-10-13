"""Workspace defaults are separate from execution modes and saved agent roles."""
from __future__ import annotations

MODES = {
    'general': {
        'id': 'general', 'label': '通用', 'title': '通用工作台', 'agent': 'general',
        'description': '代码、文件、终端与日常任务',
        'placeholder': '描述要完成的任务，或输入 / 查看命令',
        'shortcuts': ['project', 'agent', 'plan', 'terminal', 'chat', 'mode'],
    },
    'research': {
        'id': 'research', 'label': '科研', 'title': '科研工作台', 'agent': 'research',
        'description': '文献、数据、实验与可追溯科研产物',
        'placeholder': '描述研究问题，或输入 / 查看命令',
        'shortcuts': ['new', 'evidence', 'stats', 'notebook', 'swarm', 'mode'],
    },
}


def normalize_mode(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError('工作台模式须为 general 或 research')
    mode = {'通用': 'general', '科研': 'research'}.get(value.strip().lower(), value.strip().lower())
    if mode not in MODES:
        raise ValueError('工作台模式须为 general 或 research')
    return mode


def mode_for_agent(agent: str | None) -> str:
    return 'general' if agent == 'general' else 'research'


def default_agent(workspace_mode: str | None = None) -> str:
    if workspace_mode is None:
        from . import settings
        workspace_mode = settings.load().get('workspace_mode', 'research')
    return MODES[normalize_mode(workspace_mode)]['agent']


def mode_catalog() -> list[dict]:
    return [{**value, 'shortcuts': list(value['shortcuts'])} for value in MODES.values()]

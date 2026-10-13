"""Local first-run facts and an explicitly offline built-in demonstration."""
from __future__ import annotations

from . import config, model_protocols, model_registry, settings
from .version import APP_VERSION
from .workbench import redact

DEMO_NOTICE = '内置合成样例，仅供体验；不可作为真实科研证据。'
EXAMPLE_REQUEST = '检索大语言模型智能体研究，整理支持与反驳证据。'


def readiness() -> dict:
    """No probes, model requests or preference changes; configuration is not connectivity."""
    values = settings.load()
    model = {'status': 'not_configured', 'model': None, 'protocol': None,
             'connection_verified': False, 'execution_ready': False,
             'reason': '尚未配置规划模型；可以先体验离线研究，或在设置中配置模型。'}
    if not getattr(config, 'LLM_ENABLED', values.get('llm_enabled', True)):
        model.update(status='disabled', reason='模型增强已关闭；可在模型设置中启用。')
    else:
        try:
            profile = model_registry.resolve('planning')
            if profile.get('model') and profile.get('base_url'):
                model_protocols.endpoint(profile)
                model.update(status='configured', model=redact(str(profile['model'])),
                             protocol=model_protocols.protocol_of(profile), execution_ready=True,
                             reason='模型配置已填写，尚未验证连接；可在模型设置中检测连接。')
        except (ValueError, TypeError, KeyError):
            model.update(status='invalid', reason='模型连接、协议或规划路由无效，请打开模型设置检查。')
    offline_available = (config.ROOT_DIR / 'workflows/literature_scan.yaml').is_file()
    default_offline = bool(values.get('default_offline', True))
    return {'version': APP_VERSION, 'agent_mode': 'model' if model['execution_ready'] else 'rules',
            'model': model,
            'research': {'default_offline': default_offline, 'offline_available': offline_available,
                         'notice': DEMO_NOTICE},
            'checks': [
                {'id': 'model', 'title': '规划模型',
                 'status': 'info' if model['execution_ready'] else 'action_needed', 'detail': model['reason']},
                {'id': 'demo', 'title': '离线体验', 'status': 'ready' if offline_available else 'action_needed',
                 'detail': '使用内置文献与规则评分，不访问在线检索、模型或向量服务。'
                           if offline_available else '离线工作流资源缺失，请检查安装或重新解压源码包。'},
                {'id': 'default_research', 'title': '新科研任务默认模式', 'status': 'info',
                 'detail': '离线示例' if default_offline else '在线文献检索；离线体验按钮仍强制使用示例。'},
                {'id': 'connection', 'title': '连接验证', 'status': 'info',
                 'detail': '此检查只读取本地配置，不调用模型、不测试网络、不保存设置。'},
            ],
            'suggestions': {'offline_command': '/demo', 'example_request': EXAMPLE_REQUEST,
                            'settings_command': '/models'}}


def start_demo() -> dict:
    """User-invoked demo overrides offline for this task, never persisted settings."""
    if not (config.ROOT_DIR / 'workflows/literature_scan.yaml').is_file():
        raise ValueError('离线工作流资源缺失，请检查安装或重新解压源码包。')
    from .workbench import handle
    result = handle({'cmd': 'workflow_run', 'workflow_id': 'literature_scan',
                     'topic': 'large language model agents', 'offline': True})
    if result.get('ok'):
        result.update(workflow_id='literature_scan', offline=True, notice=DEMO_NOTICE)
    return result

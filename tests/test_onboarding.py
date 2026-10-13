"""First-run actions explain execution mode without sending or replacing drafts."""
from __future__ import annotations

import importlib
import importlib.util
from types import SimpleNamespace
from unittest.mock import Mock

import flet as ft


def readiness(**changes):
    row = {'version': '0.6.0', 'agent_mode': 'rules',
        'model': {'status': 'not_configured', 'model': None, 'protocol': None,
                  'connection_verified': False, 'execution_ready': False, 'reason': '尚未配置模型。'},
        'research': {'default_offline': True, 'offline_available': True,
                     'notice': '内置合成样例，仅供体验；不可作为真实科研证据。'},
        'checks': [], 'suggestions': {'offline_command': '/demo',
            'example_request': '检索大语言模型智能体研究，整理支持与反驳证据。', 'settings_command': '/models'}}
    return {**row, **changes}


def panel_fixture(api=None):
    assert importlib.util.find_spec('src.ui.onboarding') is not None, 'first-run panel is not implemented'
    module = importlib.import_module('src.ui.onboarding')
    queue = []
    page = SimpleNamespace(update=Mock(), run_thread=queue.append, show_dialog=Mock())
    request, open_models, open_demo = ft.TextField(), Mock(), Mock()
    api = api or Mock(return_value={'ok': True, 'readiness': readiness()})
    panel = module.OnboardingPanel(page, request, open_models=open_models, open_demo=open_demo, api=api)
    return panel, request, queue, page, api, open_models, open_demo


def test_unconfigured_user_sees_rules_and_usable_offline_entry():
    panel, _, _, _, api, _, _ = panel_fixture()
    panel.refresh()
    assert '规则' in panel.heading.value
    assert '尚未配置' in panel.detail.value
    assert '合成样例' in panel.offline_note.value
    assert not panel.demo_button.disabled
    api.assert_called_once_with({'cmd': 'product_readiness'})


def test_configured_model_is_not_presented_as_a_verified_connection():
    row = readiness(agent_mode='model', model={'status': 'configured', 'model': 'my-model',
        'protocol': 'responses', 'connection_verified': False, 'execution_ready': True, 'reason': ''})
    panel, _, _, _, _, _, _ = panel_fixture(Mock(return_value={'ok': True, 'readiness': row}))
    panel.refresh()
    assert '模型' in panel.heading.value and 'my-model' in panel.detail.value
    assert '尚未验证' in panel.detail.value
    assert '连接成功' not in panel.detail.value


def test_filling_example_preserves_existing_draft_and_never_starts_execution():
    panel, request, queue, _, api, _, _ = panel_fixture()
    panel.refresh()
    request.value = '用户正在编写的研究目标'
    panel.fill_example()
    assert request.value == '用户正在编写的研究目标'
    assert '保留' in panel.notice.value
    request.value = ''
    panel.fill_example()
    assert request.value == readiness()['suggestions']['example_request']
    assert not queue and api.call_count == 1


def test_model_setup_action_is_only_navigation():
    panel, request, _, _, api, open_models, _ = panel_fixture()
    request.value = '草稿'
    panel.settings_button.on_click(None)
    open_models.assert_called_once()
    api.assert_not_called()
    assert request.value == '草稿'


def test_demo_is_explicit_background_action_and_ignores_online_default():
    row = readiness(research={'default_offline': False, 'offline_available': True, 'notice': '合成样例'})
    api = Mock(side_effect=[{'ok': True, 'readiness': row}, {'ok': True, 'task_id': 'offline-demo',
        'workflow_id': 'literature_scan', 'offline': True, 'notice': '内置合成样例，仅供体验。'}])
    panel, request, queue, _, _, _, open_demo = panel_fixture(api)
    panel.refresh()
    request.value = '保留这个草稿'
    panel.start_demo()
    panel.start_demo()
    assert len(queue) == 1 and panel.demo_button.disabled
    assert api.call_count == 1
    queue.pop()()
    api.assert_called_with({'cmd': 'product_demo'})
    open_demo.assert_called_once_with('offline-demo')
    assert request.value == '保留这个草稿'
    assert not panel.demo_button.disabled


def test_demo_failure_explains_error_and_allows_retry_without_navigation():
    api = Mock(side_effect=[{'ok': True, 'readiness': readiness()}, {'ok': False, 'error': '示例文件无法读取'}])
    panel, _, queue, page, _, _, open_demo = panel_fixture(api)
    panel.refresh()
    panel.start_demo()
    queue.pop()()
    assert not panel.demo_button.disabled and '示例文件无法读取' in panel.notice.value
    assert page.show_dialog.called
    open_demo.assert_not_called()


def test_readiness_failure_does_not_guess_model_or_demo_availability():
    panel, _, _, _, _, _, _ = panel_fixture(Mock(return_value={'ok': False, 'error': '配置暂不可读'}))
    panel.refresh()
    assert '无法读取' in panel.heading.value
    assert panel.demo_button.disabled
    assert not panel.settings_button.disabled


def test_onboarding_collapses_for_started_sessions_but_user_can_reopen_it():
    panel, _, _, _, _, _, _ = panel_fixture()
    panel.selected = lambda: 'existing-session'
    panel.refresh()
    assert hasattr(panel, 'disclosure'), 'onboarding must be progressively disclosed'
    assert not panel.disclosure.expanded
    panel.disclosure.expanded = True
    panel.disclosure.on_change(SimpleNamespace(data='true'))
    panel.refresh()
    assert panel.disclosure.expanded

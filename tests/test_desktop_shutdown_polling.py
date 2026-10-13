import asyncio
import unittest
from unittest import mock

from tests import test_desktop_draft_audit as audit


class DesktopShutdownPollingTest(unittest.TestCase):
    def setUp(self):
        fixture=audit.DesktopDraftAuditTests('runTest')
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.ui=fixture.ui
        self.addCleanup(self.ui.drafts.close)

    def test_poller_returns_if_close_happens_while_sleeping(self):
        class UnwantedRefresh(Exception):
            pass
        async def close_during_sleep(_):
            self.ui.state['poll']=False
        with mock.patch('src.ui.desktop.asyncio.sleep',side_effect=close_during_sleep), \
                mock.patch.object(self.ui.session_status,'refresh',side_effect=UnwantedRefresh):
            self.ui.agent_state['session_id']='session-a'
            self.ui.nav_index['i']=4
            self.ui.composer.consume_events=mock.Mock()
            asyncio.run(self.ui.poller())
        self.ui.composer.consume_events.assert_not_called()

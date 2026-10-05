"""Window placement and resizing on monitors with nonzero/negative origins."""
import unittest
from unittest.mock import Mock
from tools.exe_launcher import NativeAssistantAgent


class AssistantMonitorTests(unittest.TestCase):
    def agent(self, area, avatar_x, avatar_y=200):
        agent = NativeAssistantAgent.__new__(NativeAssistantAgent)
        agent.root, agent.chat = Mock(), Mock()
        agent.avatar_x, agent.avatar_y = avatar_x, avatar_y
        agent.avatar_w, agent.avatar_h = 180, 220
        agent.chat_w, agent.chat_h = 500, 360
        agent.snake_active, agent.close_button = False, None
        agent._monitor_work_area = Mock(return_value=area)
        agent._place_resize_edges, agent._place_tail = Mock(), Mock()
        return agent

    def test_open_chat_uses_second_monitor_work_area(self):
        for area, avatar, expected in (((1920, 0, 3840, 1040), 3000, '500x360+2476+208'),
                                       ((-1920, -200, 0, 840), -700, '500x360+-1224+208')):
            agent = self.agent(area, avatar)
            agent._place_chat()
            agent.chat.geometry.assert_called_once_with(expected)
            agent.root.geometry.assert_not_called()

    def test_resize_keeps_chat_on_its_monitor(self):
        for area, x in (((1920, 0, 3840, 1040), 2476), ((-1920, -200, 0, 840), -1224)):
            agent = self.agent(area, x + 524)
            agent.chat.winfo_rootx.return_value = x
            agent.resize_start = {'edge': 'bottom-right', 'side': 'left', 'x': x, 'y': 208,
                                  'width': 500, 'height': 360, 'pointer_x': x + 500, 'pointer_y': 568,
                                  'avatar_x': agent.avatar_x, 'work_area': area}
            agent._update_chat_resize(x + 550, 608)
            agent.chat.geometry.assert_called_once_with(f'550x400+{x}+208')
            self.assertEqual(agent.avatar_x, x + 574)
            self.assertGreaterEqual(agent.avatar_x, area[0])
            self.assertLess(agent.avatar_x, area[2])

    def test_bottom_resize_does_not_move_avatar(self):
        agent = self.agent((1920, 100, 3840, 1180), 3000, 600)
        agent.chat.winfo_rootx.return_value = 2476
        agent.resize_start = {'edge': 'bottom', 'side': 'left', 'x': 2476, 'y': 608,
                              'width': 500, 'height': 360, 'pointer_x': 2976, 'pointer_y': 968,
                              'avatar_x': 3000, 'work_area': (1920, 100, 3840, 1180)}
        agent._update_chat_resize(2976, 1018)
        agent.root.geometry.assert_not_called()
        agent.chat.geometry.assert_called_once_with('500x410+2476+608')

"""Exercise the mini helper's Tk controls with isolated disposable history.

No input automation, live server, user history, model or GPU is used.
"""
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tkinter as tk
from tools import exe_launcher as desktop


def main():
    results, errors = {}, []
    original_tk = tk.Tk
    with tempfile.TemporaryDirectory() as temporary:
        state_dir = Path(temporary)
        with patch.object(desktop, 'assistant_state_dir', return_value=state_dir), \
             patch.object(desktop.NativeAssistantAgent, '_refresh_bootstrap'), \
             patch.object(desktop.NativeAssistantAgent, '_load_frames'):
            desktop.write_chat_state({'source': 'new-conversation', 'activeConversationId': 'first',
                'conversations': [{'id': 'first', 'title': 'Existing dialog', 'messages': [
                    {'id': 'msg', 'role': 'user', 'text': 'Keep this history\n' + '\n'.join(f'Line {i}' for i in range(40)), 'createdAt': '2026-10-02T17:00:00+00:00'}]}]})
            with patch('threading.Thread.start'):
                agent = desktop.NativeAssistantAgent(lambda: 0)

            def exercise():
                try:
                    agent._toggle_dialog_sidebar()
                    assert agent.dialog_sidebar_open and agent.dialog_sidebar.winfo_manager() == 'pack'
                    previous_count = len(desktop.read_chat_state()['conversations'])
                    agent._new_assistant_dialog()
                    state = desktop.read_chat_state()
                    assert len(state['conversations']) == previous_count + 1 and state['activeConversationId'] != 'first'
                    agent.dialog_listbox.selection_clear(0, 'end')
                    agent.dialog_listbox.selection_set(agent.dialog_ids.index('first'))
                    agent._select_assistant_dialog()
                    state = desktop.read_chat_state()
                    assert state['activeConversationId'] == 'first'
                    text = agent.history_box.get('1.0', 'end')
                    assert 'Keep this history' in text and '02.10.2026' in text
                    agent.chat.update_idletasks()
                    agent.history_box.yview_moveto(0)
                    before = agent.history_box.index('@0,0')
                    desktop.append_shared_message('assistant', 'New reply', conversation_id='first')
                    agent._render_shared_history(force=True)
                    assert agent.history_box.index('@0,0') == before
                    agent._toggle_dialog_sidebar()
                    assert not agent.dialog_sidebar_open and not agent.dialog_sidebar.winfo_manager()
                    results.update(passed=True, created_dialog=True, selected_existing=True,
                                   history_preserved=True, timestamps_visible=True, menu_hidden=True, manual_scroll_preserved=True)
                except BaseException as error:
                    errors.append(error)
                finally:
                    agent.root.destroy()

            def hidden_root(*args, **kwargs):
                root = original_tk(*args, **kwargs)
                root.withdraw()
                root.after(10, exercise)
                return root

            with patch.object(tk, 'Tk', side_effect=hidden_root):
                agent._run()
    if errors:
        raise errors[0]
    print(json.dumps(results))


if __name__ == '__main__':
    main()

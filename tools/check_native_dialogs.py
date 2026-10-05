"""Exercise the mini helper's Tk controls with isolated disposable history.

No input automation, live server, user history, model or GPU is used.
"""
import json
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tkinter as tk
from tools import exe_launcher as desktop


def main():
    results, errors = {}, []
    bootstrap_started, release_bootstrap, bootstrap_finished = threading.Event(), threading.Event(), threading.Event()
    ui_started = time.monotonic()
    def slow_request(_agent, method, path, *args, **kwargs):
        bootstrap_started.set()
        release_bootstrap.wait(5)
        bootstrap_finished.set()
        return {'models': [], 'settings': {'runtime': {'n_ctx': 4096, 'max_tokens': 512}}}
    original_tk = tk.Tk
    with tempfile.TemporaryDirectory() as temporary:
        state_dir = Path(temporary)
        with patch.object(desktop, 'assistant_state_dir', return_value=state_dir), \
             patch.object(desktop.NativeAssistantAgent, '_json_request', slow_request), \
             patch.object(desktop.NativeAssistantAgent, '_load_frames'):
            desktop.write_chat_state({'source': 'new-conversation', 'activeConversationId': 'first',
                'conversations': [{'id': 'first', 'title': 'Existing dialog', 'messages': [
                    {'id': 'msg', 'role': 'user', 'text': 'Keep this history\n' + '\n'.join(f'Line {i}' for i in range(40)), 'createdAt': '2026-10-02T17:00:00+00:00'}]}]})
            with patch('threading.Thread.start'):
                agent = desktop.NativeAssistantAgent(lambda: 0)

            def exercise():
                try:
                    assert bootstrap_started.is_set() and not bootstrap_finished.is_set(), 'Tk waited for the HTTP request'
                    assert time.monotonic() - ui_started < 3, 'Tk event processing was delayed'
                    agent.drag_start = (100, 100, agent.avatar_x, agent.avatar_y)
                    old_x, old_y = agent.avatar_x, agent.avatar_y
                    agent._move_avatar_to_pointer(140, 120)
                    assert (agent.avatar_x, agent.avatar_y) == (old_x + 40, old_y + 20)
                    agent.drag_start = None
                    desktop.append_shared_message('assistant', 'Загружаю модель.', message_id='pending', conversation_id='first', pending=True)
                    agent._render_shared_history(force=True)
                    agent._toggle_dialog_sidebar()
                    assert agent.dialog_sidebar_open and agent.dialog_sidebar.winfo_manager() == 'pack'
                    previous_count = len(desktop.read_chat_state()['conversations'])
                    agent._new_assistant_dialog()
                    state = desktop.read_chat_state()
                    assert len(state['conversations']) == previous_count + 1 and state['activeConversationId'] != 'first'
                    reading = state['activeConversationId']
                    desktop.append_shared_message('assistant', 'Прогреваю вычисления.', message_id='pending', conversation_id='first', pending=True)
                    agent._render_shared_history(force=True)
                    assert desktop.read_chat_state()['activeConversationId'] == reading
                    agent.dialog_listbox.selection_clear(0, 'end')
                    agent.dialog_listbox.selection_set(agent.dialog_ids.index('first'))
                    agent._select_assistant_dialog()
                    state = desktop.read_chat_state()
                    assert state['activeConversationId'] == 'first'
                    text = agent.history_box.get('1.0', 'end')
                    assert 'Keep this history' in text and '02.10.2026' in text
                    assert 'Прогреваю вычисления.' in text
                    desktop.append_shared_message('assistant', 'Done', message_id='pending', conversation_id='first', pending=False, phase='done')
                    agent._finish_dialog_switch()
                    agent.chat.update_idletasks()
                    agent.history_box.yview_moveto(0)
                    before = agent.history_box.index('@0,0')
                    desktop.append_shared_message('assistant', 'New reply', conversation_id='first')
                    agent._render_shared_history(force=True)
                    assert agent.history_box.index('@0,0') == before
                    agent._toggle_dialog_sidebar()
                    assert not agent.dialog_sidebar_open and not agent.dialog_sidebar.winfo_manager()
                    agent._show_context_dialog()
                    agent.context_window.withdraw()
                    assert agent.context_window.title() == 'Контекст и длина ответа'
                    agent.context_window.destroy()
                    # '+-x' specifies absolute negative coordinates to Tk.
                    agent.root.geometry('180x220+-300+-100')
                    agent.root.update_idletasks()
                    assert agent.root.winfo_rootx() == -300 and agent.root.winfo_rooty() == -100
                    results.update(passed=True, created_dialog=True, selected_existing=True,
                                   history_preserved=True, timestamps_visible=True, menu_hidden=True, manual_scroll_preserved=True,
                                   responsive_during_bootstrap=True, drag_during_load=True, browse_during_load=True,
                                   background_reply_preserves_view=True, context_menu=True, negative_monitor_coordinates=True)
                except BaseException as error:
                    errors.append(error)
                finally:
                    release_bootstrap.set()
                    agent.root.destroy()

            def hidden_root(*args, **kwargs):
                root = original_tk(*args, **kwargs)
                root.withdraw()
                root.after(100, exercise)
                return root

            with patch.object(tk, 'Tk', side_effect=hidden_root):
                agent._run()
    if errors:
        raise errors[0]
    print(json.dumps(results))


if __name__ == '__main__':
    main()

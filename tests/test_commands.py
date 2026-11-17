"""Native composer menu configuration follows Bot API contracts."""
import unittest


class CommandsTests(unittest.TestCase):
    def test_registers_and_reads_back_default_menu(self):
        from bot.commands import configure_menu, command_list
        calls = []
        def api(token, method, payload):
            calls.append((method, payload))
            if method == 'getMyCommands':
                return command_list('id')
            if method == 'getChatMenuButton':
                return {'type': 'commands'}
            return True
        configure_menu('test', api)
        self.assertIn(('setChatMenuButton', {'menu_button': {'type': 'commands'}}), calls)
        self.assertIn(('getChatMenuButton', {}), calls)
        commands = next(p['commands'] for m, p in calls if m == 'setMyCommands')
        self.assertIn('language', [c['command'] for c in commands])

    def test_chat_selection_overrides_all_client_languages(self):
        from bot.commands import configure_menu, command_list
        calls = []
        def api(token, method, payload):
            calls.append((method, payload))
            if method == 'getMyCommands':
                return command_list('en')
            if method == 'getChatMenuButton':
                return {'type': 'commands'}
            return True
        configure_menu('test', api, chat=42, code='en', private=True)
        writes = [p for m, p in calls if m == 'setMyCommands']
        self.assertEqual({p['language_code'] for p in writes}, {'', 'en', 'id'})
        self.assertTrue(all(p['scope'] == {'type': 'chat', 'chat_id': 42} for p in writes))
        self.assertIn(('setChatMenuButton', {'chat_id': 42, 'menu_button': {'type': 'commands'}}), calls)

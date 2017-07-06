"""
Unittests for the SublimeTerminalBuffer module
"""
import unittest

# Import sublime stub
import sublime

# Module to test
from TerminalView import sublime_terminal_buffer


# still some stuff todo with this testcase - lacks color tests and more edge
# cases
class line_updates(unittest.TestCase):
    def setUp(self):
        self._test_view = sublime.SublimeViewStub(1)
        self._test_view.terminal_view_scroll = None
        self._test_view.terminal_view_color_regions = {}
        self._test_view.terminal_view_buffer_contents = {}
        self._sublime_cmd = sublime_terminal_buffer.TerminalViewUpdate(self._test_view)

        # We assume the view is 5 lines and 11 chars wide
        self._expected_buffer_contents = []
        for i in range(5):
            self._expected_buffer_contents.append("           \n")

        # Update lines 0 to 5 with blanks as we should under normal operation
        lines = {}
        for i in range(5):
            lines[i] = " " * 11
        self._sublime_cmd._update_lines(None, lines, {})

        # Check that local copy of buffer is correct
        buffer_cache = self._test_view.terminal_view_buffer_contents
        for i in range(5):
            self.assertEqual(buffer_cache[i], self._expected_buffer_contents[i])

        # Check that replace calls are done correctly
        replaces = self._test_view.get_replace_calls()
        for i in range(5):
            self.assertEqual(replaces[i].region.a, i * 12)
            self.assertEqual(replaces[i].region.b, i * 12)
            self.assertEqual(replaces[i].content, self._expected_buffer_contents[i])
        self._test_view.clear_replace_calls()

    def test_line_insert(self):
        # Update lines 1 and 3 with new content
        lines = {
            0: "test line 1",
            2: "line 2     ",
        }

        self._expected_buffer_contents[0] = "test line 1\n"
        self._expected_buffer_contents[2] = "line 2     \n"
        self._sublime_cmd._update_lines(None, lines, {})

        # Check that local copy of buffer is correct
        buffer_cache = self._test_view.terminal_view_buffer_contents
        for i in range(5):
            self.assertEqual(buffer_cache[i], self._expected_buffer_contents[i])

        # Check that replace calls are done correctly
        replaces = self._test_view.get_replace_calls()
        self.assertEqual(replaces[0].region.a, 0)
        self.assertEqual(replaces[0].region.b, 12)
        self.assertEqual(replaces[0].content, self._expected_buffer_contents[0])
        self.assertEqual(replaces[1].region.a, 24)
        self.assertEqual(replaces[1].region.b, 36)
        self.assertEqual(replaces[1].content, self._expected_buffer_contents[2])
        self._test_view.clear_replace_calls()


class terminal_buffer(unittest.TestCase):
    def test_view_size(self):
        # Set up test view
        test_view = sublime.SublimeViewStub(3)
        test_view.set_viewport_extent((300, 200))
        test_view.set_line_height(10)
        test_view.set_em_width(5)

        # Make test buffer
        buf = sublime_terminal_buffer.SublimeTerminalBuffer(test_view, "sometitle", None)

        rows, cols = buf.view_size()
        self.assertEqual(rows, 20)
        self.assertEqual(cols, 59)  # Note buffer logic subtracts 1

    def test_keypress_callback(self):
        # Set up test view
        test_view = sublime.SublimeViewStub(1337)

        # Make test buffer
        buf = sublime_terminal_buffer.SublimeTerminalBuffer(test_view, "test", None)

        # Define test callback function
        expected_key = None
        expected_ctrl = False
        expected_alt = False

        def keypress_cb(key, ctrl=False, alt=False, shift=False, meta=False):
            self.assertEqual(key, expected_key)
            self.assertEqual(ctrl, expected_ctrl)
            self.assertEqual(alt, expected_alt)

        # Set callback
        buf.set_keypress_callback(keypress_cb)

        # Make the textcommand manually and execute it
        keypress_cmd = sublime_terminal_buffer.TerminalViewKeypress(test_view)
        expected_key = "dummy_key"
        keypress_cmd.run(None, key=expected_key)

        # Now with modifiers
        expected_key = "a"
        expected_alt = True
        keypress_cmd.run(None, key=expected_key, alt=expected_alt)

        expected_key = "a"
        expected_alt = False
        expected_ctrl = True
        keypress_cmd.run(None, key=expected_key, ctrl=expected_ctrl)

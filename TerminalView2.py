import os
import threading
import time
import collections
import weakref
import sublime
import sublime_plugin

from .terminal_emulator       import PyteTerminalEmulator
from .linux_pty               import LinuxPty
from .sublime_terminal_buffer import set_color_scheme
from .utils                   import ConsoleLogger


class TerminalView2(sublime_plugin.ViewEventListener, ConsoleLogger):

    ##############
    # Public API #
    ##############

    @classmethod
    def from_id(cls, id):
        """Retrieve a TerminalView2 instance from a sublime.View ID."""
        return cls._instances.get(id, None)

    @classmethod
    def from_view(cls, view):
        """Retrieve a TerminalView2 instance from a sublime.View object."""
        return cls.from_id(view.id())

    @classmethod
    def get_active_instance(cls):
        """Retrieve the instance that currently has focus, if any."""
        if cls._active_instance:
            return cls._active_instance()
        else:
            return None

    ########################################################
    # From here on out it's all private. Don't touch this! #
    ########################################################

    @classmethod
    def is_applicable(cls, settings):
        return settings.get("_terminal_view", False)

    _instances = weakref.WeakValueDictionary()
    _active_instance = None

    def __init__(self, view):
        sublime_plugin.ViewEventListener.__init__(self, view)
        ConsoleLogger.__init__(self)
        # super(TerminalView2, self).__init__(*args, **kwargs)

        self._cmd = self.view.settings().get("_terminal_view_cmd", "")
        self._cwd = self.view.settings().get("_terminal_view_cwd", "")

        self.view.set_scratch(True)
        self.view.set_read_only(True)
        self.view.settings().set("gutter", False)
        self.view.settings().set("highlight_line", False)
        self.view.settings().set("auto_complete_commit_on_tab", False)
        self.view.settings().set("draw_centered", False)
        self.view.settings().set("word_wrap", False)
        self.view.settings().set("auto_complete", False)
        self.view.settings().set("draw_white_space", "none")
        self.view.settings().set("draw_indent_guides", False)
        self.view.settings().set("caret_style", "blink")
        self.view.settings().set("scroll_past_end", False)
        self.view.settings().add_on_change(
            "color_scheme",
            lambda: set_color_scheme(self.view))

        settings = sublime.load_settings("TerminalView.sublime-settings")
        self.show_colors = settings.get("terminal_view_show_colors", False)

        # Get configured margins
        self._right_margin = settings.get("terminal_view_right_margin", 3)
        self._bottom_margin = settings.get("terminal_view_bottom_margin", 0)

        # Flag to request scrolling in view (from one thread to another)
        self._scroll = None

        # Save a dict on the view to store color regions for each line
        self._color_regions = {}

        # Save keypress callback for this view
        self._keypress_callback = None

        # Keep track of the content in the buffer (having a local copy is a lot
        # faster than using the ST3 API to get the contents)
        self._buffer_contents = {}

        self._last_update = 0

        # Use pyte as underlying terminal emulator
        hist = settings.get("terminal_view_scroll_history", 1000)
        ratio = settings.get("terminal_view_scroll_ratio", 0.5)
        self._emulator = PyteTerminalEmulator(80, 24, hist, ratio)

        self._terminal_buffer_is_open = True
        self._rows = 0
        self._cols = 0

        # Start the underlying shell
        try:
            self._shell = LinuxPty(self._cmd.split(), self._cwd)
            self._shell_is_running = True
        except OSError as e:
            sublime.error_message(str(e))
            self._shell_is_running = False

        # Upon deletion of this object it'll be removed from the _instances
        # dictionary automatically because of weak reference semantics.
        self.__class__._instances[self.view.id()] = self

        # Start the main loop
        self.update_thread = threading.Thread(
            target=self.__class__._main_update_loop,
            args=(weakref.ref(self),))
        self.update_thread.start()

    def __del__(self):
        self._stop()  # Stop if we didn't stop yet.
        try:
            self.update_thread.join()  # Wait for the update thread to stop.
        except RuntimeError:
            pass  # already stopped
        self.view.settings().set("_terminal_view", False)
        self.log("goodbye from the main thread")

    def on_activated(self):
        self.__class__._active_instance = weakref.ref(self)

    def on_deactivated(self):
        self.__class__._active_instance = None

    def terminal_view_keypress_callback(
            self,
            key,
            ctrl=False,
            alt=False,
            shift=False,
            meta=False):
        """
        Callback when a keypress is registered in the Sublime Terminal buffer.

        Args:
            key (str): String describing pressed key. May be a name like
                       'home'.
            ctrl (boolean, optional)
            alt (boolean, optional)
            shift (boolean, optional)
            meta (boolean, optional)
        """
        self._shell.send_keypress(key, ctrl, alt, shift, meta)

    def _main_update_loop(weakself):
        """
        This is the main update function. It attempts to run at a certain number
        of frames per second, and keeps input and output synchronized.
        """
        # 30 frames per second should be responsive enough
        ideal_delta = 1.0 / 30.0
        current = time.time()
        while True:
            # increment the reference count
            self = weakself()
            if not self or not self._shell_is_running:
                break
            self._poll_shell_output()
            success = self.update_view()
            if not success:
                # Leave view open as we should only get an update if we are
                # reloading the plugin
                self._stop(close_view=False)
                break

            self._resize_screen_if_needed()
            if not self.view.is_valid() or not self._shell.is_running():
                self._stop()
                break

            previous = current
            current = time.time()
            actual_delta = current - previous
            time_left = ideal_delta - actual_delta
            if time_left > 0.0:
                del self  # decrement the reference count during sleep
                time.sleep(time_left)
        print("goodbye from the update thread "
              "(note i can't use self anymore reliably)")

    def insert_data(self, data):
        start = time.time()
        self._emulator.feed(data)
        t = time.time() - start
        self.log("Updated terminal emulator in %.3f ms" % (t * 1000.))

    def _poll_shell_output(self):
        """
        Poll the output of the shell
        """
        max_read_size = 4096
        data = self._shell.receive_output(max_read_size)
        if data is not None:
            self.log("Got %u bytes of data from shell" % (len(data), ))
            self.insert_data(data)

    def _resize_screen_if_needed(self):
        """
        Check if the terminal view was resized. If so update the screen size of
        the terminal and notify the shell.
        """
        rows, cols = self.view_size()
        row_diff = abs(self._rows - rows)
        col_diff = abs(self._cols - cols)

        if row_diff or col_diff:
            log = "Changing screen size from (%i, %i) to (%i, %i)" % \
                  (self._rows, self._cols, rows, cols)
            self.log(log)

            self._rows = rows
            self._cols = cols
            self._shell.update_screen_size(self._rows, self._cols)
            self._emulator.resize(self._rows, self._cols)

    def _stop(self, close_view=True):
        """
        Stop the terminal and close everything down.
        """
        # if self._terminal_buffer_is_open and close_view:
        #     self._terminal_buffer.close()
        #     self._terminal_buffer_is_open = False

        if self._shell_is_running:
            self._shell.stop()
            self._shell_is_running = False

    def view_size(self):
        pixel_width, pixel_height = self.view.viewport_extent()
        pixel_per_line = self.view.line_height()
        pixel_per_char = self.view.em_width()

        if pixel_per_line == 0 or pixel_per_char == 0:
            return (0, 0)

        # Subtract one to avoid any wrapping issues
        nb_columns = int(pixel_width / pixel_per_char) - self._right_margin
        if nb_columns < 1:
            nb_columns = 1

        nb_rows = int(pixel_height / pixel_per_line) - self._bottom_margin
        if nb_rows < 1:
            nb_rows = 1

        return (nb_rows, nb_columns)

    def update_view(self):
        last_update = self._last_update
        # When reloading the plugin the view sometimes becomes completely
        # invalid as seen from text commands
        # if not hasattr(self.view, "terminal_view_emulator"):
        #     return

        # Check if scroll was requested
        self._update_scrolling()

        # Update dirty lines in buffer if there are any
        dirty_lines = self._emulator.dirty_lines()
        if len(dirty_lines) > 0:
            # Reset viewport when data is inserted
            self._update_viewport_position()

            # Invalidate the last cursor position when dirty lines are updated
            self.view.settings().set("terminal_view_last_cursor_pos", None)

            # Generate color map
            color_map = {}
            if self.show_colors:
                start = time.time()
                color_map = self._emulator.color_map(dirty_lines.keys())
                t = time.time() - start
                self.log("Generated color map in %.3f ms" % (t * 1000.))

            # Update the view
            start = time.time()
            self._update_lines(dirty_lines, color_map)
            self._emulator.clear_dirty()
            t = time.time() - start
            self.log("Updated ST3 view in %.3f ms" % (t * 1000.))

        # Update cursor last to avoid a selection blinking at the top of the
        # terminal when starting or when a new prompt is being drawn at the
        # bottom
        self._update_cursor()

        self._last_update = time.time()
        if self._last_update == last_update:
            return False
        return True

    def _update_viewport_position(self):
        self.view.set_viewport_position((0, 0), animate=False)

    def _update_scrolling(self):
        if self._scroll is not None:
            index = self._scroll[0]
            direction = self._scroll[1]
            if index == "line":
                if direction == "up":
                    self._emulator.prev_line()
                else:
                    self._emulator.next_line()
            else:
                if direction == "up":
                    self._emulator.prev_page()
                else:
                    self._emulator.next_page()

            self._scroll = None

    def _update_cursor(self):
        cursor_pos = self._emulator.cursor()
        last_cursor_pos = self.view.settings().get(
            "terminal_view_last_cursor_pos")
        if (last_cursor_pos and
                last_cursor_pos[0] == cursor_pos[0] and
                last_cursor_pos[1] == cursor_pos[1]):
            return
        tp = self.view.text_point(cursor_pos[0], cursor_pos[1])
        self.view.sel().clear()
        self.view.sel().add(sublime.Region(tp, tp))
        self.view.settings().set("terminal_view_last_cursor_pos", cursor_pos)

    def _update_lines(self, dirty_lines, color_map):
        self.view.set_read_only(False)
        lines = dirty_lines.keys()
        for line_no in sorted(lines):
            # Clear any colors on the line
            self._remove_color_regions_on_line(line_no)

            # Update the line
            self._update_line_content(line_no, dirty_lines[line_no])

            # Apply colors to the line if there are any on it
            if line_no in color_map:
                self._update_line_colors(line_no, color_map[line_no])

        self.view.set_read_only(True)

    def _remove_color_regions_on_line(self, line_no):
        if line_no in self._color_regions:
            region_deque = self._color_regions[line_no]
            try:
                while True:
                    region = region_deque.popleft()
                    self.view.erase_regions(region)
            except IndexError:
                pass

    def _update_line_content(self, line_no, content):
        # Note this function has been optimized quite a bit. Calls to the ST3
        # API has been left out on purpose as they are slower than the
        # alternative.

        # Get start and end point of the line
        line_start, line_end = self._get_line_start_and_end_points(line_no)

        # Make region spanning entire line (including any newline at the end)
        # line_region = sublime.Region(line_start, line_end)

        if content is None:

            self.view.run_command(
                "terminal_view_erase",
                {"region_start": line_start, "region_end": line_end})

            # self.view.erase(edit, line_region)
            if line_no in self._buffer_contents:
                del self._buffer_contents[line_no]
        else:
            # Replace content on the line with new content
            content_w_newline = content + "\n"

            self.view.run_command(
                "terminal_view_replace",
                {"region_start": line_start,
                 "region_end": line_end,
                 "content": content_w_newline})

            # self.view.replace(edit, line_region, content_w_newline)

            # Update our local copy of the ST3 view buffer
            self._buffer_contents[line_no] = content_w_newline

    def _update_line_colors(self, line_no, line_color_map):
        # Note this function has been optimized quite a bit. Calls to the ST3
        # API has been left out on purpose as they are slower than the
        # alternative.

        for idx, field in line_color_map.items():
            length = field["field_length"]
            color_scope = "terminalview.%s_%s" % (field["color"][0],
                                                  field["color"][1])

            # Get text point where color should start
            line_start, _ = self._get_line_start_and_end_points(line_no)
            color_start = line_start + idx

            # Make region that should be colored
            buffer_region = sublime.Region(color_start, color_start + length)
            region_key = "%i,%s" % (line_no, idx)

            # Add the region
            flags = sublime.DRAW_NO_OUTLINE | sublime.PERSISTENT

            self.view.add_regions(region_key,
                                  [buffer_region],
                                  color_scope,
                                  flags=flags)

            self._register_color_region(line_no, region_key)

    def _register_color_region(self, line_no, key):
        if line_no in self._color_regions:
            self._color_regions[line_no].appendleft(key)
        else:
            self._color_regions[line_no] = collections.deque()
            self._color_regions[line_no].appendleft(key)

    def _get_line_start_and_end_points(self, line_no):
        start_point = 0

        # Sum all lines leading up to the line we want the start point to
        for i in range(line_no):
            if i in self._buffer_contents:
                line_len = len(self._buffer_contents[i])
                start_point = start_point + line_len

        # Add length of line to the end_point
        end_point = start_point
        if line_no in self._buffer_contents:
            line_len = len(self._buffer_contents[line_no])
            end_point = end_point + line_len

        return (start_point, end_point)


class TerminalViewReplaceCommand(sublime_plugin.TextCommand):

    def run(self, edit, region_start, region_end, content):
        self.view.replace(edit,
                          sublime.Region(region_start, region_end), content)


class TerminalViewEraseCommand(sublime_plugin.TextCommand):

    def run(self, edit, region_start, region_end):
        self.view.erase(edit,
                        sublime.Region(region_start, region_end))


class TerminalViewKeypressCommand(sublime_plugin.TextCommand):
    def run(self, _, **kwargs):
        print("foo")
        if type(kwargs["key"]) is not str:
            sublime.error_message(
                "Terminal View: Got keypress with non-string key")
            return

        if "meta" in kwargs and kwargs["meta"]:
            sublime.error_message(
                "Terminal View: Meta key is not supported yet")
            return

        if "meta" not in kwargs:
            kwargs["meta"] = False
        if "alt" not in kwargs:
            kwargs["alt"] = False
        if "ctrl" not in kwargs:
            kwargs["ctrl"] = False
        if "shift" not in kwargs:
            kwargs["shift"] = False

        instance = TerminalView2.get_active_instance()
        if instance:
            instance.terminal_view_keypress_callback(
                kwargs["key"],
                kwargs["ctrl"],
                kwargs["alt"],
                kwargs["shift"],
                kwargs["meta"])

import os
import unittest


@unittest.skipUnless(os.name == "nt", "Windows compositor")
class WindowsPortraitTest(unittest.TestCase):
    def test_soft_pixels_clicks_and_native_window_lifetime(self):
        import ctypes
        import tkinter as tk
        from ctypes import wintypes
        from PIL import Image
        from workspace.app.windows_portrait import PortraitWindow

        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        events = []
        window = PortraitWindow(root, (154, 210), lambda name, xy: events.append(name))
        self.addCleanup(window.close)
        frame = Image.new("RGBA", window.size)
        frame.putpixel((20, 42), (220, 120, 40, 128))
        window.paint(frame, ("✓", "#25a957"))
        pixels = ctypes.string_at(window.bits, 154 * 210 * 4)
        offset = (42 * 154 + 20) * 4
        self.assertEqual(pixels[offset:offset+4], bytes([20, 60, 110, 128]))
        self.assertEqual(pixels[:4], bytes(4))
        badge_pixel = (29 * 154 + 128) * 4
        self.assertGreater(pixels[badge_pixel+3], 0)
        window.paint(frame)
        self.assertEqual(ctypes.string_at(window.bits, 154 * 210 * 4)[badge_pixel:badge_pixel+4], bytes(4))

        send = ctypes.windll.user32.SendMessageW
        send.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        send.restype = ctypes.c_ssize_t
        send(window.hwnd, 0x0201, 0, 0)
        send(window.hwnd, 0x0200, 1, 0)
        send(window.hwnd, 0x0202, 0, 0)
        send(window.hwnd, 0x0205, 0, 0)
        root.update()
        self.assertEqual(events, ["portrait_press", "portrait_move", "portrait_release", "portrait_menu"])
        window.close()
        self.assertIsNone(window.hwnd)
        self.assertIsNone(window.dc)

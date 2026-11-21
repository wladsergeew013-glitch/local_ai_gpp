"""Host the window buttons above WebView2 and give its header native drag regions."""
import ctypes
from ctypes import wintypes


def install_titlebar(window):
    # pywebview's before_show event runs on the WinForms UI thread.
    view = window.native.webview
    if not hasattr(view, "CoreWebView2InitializationCompleted"):
        return

    def ready(sender, event=None):
        if event is not None and not event.IsSuccess:
            return
        try:
            sender.CoreWebView2.Settings.IsNonClientRegionSupportEnabled = True
        except (AttributeError, OSError, NotImplementedError):
            return
        if _install_controls(window.native):
            window._workspace_titlebar = True

    if view.CoreWebView2:
        ready(view)
    else:
        view.CoreWebView2InitializationCompleted += ready


def _install_controls(form):
    from System.Drawing import Color, Font, FontStyle
    from System.Windows.Forms import Button, FlatStyle, FormWindowState

    hwnd = form.Handle.ToInt64()
    user = ctypes.WinDLL("user32", use_last_error=True)
    common = ctypes.WinDLL("comctl32", use_last_error=True)
    signature = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, *signature, ctypes.c_size_t, ctypes.c_size_t)
    common.DefSubclassProc.argtypes = signature
    common.DefSubclassProc.restype = ctypes.c_ssize_t
    common.SetWindowSubclass.argtypes = [wintypes.HWND, callback_type, ctypes.c_size_t, ctypes.c_size_t]
    common.RemoveWindowSubclass.argtypes = [wintypes.HWND, callback_type, ctypes.c_size_t]
    user.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
    user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user.IsZoomed.argtypes = [wintypes.HWND]
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.PostMessageW.argtypes = signature

    @callback_type
    def frame_proc(handle, message, wparam, lparam, subclass, data):
        if message == 0x0083 and wparam and not user.IsZoomed(handle):  # WM_NCCALCSIZE
            # Preserve the system's side and bottom resize frame. The first RECT
            # in NCCALCSIZE_PARAMS is the proposed window, then the client area.
            bounds = ctypes.cast(lparam, ctypes.POINTER(wintypes.RECT)).contents
            top = bounds.top
            common.DefSubclassProc(handle, message, wparam, lparam)
            bounds.top = top
            return 0
        hit = common.DefSubclassProc(handle, message, wparam, lparam)
        if message == 0x0084 and hit == 1 and not user.IsZoomed(handle):  # WM_NCHITTEST
            bounds = wintypes.RECT()
            user.GetWindowRect(handle, ctypes.byref(bounds))
            y = ctypes.c_int16(lparam >> 16).value
            if bounds.top <= y < bounds.top + 4:
                return 12  # HTTOP
        return hit

    if not common.SetWindowSubclass(hwnd, frame_proc, 1, 0):
        return False
    glyph = Font("Segoe MDL2 Assets", 9, FontStyle.Regular)
    buttons = []

    def command(button, _event):
        code = int(button.Tag)
        if code == 0xF030 and form.WindowState == FormWindowState.Maximized:
            code = 0xF120
        user.PostMessageW(hwnd, 0x0112, code, 0)  # WM_SYSCOMMAND preserves the normal close-to-companion path.

    def layout(*_):
        scale = form.DeviceDpi / 96
        width, height = round(34 * scale), round(30 * scale)
        first = form.ClientSize.Width - len(buttons) * width - round(8 * scale)
        maximized = form.WindowState == FormWindowState.Maximized
        buttons[1].Text = "\ue923" if maximized else "\ue922"
        buttons[1].AccessibleName = "Восстановить окно" if maximized else "Развернуть окно"
        for i, button in enumerate(buttons):
            button.SetBounds(first + i * width, round(15 * scale), width, height)

    for code, symbol, name in ((0xF020, "\ue921", "Свернуть окно"), (0xF030, "\ue922", "Развернуть окно"),
                               (0xF060, "\ue8bb", "Закрыть окно")):
        button = Button()
        button.Name, button.AccessibleName, button.Text = name, name, symbol
        button.Tag, button.Font = code, glyph
        button.FlatStyle = FlatStyle.Flat
        button.UseVisualStyleBackColor = False
        button.BackColor = Color.White
        button.ForeColor = Color.FromArgb(61, 78, 103)
        button.FlatAppearance.BorderSize = 0
        button.FlatAppearance.MouseOverBackColor = Color.MistyRose if code == 0xF060 else Color.FromArgb(230, 238, 249)
        button.FlatAppearance.MouseDownBackColor = Color.LightCoral if code == 0xF060 else Color.FromArgb(210, 225, 247)
        button.TabStop = False
        button.Click += command
        form.Controls.Add(button)
        button.BringToFront()
        buttons.append(button)

    def closed(*_):
        common.RemoveWindowSubclass(hwnd, frame_proc, 1)
        glyph.Dispose()

    form.FormClosed += closed  # keeps the native callback alive for the lifetime of the HWND
    form.Resize += layout
    form.DpiChanged += layout
    layout()
    style = user.GetWindowLongW(hwnd, -16)
    user.SetWindowLongW(hwnd, -16, style & ~0x00C00000)  # remove WS_CAPTION, retain the system sizing styles
    user.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x37)  # FRAMECHANGED, preserve size, position and activation
    return True

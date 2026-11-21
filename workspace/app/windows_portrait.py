"""Per-pixel Windows desktop portrait. The Tk owner retains the interactive speech panel."""

import ctypes as c
from ctypes import wintypes as w
from functools import cache

from PIL import Image, ImageDraw, ImageFont


class BitmapHeader(c.Structure):
    _fields_ = [("size", w.DWORD), ("width", w.LONG), ("height", w.LONG),
                ("planes", w.WORD), ("depth", w.WORD), ("compression", w.DWORD),
                ("bytes", w.DWORD), ("xppm", w.LONG), ("yppm", w.LONG),
                ("colors", w.DWORD), ("important", w.DWORD)]


class Blend(c.Structure):
    _fields_ = [("operation", w.BYTE), ("flags", w.BYTE), ("opacity", w.BYTE), ("format", w.BYTE)]


def function(lib, name, result, *args):
    fn = getattr(lib, name)
    fn.restype, fn.argtypes = result, args
    return fn


user = c.WinDLL("user32", use_last_error=True)
gdi = c.WinDLL("gdi32", use_last_error=True)
controls = c.WinDLL("comctl32", use_last_error=True)
callback_type = c.WINFUNCTYPE(c.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM, c.c_size_t, c.c_size_t)
ancestor = function(user, "GetAncestor", w.HWND, w.HWND, w.UINT)
create_window = function(user, "CreateWindowExW", w.HWND, w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
                         c.c_int, c.c_int, c.c_int, c.c_int, w.HWND, w.HMENU, w.HINSTANCE, c.c_void_p)
destroy = function(user, "DestroyWindow", w.BOOL, w.HWND)
show = function(user, "ShowWindow", w.BOOL, w.HWND, c.c_int)
set_pos = function(user, "SetWindowPos", w.BOOL, w.HWND, w.HWND, c.c_int, c.c_int, c.c_int, c.c_int, w.UINT)
update = function(user, "UpdateLayeredWindow", w.BOOL, w.HWND, w.HDC, c.POINTER(w.POINT), c.POINTER(w.SIZE),
                  w.HDC, c.POINTER(w.POINT), w.DWORD, c.POINTER(Blend), w.DWORD)
create_dc = function(gdi, "CreateCompatibleDC", w.HDC, w.HDC)
create_dib = function(gdi, "CreateDIBSection", w.HBITMAP, w.HDC, c.POINTER(BitmapHeader), w.UINT, c.POINTER(c.c_void_p), w.HANDLE, w.DWORD)
select = function(gdi, "SelectObject", w.HGDIOBJ, w.HDC, w.HGDIOBJ)
delete_object = function(gdi, "DeleteObject", w.BOOL, w.HGDIOBJ)
delete_dc = function(gdi, "DeleteDC", w.BOOL, w.HDC)
subclass = function(controls, "SetWindowSubclass", w.BOOL, w.HWND, callback_type, c.c_size_t, c.c_size_t)
unsubclass = function(controls, "RemoveWindowSubclass", w.BOOL, w.HWND, callback_type, c.c_size_t)
default_proc = function(controls, "DefSubclassProc", c.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
client_to_screen = function(user, "ClientToScreen", w.BOOL, w.HWND, c.POINTER(w.POINT))
capture = function(user, "SetCapture", w.HWND, w.HWND)
get_capture = function(user, "GetCapture", w.HWND)
release = function(user, "ReleaseCapture", w.BOOL)


@cache
def status_badge(glyph, color):
    scale = 3
    size = 28
    image = Image.new("RGBA", (size * scale, size * scale))
    draw = ImageDraw.Draw(image)
    draw.ellipse((1, 1, size * scale - 2, size * scale - 2), fill=color, outline="#f5faff", width=2 * scale)
    if glyph == "✓":
        draw.line([(7*scale, 14*scale), (12*scale, 19*scale), (21*scale, 8*scale)], fill="white", width=3*scale)
    else:
        try:
            font = ImageFont.truetype("segoeuib.ttf", 19 * scale)
        except OSError:
            font = ImageFont.load_default(size=19 * scale)
        draw.text((size*scale//2, size*scale//2), glyph, font=font, fill="white", anchor="mm")
    return image.resize((size, size), Image.Resampling.LANCZOS)


class PortraitWindow:
    """One owned, non-activating popup. Windows hit-tests the same alpha that it displays."""

    def __init__(self, root, size, on_input):
        self.root, self.size = root, size
        self.hwnd = self.dc = self.dib = self.previous = None
        self.bits = c.c_void_p()
        self.xy = None
        self.visible = self.painted = False
        self.pressed = False

        def emit(hwnd, name, position):
            point = w.POINT(c.c_short(position & 0xffff).value, c.c_short((position >> 16) & 0xffff).value)
            if client_to_screen(hwnd, c.byref(point)):
                on_input(name, (point.x, point.y))

        @callback_type
        def messages(hwnd, message, wp, lp, ident, data):
            # Tk's message pump is in native code here. Queue input; entering Tcl
            # again from this callback can abort the desktop process.
            if message == 0x0201:  # WM_LBUTTONDOWN
                self.pressed = True
                capture(hwnd)
                emit(hwnd, "portrait_press", lp)
                return 0
            if message == 0x0200 and self.pressed and wp & 1:  # WM_MOUSEMOVE with MK_LBUTTON
                emit(hwnd, "portrait_move", lp)
                return 0
            if message == 0x0202 and self.pressed:  # WM_LBUTTONUP
                self.pressed = False
                emit(hwnd, "portrait_release", lp)
                if get_capture() == hwnd:
                    release()
                return 0
            if message == 0x0215 and self.pressed:  # WM_CAPTURECHANGED
                self.pressed = False
                on_input("portrait_cancel", None)
                return 0
            if message == 0x0205:  # WM_RBUTTONUP
                emit(hwnd, "portrait_menu", lp)
                return 0
            if message == 0x0021:  # WM_MOUSEACTIVATE
                return 3  # MA_NOACTIVATE
            return default_proc(hwnd, message, wp, lp)
        self.messages = messages
        try:
            owner = ancestor(root.winfo_id(), 2)
            # WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE; WS_POPUP | SS_NOTIFY
            self.hwnd = create_window(0x08080080, "STATIC", "Local AI Workspace Portrait", 0x80000100,
                                      0, 0, *size, owner, None, None, None)
            if not self.hwnd or not subclass(self.hwnd, messages, 1, 0):
                raise c.WinError(c.get_last_error())
            self.dc = create_dc(None)
            header = BitmapHeader(c.sizeof(BitmapHeader), size[0], -size[1], 1, 32)
            if self.dc:
                self.dib = create_dib(self.dc, c.byref(header), 0, c.byref(self.bits), None, 0)
            if not self.dib or not self.bits.value:
                raise c.WinError(c.get_last_error())
            self.previous = select(self.dc, self.dib)
            if not self.previous:
                raise c.WinError(c.get_last_error())
        except BaseException:
            self.close()
            raise

    def paint(self, frame, badge=None):
        width, height = self.size
        canvas = Image.new("RGBA", self.size)
        canvas.paste(frame, ((width-frame.width)//2, (height-frame.height)//2))
        if badge:
            canvas.alpha_composite(status_badge(*badge), (width-40, 15))
        pixels = canvas.convert("RGBa").tobytes("raw", "BGRa")
        c.memmove(self.bits, pixels, len(pixels))
        point, size, origin = w.POINT(*(self.xy or (0, 0))), w.SIZE(width, height), w.POINT()
        blend = Blend(0, 0, 255, 1)  # AC_SRC_OVER, AC_SRC_ALPHA
        if not update(self.hwnd, None, c.byref(point), c.byref(size), self.dc,
                      c.byref(origin), 0, c.byref(blend), 2):  # ULW_ALPHA
            raise c.WinError(c.get_last_error())
        self.painted = True

    def sync(self, xy, mapped):
        if xy != self.xy:
            self.xy = xy
            # Keep input focus in the chat editor while moving the portrait.
            if not set_pos(self.hwnd, None, *xy, 0, 0, 0x15):  # NOSIZE | NOZORDER | NOACTIVATE
                raise c.WinError(c.get_last_error())
        visible = bool(mapped and self.painted)
        if self.visible != visible:
            show(self.hwnd, 8 if visible else 0)  # SW_SHOWNA / SW_HIDE
            self.visible = visible

    def close(self):
        if self.hwnd:
            unsubclass(self.hwnd, self.messages, 1)
            destroy(self.hwnd)
            self.hwnd = None
        if self.previous:
            select(self.dc, self.previous)
            self.previous = None
        if self.dib:
            delete_object(self.dib)
            self.dib = None
        if self.dc:
            delete_dc(self.dc)
            self.dc = None

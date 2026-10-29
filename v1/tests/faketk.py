"""A tiny fake of tkinter so the UI code can be imported and exercised in an environment without a display.
It records what was configured and keeps Treeview rows, so tests can assert on what the user would see."""
import sys
import types


class Widget:
    _count = 0

    def __init__(self, *args, **kwargs):
        Widget._count += 1
        self.opts = dict(kwargs)
        self.children = []
        self.parent = args[0] if args and isinstance(args[0], Widget) else None
        self.bindings = {}
        self.menu_items = []
        if self.parent is not None:
            self.parent.children.append(self)

    # generic: any other method is a harmless no-op returning self
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *a, **k: self

    def configure(self, *a, **kw):          # ttk.Style.configure(style_name, **opts) takes a positional name
        self.opts.update(kw)

    config = configure

    def cget(self, k):
        return self.opts.get(k)

    def __setitem__(self, k, v):
        self.opts[k] = v

    def __getitem__(self, k):
        if k == "height" and "height" not in self.opts:
            return 28
        return self.opts.get(k)

    def bind(self, seq, fn):
        self.bindings[seq] = fn

    def after(self, delay, callback=None, *args):
        # Execute tiny idle-yield callbacks immediately in headless tests. Longer
        # recurring timers (e.g. the app's 150 ms queue poll) remain manually driven.
        if callback is not None and delay <= 1:
            callback(*args)
        return object()

    def after_cancel(self, _token):
        pass

    def winfo_width(self):
        return 400

    def winfo_children(self):
        return list(self.children)

    def destroy(self):
        if self.parent is not None and self in self.parent.children:
            self.parent.children.remove(self)

    # Menu
    def add_command(self, **kw):
        self.menu_items.append(("command", kw))

    def add_checkbutton(self, **kw):
        self.menu_items.append(("check", kw))

    def add_separator(self, **kw):
        pass

    def add_cascade(self, **kw):
        pass

    def pack(self, *a, **k):
        pass

    def grid(self, *a, **k):
        pass

    def state(self, flags):
        self.opts["_state"] = flags

    # Notebook
    def add(self, child, **kw):
        self.children.append(child)

    def select(self, *a):
        if a:
            self.opts["_selected"] = a[0]
        return self.opts.get("_selected")

    def index(self, tab):
        return tab

    # Canvas
    def create_rectangle(self, *a, **k):
        return 1

    create_line = create_oval = create_polygon = create_text = create_image = create_rectangle

    def delete(self, *a):
        pass


class Tree(Widget):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.rows = {}
        self.order = []
        self.sel = []
        self.headings = {}

    def heading(self, key, text=None, **k):
        self.headings[key] = text

    def column(self, *a, **k):
        pass

    def tag_configure(self, *a, **k):
        pass

    def insert(self, parent, index, iid=None, values=(), tags=()):
        self.rows[iid] = {"values": tuple(values), "tags": tuple(tags)}
        self.order.append(iid)
        return iid

    def get_children(self, *a):
        return tuple(self.order)

    def item(self, iid, **kw):
        if "values" in kw:
            self.rows[iid]["values"] = tuple(kw["values"])
        if "tags" in kw:
            self.rows[iid]["tags"] = tuple(kw["tags"])
        return self.rows[iid]

    def delete(self, *iids):
        for i in iids:
            self.rows.pop(i, None)
            if i in self.order:
                self.order.remove(i)

    def selection(self):
        return tuple(self.sel)

    def selection_set(self, items):
        self.sel = list(items if isinstance(items, (list, tuple)) else [items])

    def selection_remove(self, *items):
        self.sel = [s for s in self.sel if s not in items]


class Var:
    def __init__(self, master=None, value=None, **k):
        self.v = value

    def get(self):
        return self.v

    def set(self, v):
        self.v = v


class Menubutton(Widget):
    pass


def install():
    tk = types.ModuleType("tkinter")
    ttk = types.ModuleType("tkinter.ttk")
    fd = types.ModuleType("tkinter.filedialog")
    mb = types.ModuleType("tkinter.messagebox")
    tk.__path__ = []
    for name in ("Tk", "Frame", "Label", "Canvas", "Toplevel", "Menu", "Scrollbar", "PhotoImage", "Button", "Entry"):
        setattr(tk, name, type(name, (Widget,), {}))
    tk.StringVar = tk.BooleanVar = tk.IntVar = tk.DoubleVar = Var
    tk.TclError = type("TclError", (Exception,), {})
    tk.END = "end"
    for name in ("Frame", "Label", "Button", "Checkbutton", "Radiobutton", "Notebook", "Combobox", "Entry",
                 "Style", "Scrollbar", "Separator", "Progressbar", "Scale"):
        setattr(ttk, name, type(name, (Widget,), {}))
    ttk.Treeview = Tree
    ttk.Menubutton = Menubutton
    fd.asksaveasfilename = lambda **k: ""
    fd.askopenfilename = lambda **k: ""
    mb.showinfo = mb.showerror = mb.askyesno = lambda *a, **k: None
    tk.ttk, tk.filedialog, tk.messagebox = ttk, fd, mb
    sys.modules.update({"tkinter": tk, "tkinter.ttk": ttk, "tkinter.filedialog": fd, "tkinter.messagebox": mb})
    return tk

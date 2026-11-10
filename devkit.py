"""New Flipper app projects from templates (FAP in C, or a JavaScript app).

Every C template is a complete ufbt project (application.fam, source, 10x10 icon) that Workbench
builds, installs and rebuilds on save like any other local folder.
"""

import re
from pathlib import Path

CATEGORIES = ["Tools", "Games", "GPIO", "Media", "Misc", "Infrared", "NFC", "RFID", "Sub-GHz", "Bluetooth", "USB", "iButton"]

FAM = '''App(
    appid="{appid}",
    name="{name}",
    apptype=FlipperAppType.EXTERNAL,
    entry_point="{appid}_app",
    stack_size=2 * 1024,
    fap_category="{category}",
    fap_version="0.1",
    fap_icon="icon.png",
    fap_description="{name}, made with Flipper Workbench.",
)
'''

HELLO = r'''// {name}: a minimal app. Draws text, counts OK presses, Back exits.
#include <furi.h>
#include <gui/gui.h>
#include <input/input.h>

typedef struct {{
    int presses;
}} State;

static void draw_cb(Canvas* canvas, void* ctx) {{
    State* s = ctx;
    canvas_clear(canvas);
    canvas_set_font(canvas, FontPrimary);
    canvas_draw_str_aligned(canvas, 64, 14, AlignCenter, AlignCenter, "{name}");
    canvas_set_font(canvas, FontSecondary);
    char line[32];
    snprintf(line, sizeof(line), "OK pressed %d times", s->presses);
    canvas_draw_str_aligned(canvas, 64, 36, AlignCenter, AlignCenter, line);
    canvas_draw_str_aligned(canvas, 64, 56, AlignCenter, AlignCenter, "Back to exit");
}}

static void input_cb(InputEvent* event, void* ctx) {{
    furi_message_queue_put(ctx, event, FuriWaitForever);
}}

int32_t {appid}_app(void* p) {{
    UNUSED(p);
    State* s = malloc(sizeof(State));
    s->presses = 0;
    FuriMessageQueue* queue = furi_message_queue_alloc(8, sizeof(InputEvent));
    ViewPort* view_port = view_port_alloc();
    view_port_draw_callback_set(view_port, draw_cb, s);
    view_port_input_callback_set(view_port, input_cb, queue);
    Gui* gui = furi_record_open(RECORD_GUI);
    gui_add_view_port(gui, view_port, GuiLayerFullscreen);
    FURI_LOG_I("{appid}", "started");

    InputEvent event;
    while(furi_message_queue_get(queue, &event, FuriWaitForever) == FuriStatusOk) {{
        if(event.type != InputTypeShort) continue;
        if(event.key == InputKeyBack) break;
        if(event.key == InputKeyOk) {{
            s->presses++;
            FURI_LOG_D("{appid}", "OK pressed %d times", s->presses);
            view_port_update(view_port);
        }}
    }}

    gui_remove_view_port(gui, view_port);
    view_port_free(view_port);
    furi_message_queue_free(queue);
    furi_record_close(RECORD_GUI);
    free(s);
    return 0;
}}
'''

MENU = r'''// {name}: a menu app (Submenu + Widget in a ViewDispatcher). Back goes up, then exits.
#include <furi.h>
#include <gui/gui.h>
#include <gui/view_dispatcher.h>
#include <gui/modules/submenu.h>
#include <gui/modules/widget.h>

typedef enum {{ ViewMenu, ViewInfo }} ViewId;

typedef struct {{
    ViewDispatcher* dispatcher;
    Submenu* menu;
    Widget* info;
}} App;

static void show_info(App* app, const char* text) {{
    widget_reset(app->info);
    widget_add_string_multiline_element(app->info, 64, 32, AlignCenter, AlignCenter, FontSecondary, text);
    view_dispatcher_switch_to_view(app->dispatcher, ViewInfo);
}}

static void menu_cb(void* ctx, uint32_t index) {{
    App* app = ctx;
    FURI_LOG_I("{appid}", "picked item %lu", index);
    show_info(app, index == 0 ? "Hello from\n{name}!" : "Made with\nFlipper Workbench");
}}

static uint32_t back_to_menu(void* ctx) {{
    UNUSED(ctx);
    return ViewMenu;
}}

static uint32_t exit_app(void* ctx) {{
    UNUSED(ctx);
    return VIEW_NONE;
}}

int32_t {appid}_app(void* p) {{
    UNUSED(p);
    App* app = malloc(sizeof(App));
    app->dispatcher = view_dispatcher_alloc();
    app->menu = submenu_alloc();
    app->info = widget_alloc();
    submenu_add_item(app->menu, "Say hello", 0, menu_cb, app);
    submenu_add_item(app->menu, "About", 1, menu_cb, app);
    view_set_previous_callback(submenu_get_view(app->menu), exit_app);
    view_set_previous_callback(widget_get_view(app->info), back_to_menu);
    view_dispatcher_add_view(app->dispatcher, ViewMenu, submenu_get_view(app->menu));
    view_dispatcher_add_view(app->dispatcher, ViewInfo, widget_get_view(app->info));
    Gui* gui = furi_record_open(RECORD_GUI);
    view_dispatcher_attach_to_gui(app->dispatcher, gui, ViewDispatcherTypeFullscreen);
    view_dispatcher_switch_to_view(app->dispatcher, ViewMenu);
    view_dispatcher_run(app->dispatcher);

    view_dispatcher_remove_view(app->dispatcher, ViewMenu);
    view_dispatcher_remove_view(app->dispatcher, ViewInfo);
    submenu_free(app->menu);
    widget_free(app->info);
    view_dispatcher_free(app->dispatcher);
    furi_record_close(RECORD_GUI);
    free(app);
    return 0;
}}
'''

LED = r'''// {name}: blinks the Flipper's own RGB LED (no wiring needed). OK cycles the colour, Back exits.
#include <furi.h>
#include <gui/gui.h>
#include <input/input.h>
#include <notification/notification_messages.h>

static const NotificationSequence* colours[] = {{&sequence_blink_red_100, &sequence_blink_green_100, &sequence_blink_blue_100}};
static const char* names[] = {{"red", "green", "blue"}};

typedef struct {{
    int colour;
}} State;

static void draw_cb(Canvas* canvas, void* ctx) {{
    State* s = ctx;
    canvas_clear(canvas);
    canvas_set_font(canvas, FontPrimary);
    canvas_draw_str_aligned(canvas, 64, 14, AlignCenter, AlignCenter, "{name}");
    canvas_set_font(canvas, FontSecondary);
    char line[32];
    snprintf(line, sizeof(line), "Blinking %s", names[s->colour]);
    canvas_draw_str_aligned(canvas, 64, 36, AlignCenter, AlignCenter, line);
    canvas_draw_str_aligned(canvas, 64, 56, AlignCenter, AlignCenter, "OK: colour  Back: exit");
}}

static void input_cb(InputEvent* event, void* ctx) {{
    furi_message_queue_put(ctx, event, FuriWaitForever);
}}

int32_t {appid}_app(void* p) {{
    UNUSED(p);
    State* s = malloc(sizeof(State));
    s->colour = 0;
    NotificationApp* notifications = furi_record_open(RECORD_NOTIFICATION);
    FuriMessageQueue* queue = furi_message_queue_alloc(8, sizeof(InputEvent));
    ViewPort* view_port = view_port_alloc();
    view_port_draw_callback_set(view_port, draw_cb, s);
    view_port_input_callback_set(view_port, input_cb, queue);
    Gui* gui = furi_record_open(RECORD_GUI);
    gui_add_view_port(gui, view_port, GuiLayerFullscreen);

    InputEvent event;
    bool running = true;
    while(running) {{
        if(furi_message_queue_get(queue, &event, 500) == FuriStatusOk) {{
            if(event.type == InputTypeShort && event.key == InputKeyBack) running = false;
            if(event.type == InputTypeShort && event.key == InputKeyOk) {{
                s->colour = (s->colour + 1) % 3;
                FURI_LOG_I("{appid}", "colour %s", names[s->colour]);
                view_port_update(view_port);
            }}
        }} else {{
            notification_message(notifications, colours[s->colour]);
        }}
    }}

    gui_remove_view_port(gui, view_port);
    view_port_free(view_port);
    furi_message_queue_free(queue);
    furi_record_close(RECORD_GUI);
    furi_record_close(RECORD_NOTIFICATION);
    free(s);
    return 0;
}}
'''

JS_APP = '''// {name}: a JavaScript app. Runs on Momentum and recent official firmware (Apps > Scripts).
// Docs: https://developer.flipper.net/flipperzero/doxygen/js.html
let eventLoop = require("event_loop");
let gui = require("gui");
let dialog = require("gui/dialog");

let view = dialog.makeWith({{
    header: "{name}",
    text: "Made with Flipper Workbench",
    center: "OK",
}});
eventLoop.subscribe(view.input, function (_sub, button, gui, eventLoop) {{
    if (button === "center") eventLoop.stop();
}}, gui, eventLoop);
eventLoop.subscribe(gui.viewDispatcher.navigation, function (_sub, _item, eventLoop) {{
    eventLoop.stop();
}}, eventLoop);
gui.viewDispatcher.switchTo(view);
eventLoop.run();
'''

TEMPLATES = {
    "hello": ("Hello screen", "Draws text and counts button presses. The smallest complete app.", HELLO),
    "menu": ("Menu app", "A menu with two items and an info screen, using the ViewDispatcher.", MENU),
    "led": ("LED blinker", "Blinks the Flipper's RGB LED. No wiring needed.", LED),
    "js": ("JavaScript app", "A .js app: no compiling, runs straight from the SD card.", JS_APP),
}


def appid_from(name):
    a = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    if not a or a[0].isdigit():
        a = "app_" + a
    return a[:32]


def _icon(path):
    from PIL import Image
    rows = ["..######..", ".#......#.", "#..#..#..#", "#........#", "#.#....#.#", "#..####..#", "#........#",
            ".#......#.", "..######..", ".........."]
    im = Image.new("1", (10, 10), 1)
    for y, r in enumerate(rows):
        for x, c in enumerate(r):
            if c == "#":
                im.putpixel((x, y), 0)
    im.save(path)


def create(parent, name, template, category="Tools"):
    """Write the project folder; returns its path (and the .js file for JS apps)."""
    name = re.sub(r"[\"\\\r\n]+", "", name).strip()[:30] or "My App"
    if template not in TEMPLATES:
        raise ValueError("Unknown template.")
    if category not in CATEGORIES:
        category = "Tools"
    appid = appid_from(name)
    folder = Path(parent) / appid
    if folder.exists() and any(folder.iterdir()):
        raise FileExistsError(f"{folder} already exists and isn't empty.")
    folder.mkdir(parents=True, exist_ok=True)
    src = TEMPLATES[template][2].format(name=name, appid=appid)
    if template == "js":
        (folder / f"{appid}.js").write_text(src, encoding="utf-8", newline="\n")
        return {"folder": str(folder), "appid": appid, "js": str(folder / f"{appid}.js")}
    (folder / "application.fam").write_text(FAM.format(appid=appid, name=name, category=category), encoding="utf-8", newline="\n")
    (folder / f"{appid}.c").write_text(src, encoding="utf-8", newline="\n")
    _icon(folder / "icon.png")
    (folder / ".gitignore").write_text("dist/\n.vscode/\n", encoding="utf-8", newline="\n")
    return {"folder": str(folder), "appid": appid}

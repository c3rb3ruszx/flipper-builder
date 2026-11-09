// Workbench companion: shows what Flipper Workbench (the PC app) wrote to
// /ext/apps_data/workbench/status.txt the last time it was connected.
// Back closes it. Reads one small file; writes nothing.

#include <furi.h>
#include <gui/gui.h>
#include <input/input.h>
#include <storage/storage.h>

#define STATUS_PATH EXT_PATH("apps_data/workbench/status.txt")
#define TEXT_MAX    480
#define LINE_MAX    32

typedef struct {
    char text[TEXT_MAX];
    uint8_t scroll;
    uint8_t lines;
} State;

static void count_lines(State* s) {
    s->lines = s->text[0] ? 1 : 0;
    for(const char* p = s->text; *p; p++)
        if(*p == '\n' && p[1]) s->lines++;
}

static void draw_cb(Canvas* canvas, void* ctx) {
    State* s = ctx;
    canvas_clear(canvas);
    canvas_set_font(canvas, FontPrimary);
    canvas_draw_str(canvas, 2, 10, "Flipper Workbench");
    canvas_draw_line(canvas, 0, 13, 127, 13);
    canvas_set_font(canvas, FontSecondary);

    const char* p = s->text;
    uint8_t index = 0;
    int y = 24;
    while(*p && y <= 63) {
        char line[LINE_MAX];
        size_t n = 0;
        while(*p && *p != '\n' && n < LINE_MAX - 1)
            line[n++] = *p++;
        line[n] = '\0';
        while(*p && *p != '\n')
            p++; // skip the rest of a too-long line
        if(*p == '\n') p++;
        if(index++ < s->scroll) continue;
        canvas_draw_str(canvas, 2, y, line);
        y += 10;
    }
    if(s->lines > 4) canvas_draw_str_aligned(canvas, 126, 63, AlignRight, AlignBottom, "...");
}

static void input_cb(InputEvent* event, void* ctx) {
    furi_message_queue_put(ctx, event, FuriWaitForever);
}

int32_t workbench_companion_app(void* p) {
    UNUSED(p);
    State* s = malloc(sizeof(State));
    memset(s, 0, sizeof(State));
    strncpy(
        s->text,
        "Not connected yet.\nOpen Flipper Workbench on\nyour PC and plug me in.",
        TEXT_MAX - 1);

    Storage* storage = furi_record_open(RECORD_STORAGE);
    File* file = storage_file_alloc(storage);
    if(storage_file_open(file, STATUS_PATH, FSAM_READ, FSOM_OPEN_EXISTING)) {
        size_t n = storage_file_read(file, s->text, TEXT_MAX - 1);
        s->text[n] = '\0';
    }
    storage_file_close(file);
    storage_file_free(file);
    furi_record_close(RECORD_STORAGE);
    count_lines(s);

    FuriMessageQueue* queue = furi_message_queue_alloc(8, sizeof(InputEvent));
    ViewPort* view_port = view_port_alloc();
    view_port_draw_callback_set(view_port, draw_cb, s);
    view_port_input_callback_set(view_port, input_cb, queue);
    Gui* gui = furi_record_open(RECORD_GUI);
    gui_add_view_port(gui, view_port, GuiLayerFullscreen);

    InputEvent event;
    while(furi_message_queue_get(queue, &event, FuriWaitForever) == FuriStatusOk) {
        if(event.type != InputTypeShort && event.type != InputTypeRepeat) continue;
        if(event.key == InputKeyBack) break;
        if(event.key == InputKeyDown && s->lines > 4 && s->scroll < s->lines - 4) s->scroll++;
        if(event.key == InputKeyUp && s->scroll > 0) s->scroll--;
        view_port_update(view_port);
    }

    gui_remove_view_port(gui, view_port);
    view_port_free(view_port);
    furi_message_queue_free(queue);
    furi_record_close(RECORD_GUI);
    free(s);
    return 0;
}

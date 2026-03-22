"""
GIF generators for emoji frames. Targets GIMP 3.x (PyGObject), not legacy gimpfu / GIMP 2.
Run only via GIMP batch (see run_emojis.sh).
"""

from __future__ import annotations

import math
import os
import sys
from random import randint

try:
    import gi

    gi.require_version("Gimp", "3.0")
    gi.require_version("Gegl", "0.4")
    from gi.repository import Gegl, Gimp, Gio
except (ImportError, ValueError, OSError) as e:
    print(
        "Could not load GIMP 3 Python bindings (gi.repository.Gimp).\n"
        "This script must run inside GIMP batch mode, not plain Python.\n\n"
        "  cd emojis && ./run_emojis.sh /path/to/image.png\n\n"
        f"Import error: {e}",
        file=sys.stderr,
    )
    sys.exit(1)


def output_filename(in_file: str, ext: str) -> str:
    return os.path.splitext(in_file)[0] + "-{}.gif".format(ext)


num_colors = 48


def _load_png(path: str) -> Gimp.Image:
    proc = Gimp.get_pdb().lookup_procedure("file-png-load")
    config = proc.create_config()
    config.set_property("run-mode", Gimp.RunMode.NONINTERACTIVE)
    config.set_property("file", Gio.File.new_for_path(path))
    result = proc.run(config)
    if result.index(0) != Gimp.PDBStatusType.SUCCESS:
        raise RuntimeError(f"file-png-load failed for {path}: {result.index(1)}")
    return result.index(1)


def _save_gif_animated(
    image: Gimp.Image,
    outfile: str,
    frame_time_ms: int,
    replace_frames: bool,
) -> None:
    proc = Gimp.get_pdb().lookup_procedure("file-gif-export")
    if proc is None:
        raise RuntimeError("file-gif-export procedure not found")
    config = proc.create_config()
    config.set_property("run-mode", Gimp.RunMode.NONINTERACTIVE)
    config.set_property("image", image)
    config.set_property("file", Gio.File.new_for_path(outfile))
    config.set_property("options", None)
    arg_names = {p.name for p in proc.get_arguments()}
    if "metadata" in arg_names:
        config.set_property("metadata", None)
    config.set_property("interlace", False)
    config.set_property("loop", True)
    config.set_property("number-of-repeats", 0)
    config.set_property("default-delay", frame_time_ms)
    config.set_property("default-dispose", "replace" if replace_frames else "combine")
    config.set_property("as-animation", True)
    config.set_property("force-delay", True)
    config.set_property("force-dispose", True)
    result = proc.run(config)
    if result.index(0) != Gimp.PDBStatusType.SUCCESS:
        err = result.index(1) if result.get_n_values() > 1 else "unknown error"
        raise RuntimeError(f"file-gif-export failed for {outfile}: {err}")


def _plug_in_tile(image: Gimp.Image, drawable: Gimp.Drawable, new_w: int, new_h: int, new_image: bool) -> None:
    proc = Gimp.get_pdb().lookup_procedure("plug-in-tile")
    if proc is None:
        raise RuntimeError("plug-in-tile procedure not found")
    config = proc.create_config()
    config.set_property("run-mode", Gimp.RunMode.NONINTERACTIVE)
    config.set_property("image", image)
    config.set_core_object_array("drawables", [drawable])
    config.set_property("new-width", new_w)
    config.set_property("new-height", new_h)
    config.set_property("new-image", new_image)
    result = proc.run(config)
    if result.index(0) != Gimp.PDBStatusType.SUCCESS:
        err = result.index(1) if result.get_n_values() > 1 else "unknown error"
        raise RuntimeError(f"plug-in-tile failed: {err}")


def _layers_top_to_bottom(img: Gimp.Image):
    return img.get_layers()


def _insert_layer_top(img: Gimp.Image, layer: Gimp.Layer) -> None:
    img.insert_layer(layer, None, 0)


def intensifies(in_file: str) -> None:
    img = _load_png(in_file)
    outfile = output_filename(in_file, "intensifies")
    horiz_displace_pct = 17
    vert_displace_pct = 17
    frame_time_ms = 20
    replace_frames = True

    num_frames = 24

    w, h = img.get_width(), img.get_height()
    horiz_displace_px = int(w * (horiz_displace_pct / 100.0))
    vert_displace_px = int(h * (vert_displace_pct / 100.0))

    img.undo_disable()
    layers = _layers_top_to_bottom(img)
    delta_x = randint(-horiz_displace_px, horiz_displace_px)
    delta_y = randint(-vert_displace_px, vert_displace_px)
    for _ in range(1, num_frames):
        bottom = _layers_top_to_bottom(img)[-1]
        layer = bottom.copy()
        _insert_layer_top(img, layer)
        top = _layers_top_to_bottom(img)[0]
        top.transform_translate(float(delta_x), float(delta_y))
        next_delta_x = randint(-horiz_displace_px, horiz_displace_px)
        next_delta_y = randint(-vert_displace_px, vert_displace_px)
        while (
            abs(next_delta_x - delta_x) < horiz_displace_px * 0.15
            and abs(next_delta_y - delta_y) < vert_displace_px * 0.15
        ):
            next_delta_x = randint(-horiz_displace_px, horiz_displace_px)
            next_delta_y = randint(-vert_displace_px, vert_displace_px)
        delta_x, delta_y = next_delta_x, next_delta_y

    img.crop(img.get_width(), img.get_height(), 0, 0)

    img.convert_indexed(
        Gimp.ConvertDitherType.NONE,
        Gimp.ConvertPaletteType.GENERATE,
        num_colors,
        False,
        False,
        "",
    )
    _save_gif_animated(img, outfile, frame_time_ms, replace_frames)
    img.delete()


def party(in_file: str) -> None:
    img = _load_png(in_file)
    outfile = output_filename(in_file, "party")
    rotate = True
    hue_party = False
    polarity = 1
    replace_frames = True

    num_steps = 24

    img.undo_disable()
    for step in range(0, num_steps):
        bottom = _layers_top_to_bottom(img)[-1]
        layer = bottom.copy()
        _insert_layer_top(img, layer)
        if rotate:
            layer.transform_rotate(
                polarity * step / float(num_steps) * 2 * math.pi,
                True,
                0.0,
                0.0,
            )
        if hue_party:
            if polarity == 1:
                layer.colorize_hsl((step / float(num_steps) * 360 + 50) % 360, 100.0, 0.0)
            else:
                layer.colorize_hsl((360 - step / float(num_steps) * 360 + 50) % 360, 100.0, 0.0)

    bottom = _layers_top_to_bottom(img)[-1]
    img.remove_layer(bottom)

    img.convert_indexed(
        Gimp.ConvertDitherType.NONE,
        Gimp.ConvertPaletteType.GENERATE,
        num_colors,
        False,
        False,
        "",
    )
    _save_gif_animated(img, outfile, 60, replace_frames)
    img.delete()


def conga(in_file: str) -> None:
    img = _load_png(in_file)
    LEFT = "left"
    RIGHT = "right"
    UP = "up"
    DOWN = "down"

    direction = RIGHT
    step_size_px = 8
    frame_time_ms = 40
    replace_frames = True

    outfile = output_filename(in_file, "conga")
    orig_width, orig_height = img.get_width(), img.get_height()

    if direction in (LEFT, RIGHT):
        num_steps = orig_width // step_size_px
    elif direction in (UP, DOWN):
        num_steps = orig_height // step_size_px
    else:
        raise AssertionError("Invalid slide direction %s" % direction)

    img.undo_disable()
    layers = _layers_top_to_bottom(img)
    drw = layers[0]

    _plug_in_tile(img, drw, 3 * orig_width, 3 * orig_height, False)

    for i in range(1, num_steps):
        bottom = _layers_top_to_bottom(img)[-1]
        layer = bottom.copy()
        _insert_layer_top(img, layer)
        if direction == LEFT:
            layer.set_offsets(-step_size_px * i, 0)
        elif direction == RIGHT:
            layer.set_offsets(step_size_px * i, 0)
        elif direction == UP:
            layer.set_offsets(0, -step_size_px * i)
        else:
            layer.set_offsets(0, step_size_px * i)

    img.crop(orig_width, orig_height, orig_width, orig_height)

    img.convert_indexed(
        Gimp.ConvertDitherType.NONE,
        Gimp.ConvertPaletteType.GENERATE,
        num_colors,
        False,
        False,
        "",
    )
    _save_gif_animated(img, outfile, frame_time_ms, replace_frames)
    img.delete()


def run(in_file: str) -> None:
    Gegl.init(None)
    intensifies(in_file)
    party(in_file)
    conga(in_file)

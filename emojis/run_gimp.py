from random import randint
import os
from gimpfu import *

def outputFilename(inFile, ext):
    return os.path.splitext(inFile)[0] + "-{}.gif".format(ext)

num_colors = 48

def intensifies(inFile):
    img = pdb.file_png_load(inFile, 1)
    outfile = outputFilename(inFile, "intensifies")
    horiz_displace_pct = 17
    vert_displace_pct = 17
    frame_time_ms = 20 # Must be multiple of 10 per the GIF standard
    replacement_policy = 2 # 1=combine, 2=replace

    # Tune these if the output is too big
    num_frames = 24

    horiz_displace_px = int(img.width * (horiz_displace_pct/100.0))
    vert_displace_px = int(img.height * (vert_displace_pct/100.0))

    img.disable_undo()
    # Make copies of the base layer
    delta_x = randint(-horiz_displace_px, horiz_displace_px)
    delta_y = randint(-vert_displace_px, vert_displace_px)
    for _ in range(1, num_frames):
        layer = pdb.gimp_layer_copy(img.layers[-1], False) # Don't add transparency
        img.add_layer(layer)
        pdb.gimp_item_transform_translate(
            img.layers[0],
            delta_x,
            delta_y,
        )
        # Prevent the next frame being too close to the current one
        next_delta_x = randint(-horiz_displace_px, horiz_displace_px)
        next_delta_y = randint(-vert_displace_px, vert_displace_px)
        while abs(next_delta_x - delta_x) < horiz_displace_px * 0.15 and abs(next_delta_y - delta_y) < vert_displace_px * 0.15:
            next_delta_x = randint(-horiz_displace_px, horiz_displace_px)
            next_delta_y = randint(-vert_displace_px, vert_displace_px)
        delta_x, delta_y = next_delta_x, next_delta_y

    pdb.gimp_image_crop(img, img.width, img.height, 0, 0)

    # Image, no dithering, make optimal palette, # colors, dither alpha channel,
    # remove unused colors (ignored), name of custom palette (ignored)
    pdb.gimp_image_convert_indexed(img, 0, 0, num_colors, False, False, "")
    drw = pdb.gimp_image_get_active_drawable(img)
    # Image, drawable, filename, raw filename, interlaced, loop forever, ms between frames, replace frames
    pdb.file_gif_save(img, drw, outfile, outfile, 0, 1, frame_time_ms, replacement_policy, run_mode=1)

def party(inFile):
    img = pdb.file_png_load(inFile, 1)
    outfile = outputFilename(inFile, "party")
    rotate = True
    party = False
    polarity = 1  # Use -1 to reverse direction of spin/colors
    replacement_policy = 2 # 1=combine, 2=replace

    # Tune these if the output is too big
    num_steps = 24

    img.disable_undo()
    for step in range(0, num_steps):
        layer = pdb.gimp_layer_copy(img.layers[-1], False) # Don't add transparency
        img.add_layer(layer)
        if rotate:
            # Item, radians, auto center, center x coords (ignored), center y coords (ignored)
            pdb.gimp_item_transform_rotate(layer, polarity*step/float(num_steps)*2*3.1415926535, True, 0, 0)
        if party:
            # Item, hue (degrees), saturation, lightness
            if polarity == 1:
                pdb.gimp_drawable_colorize_hsl(layer, (step/float(num_steps)*360 + 50) % 360, 100, 0)
            else:
                pdb.gimp_drawable_colorize_hsl(layer, (360 - step/float(num_steps)*360 + 50) % 360, 100, 0)

    # delete the original layer
    img.remove_layer(img.layers[-1])

    # Image, no dithering, make optimal palette, # colors, dither alpha channel,
    # remove unused colors (ignored), name of custom palette (ignored)
    pdb.gimp_image_convert_indexed(img, 0, 0, num_colors, False, False, "")
    drw = pdb.gimp_image_get_active_drawable(img)
    # Image, drawable, filename, raw filename, interlaced, loop forever, ms between frames, replace frames
    pdb.file_gif_save(img, drw, outfile, outfile, 0, 1, 60, replacement_policy, run_mode=1)

def conga(inFile):
    img = pdb.file_png_load(inFile, 1)
    LEFT = "left"
    RIGHT = "right"
    UP = "up"
    DOWN = "down"

    direction = RIGHT
    step_size_px = 8 # For best results, this should be a factor of the image direction where the slide is happening
    frame_time_ms = 40 # Must be multiple of 10 per the GIF standard
    replacement_policy = 2 # 1=combine, 2=replace

    outfile = outputFilename(inFile, "conga")
    orig_width, orig_height = img.width, img.height


    if direction in (LEFT, RIGHT):
        num_steps = img.width // step_size_px
    elif direction in (UP, DOWN):
        num_steps = img.height // step_size_px
    else:
        assert False, "Invalid slide direction %s" % direction

    img.disable_undo()
    drw = pdb.gimp_image_active_drawable(img)

    pdb.plug_in_tile(img, drw, 3*img.width, 3*img.height, False) # image, drawable, new width, new height, don't create new image

    # Make 3 copies of the base layer
    for i in range(1, num_steps):
        layer = pdb.gimp_layer_copy(img.layers[-1], False) # Don't add transparency
        if direction == LEFT:
            translated_layer = pdb.gimp_item_transform_translate(layer, -step_size_px * i, 0)
        elif direction == RIGHT:
            translated_layer = pdb.gimp_item_transform_translate(layer, step_size_px * i, 0)
        elif direction == UP:
            translated_layer = pdb.gimp_item_transform_translate(layer, 0, -step_size_px * i)
        elif direction == DOWN:
            translated_layer = pdb.gimp_item_transform_translate(layer, 0, step_size_px * i)
        img.add_layer(translated_layer)

    pdb.gimp_image_crop(img, orig_width, orig_height, orig_width, orig_height)

    # Image, no dithering, make optimal palette, # colors, dither alpha channel,
    # remove unused colors (ignored), name of custom palette (ignored)
    pdb.gimp_image_convert_indexed(img, 0, 0, num_colors, False, False, "")
    drw = pdb.gimp_image_get_active_drawable(img)
    # Image, drawable, filename, raw filename, interlaced, loop forever, ms between frames, replace frames
    pdb.file_gif_save(img, drw, outfile, outfile, 0, 1, frame_time_ms, replacement_policy, run_mode=1)

def run(inFile):
    intensifies(inFile)
    party(inFile)
    conga(inFile)

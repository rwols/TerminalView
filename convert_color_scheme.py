"""Functionality for converting a color scheme to a "TerminalView" scheme."""
import os
import plistlib
import sublime


def hex_to_rgb(hexstring):
    """Convert a string representing a hex color to an RGB tuple."""
    return (int(hexstring[1:3], 16) / 255,
            int(hexstring[3:5], 16) / 255,
            int(hexstring[5:7], 16) / 255)


def rgb_to_hex(rgb):
    """Convert an RGB tuple to a hex string."""
    return "#{}{}{}".format(
        format(int(rgb[0] * 255), "02x"),
        format(int(rgb[1] * 255), "02x"),
        format(int(rgb[2] * 255), "02x"))


def norm2(vec3):
    """Compute the squared norm of a three-dimensional vector."""
    return vec3[0]**2 + vec3[1]**2 + vec3[2]**2


def distance2(a, b):
    """Compute the squared distance between two 3D vectors."""
    return norm2((a[0] - b[0], a[1] - b[1], a[2] - b[2]))


def next_color(color_text):
    """Given a color string "#xxxxxy", returns its next color "#xxxxx{y+1}"."""
    hex_value = int(color_text[1:], 16)
    if hex_value == 16777215:  # #ffffff
        return "#fffffe"
    else:
        return "#{}".format(hex(hex_value + 1)[2:])


# Also see: pyte/graphics.py
_name_from_index = ["black", "white", "red", "green", "blue", "brown", "magenta", "cyan"]

_rgb_from_name = {
    "black":   (0., 0., 0.),  # NOQA (silence flake8 linter about extraneous whitespace)
    "white":   (1., 1., 1.),  # NOQA

    "red":     (1., 0., 0.),  # NOQA
    "green":   (0., 1., 0.),  # NOQA
    "blue":    (0., 0., 1.),  # NOQA

    "cyan":    (0., 1., 1.),  # NOQA
    "magenta": (1., 0., 1.),  # NOQA
    "brown":   (1., 1., 0.)   # NOQA FIXME: Should be yellow...? This looks like a pyte issue.
}


def convert_color_scheme(infile, outfile):
    """Convert a color scheme from infile into outfile."""
    print("processing file", infile)
    base = plistlib.readPlistFromBytes(sublime.load_resource(infile).encode("utf-8"))
    scheme = base["settings"]

    # Fetch the "default" color.
    default = hex_to_rgb(scheme[0]["settings"]["background"])

    # Fetch the "black" color. In a dark scheme, it's actually white-ish.
    black = hex_to_rgb(scheme[0]["settings"]["foreground"])

    # Fetch the "selection" color. We make the assumption that the selection color is a suitable
    # background color for all other colors.
    selection = scheme[0]["settings"]["selection"]

    # Fetch all the other colors (start at 1).
    colors = set()
    for i in range(1, len(scheme)):
        item = scheme[i]
        scope = item.get("scope", None)
        if scope and "sublimelinter" in scope:
            print("skipping sublimelinter scope...")
            continue
        hexcolor = item.get("settings", {}).get("foreground", None)
        if hexcolor:
            # Note that colors is a set, so duplicates are removed while we iterate.
            colors.add(hex_to_rgb(hexcolor))

    # Convert into a list
    colors = list(colors)
    print("extracted", len(colors), "scope colors from scheme")

    # Start processing our colors.
    terminal_colors = [default, black]
    while len(colors) < 6:
        print("adding extra black color so that we have enough colors to work with.")
        # we need at least six colors
        colors.append(black)

    # Skip the first two colors ("black" and "white").
    # This is the main "algorithm" of this function.
    for i in range(2, 8):
        best_index = -1
        smallest_distance = float("inf")
        terminal_color = _rgb_from_name[_name_from_index[i]]
        for j, color in enumerate(colors):
            d = distance2(color, terminal_color)
            if d < smallest_distance:
                best_index = j
                smallest_distance = d
        terminal_colors.append(colors[best_index])
        del colors[best_index]  # Don't repeat colors.

    # Convert our colors back to hex.
    terminal_colors = [rgb_to_hex(c) for c in terminal_colors]

    # Remove scopes from the color scheme.
    while len(scheme) > 1:
        del scheme[-1]

    # Now start adding in our own scopes.
    for i in range(0, 8):
        if i == 0:
            background = next_color(terminal_colors[i])
        else:
            background = terminal_colors[i]
        for j in range(0, 8):
            scope = "terminalview.{}_{}".format(_name_from_index[i], _name_from_index[j])
            # If the foreground color is the same as the background, use the "selection" color for
            # the foreground.
            foreground = selection if i == j else terminal_colors[j]
            settings = {"background": background, "foreground": foreground}
            scheme.append({"scope": scope, "settings": settings})

    # Save the results.
    os.makedirs(os.path.dirname(outfile), exist_ok=True)
    print("saving to", outfile)
    plistlib.writePlist(base, outfile)

# svg_tools.py
from __future__ import annotations
import re
import xml.etree.ElementTree as ET
from typing import Dict, Optional, Union

# =========================
# Helpers
# =========================

def _svg_ns(root: ET.Element) -> str | None:
    """Return the SVG namespace URI from the root element, if present."""
    if root.tag.startswith("{") and "}" in root.tag:
        return root.tag[1:].split("}", 1)[0]
    return None

def add_top_left_label(
    tree: ET.ElementTree,
    label_text: str,
    *,
    x: str | int = 8,
    y: str | int = 16,
    font_size: str = "12",
    fill: str = "#000000",
    font_family: str = "sans-serif",
    element_id: str = "__theme_label__",
    replace_if_exists: bool = True,
) -> ET.ElementTree:
    """
    Add a <text> element at the top-left corner with the given label.
    - Coordinates are in SVG user units (works with viewBox).
    - If replace_if_exists=True, an existing element with the same id is removed.
    - Keeps everything else intact.

    Returns the same ElementTree (mutated).
    """
    root = tree.getroot()
    ns_uri = _svg_ns(root)
    ns = f"{{{ns_uri}}}" if ns_uri else ""

    # Remove any previous label to avoid duplicates (optional)
    if replace_if_exists and element_id:
        for elem in list(root):
            if _localname(elem.tag) == "text" and elem.get("id") == element_id:
                root.remove(elem)

    text_el = ET.Element(f"{ns}text", {
        "x": str(x),
        "y": str(y),
        "font-size": str(font_size),
        "fill": fill,
        "font-family": font_family,
    })
    if element_id:
        text_el.set("id", element_id)

    text_el.text = label_text

    # Insert near the beginning so it's above backgrounds but before overlays
    # (append also works; choose what looks best for your files)
    root.insert(0, text_el)
    return tree

def _localname(tag: str) -> str:
    """Return the localname without the namespace, e.g. '{ns}g' -> 'g'."""
    return tag.rsplit('}', 1)[-1] if tag.startswith('{') else tag

def _tostring(elem: ET.Element) -> str:
    """Serialize an element (unicode)."""
    return ET.tostring(elem, encoding="unicode")

def parse_svg(file_path: str) -> ET.ElementTree:
    """
    Load an SVG from disk and return an ElementTree parser object.
    """
    return ET.parse(file_path)

def get_second_level_groups(tree: ET.ElementTree) -> Dict[str, str]:
    """
    Return a dict of all SECOND-LEVEL <g> groups (key = group id, value = group XML).
    'Second-level' means: <svg> -> (any direct child) -> <g> (direct child of that).
    If a <g> lacks an id, a synthetic key like '(no-id)#3' is used.
    """
    root = tree.getroot()
    result: Dict[str, str] = {}
    no_id_counter = 0

    # Iterate over direct children of <svg>
    for child in list(root):
        # Only consider the child's DIRECT children that are <g>
        for gc in list(child):
            if _localname(gc.tag) == "g":
                gid = gc.get("id")
                if not gid:
                    no_id_counter += 1
                    gid = f"(no-id)#{no_id_counter}"
                result[gid] = _tostring(gc)
    return result

def write_svg(tree: ET.ElementTree, out_path: str) -> None:
    """
    Save the (possibly modified) SVG back to disk.
    """
    tree.write(out_path, encoding="unicode", xml_declaration=True)

# =========================
# Public API (bulk style update)
# =========================

def _replace_styles_with_map(xml_fragment: str, fill: str, stroke: str, stroke_width:str) -> str:
    """
    Apply the requested regex:
      pattern = style="(.*)(fill|stroke):(.*);(fill|stroke):(.*)"
      repl    = style="$1fill:[fill];stroke:[stroke]"
    Where [fill]/[stroke] are substituted from the dict.

    Notes:
    - Implemented via a callback so we can use Python's group values directly.
    - DOTALL and IGNORECASE to handle newlines/casing inside style="...".
    - Keeps any pre-existing style prefix captured by group(1).
    """
    #regexfill = re.compile('fill:(.*)', flags=re.IGNORECASE | re.DOTALL)
    #regexstroke = re.compile('stroke:(.*)', flags=re.IGNORECASE | re.DOTALL)
    #def _replfill(m: re.Match) -> str:
    #    g2 = m.group(2)  # everything before the first fill|stroke pair
    #    return f'fill:{fill}{g2}'
    
    xml_fragment = re.sub(pattern='stroke="(#......)"', repl=f'stroke="{stroke}"', string=xml_fragment, flags=re.IGNORECASE)
    xml_fragment = re.sub(pattern='stroke="rgb\((.*),(.*),(.*)\)"', repl=f'stroke="{stroke}"', string=xml_fragment, flags=re.IGNORECASE)
    xml_fragment = re.sub(pattern='stroke="none"', repl=f'stroke="{stroke}"', string=xml_fragment, flags=re.IGNORECASE)

    xml_fragment = re.sub(pattern='stroke:(#......)', repl=f'stroke:{stroke}', string=xml_fragment, flags=re.IGNORECASE)
    xml_fragment = re.sub(pattern='stroke:rgb\((.*),(.*),(.*)\)', repl=f'stroke:{stroke}', string=xml_fragment, flags=re.IGNORECASE)
    xml_fragment = re.sub(pattern='stroke:none', repl=f'stroke:{stroke}', string=xml_fragment, flags=re.IGNORECASE)
    
    xml_fragment = re.sub(pattern='fill="(#......)"', repl=f'fill="{fill}"', string=xml_fragment, flags=re.IGNORECASE)
    xml_fragment = re.sub(pattern='fill="rgb\((.*),(.*),(.*)\)"', repl=f'fill="{fill}"', string=xml_fragment, flags=re.IGNORECASE)
    xml_fragment = re.sub(pattern='fill="none"', repl=f'fill="{fill}"', string=xml_fragment, flags=re.IGNORECASE)
    
    xml_fragment = re.sub(pattern='fill:(#......)', repl=f'fill:{fill}', string=xml_fragment, flags=re.IGNORECASE)
    xml_fragment = re.sub(pattern='fill:rgb\((.*),(.*),(.*)\)', repl=f'fill:{fill}', string=xml_fragment, flags=re.IGNORECASE)
    xml_fragment = re.sub(pattern='fill:none', repl=f'fill:{fill}', string=xml_fragment, flags=re.IGNORECASE)

    xml_fragment = re.sub(pattern='stroke-width="(.*)(mm|px)"', repl=f'stroke-width="{stroke_width}px"', string=xml_fragment, flags=re.IGNORECASE)
    
    return xml_fragment

def bulk_update_group_styles(
    tree_or_path: Union[str, ET.ElementTree],
    style_map: Dict[str, Dict[str, str]],
) -> ET.ElementTree:
    """
    Update multiple groups' inner content styles by regex-replacing any style="...fill:...;stroke:..."
    (in any order) found within the group's descendants.

    Parameters
    ----------
    tree_or_path : Union[str, ET.ElementTree]
        Either a filesystem path to an SVG or an already parsed ElementTree.
    style_map : Dict[str, Dict[str, str]]
        e.g. {
          "groupA": {"fill": "#FF00FF", "stroke": "#333333"},
          "groupB": {"fill": "none",   "stroke": "#000000"},
        }

    Returns
    -------
    ET.ElementTree
        The same (mutated) ElementTree with updated groups.
    """
    # Load tree if needed
    if isinstance(tree_or_path, ET.ElementTree):
        tree = tree_or_path
    else:
        tree = ET.parse(str(tree_or_path))

    groups = get_second_level_groups(tree=tree)
    layer = tree.find(".//{*}g")
    layer.clear()

    for gid, kv in style_map.items():
        if gid in groups.keys():
            fill = kv.get("fill", "")
            stroke = kv.get("stroke", "")
            stroke_width = kv.get("stroke-width", "")
            target = groups[gid]
        else:
            # Skip missing groups
            continue

        updated_inner = _replace_styles_with_map(target, fill=fill, stroke=stroke, stroke_width=stroke_width)
        layer.append(ET.fromstring(updated_inner))

    return tree

def process_svg_styles(
    svg_path: str,
    style_map: Dict[str, Dict[str, str]],
    out_path: Optional[str] = None
) -> ET.ElementTree:
    """
    One-shot convenience: read, update, and optionally write.
    """
    tree = bulk_update_group_styles(svg_path, style_map)
    if out_path:
        write_svg(tree, out_path)
    return tree

# =========================
# Usage example (optional)
# =========================
if __name__ == "__main__":
    # Example:
    tree = parse_svg("test.svg")
    #groups = get_second_level_groups(tree)
    #print(groups.keys())
    
    style_updates = {
        "MainShape": {"fill": "#FF0000", "stroke": "#9DFF00FF", "stroke-width": "0.2mm"},
        "PowerPort": {"fill": "#000000", "stroke": "#9DFF00FF", "stroke-width": "0.2mm"},
        "SecondaryShape": {"fill": "#000000", "stroke": "#9DFF00FF", "stroke-width": "0.2mm"},
        "Pin": {"fill": "#000000", "stroke": "#9DFF00FF", "stroke-width": "0.2mm"},
        "Wire": {"fill": "#000000", "stroke": "#9DFF00FF", "stroke-width": "0.2mm"},
        "CrossRef": {"fill": "#000000", "stroke": "#9DFF00FF", "stroke-width": "0.2mm"},
        "CrossRefText": {"fill": "#000000", "stroke": "#9DFF00FF", "stroke-width": "0.2mm"},
        "AllOtherText": {"fill": "#000000", "stroke": "#9DFF00FF", "stroke-width": "0.2mm"}
    }
    write_svg(tree, "test_save.svg")
    process_svg_styles(tree, style_updates, out_path="test_new.svg")
    pass

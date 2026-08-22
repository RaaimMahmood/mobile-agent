from dataclasses import dataclass
from typing import Optional
from lxml import etree


@dataclass
class InteractiveElement:
    id: int
    bounds: tuple[int, int, int, int]   # x1, y1, x2, y2
    class_name: str
    resource_id: str
    content_desc: str
    text: str
    clickable: bool
    focusable: bool
    scrollable: bool
    checkable: bool
    checked: bool
    password: bool

    @property
    def center(self) -> tuple[int, int]:
        return (
            (self.bounds[0] + self.bounds[2]) // 2,
            (self.bounds[1] + self.bounds[3]) // 2,
        )

    @property
    def signature(self) -> str:
        return f"{self.resource_id}::{self.class_name}"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "bounds": list(self.bounds),
            "class_name": self.class_name,
            "resource_id": self.resource_id,
            "content_desc": self.content_desc,
            "text": self.text,
            "clickable": self.clickable,
            "focusable": self.focusable,
            "scrollable": self.scrollable,
            "password": self.password,
        }


def _parse_bounds(bounds_str: str) -> Optional[tuple[int, int, int, int]]:
    """Parse '[x1,y1][x2,y2]' format from uiautomator XML."""
    import re
    nums = re.findall(r"\d+", bounds_str)
    if len(nums) == 4:
        x1, y1, x2, y2 = int(nums[0]), int(nums[1]), int(nums[2]), int(nums[3])
        if x2 > x1 and y2 > y1:
            return x1, y1, x2, y2
    return None


def parse_interactive_elements(xml_str: str) -> list[InteractiveElement]:
    """
    Parse uiautomator XML dump into a list of interactive UI elements.
    Only keeps elements that are clickable, focusable, or scrollable and have non-zero area.
    Assigns sequential integer IDs (1-indexed, top-to-bottom left-to-right order).
    """
    if not xml_str or not xml_str.strip():
        return []

    try:
        root = etree.fromstring(xml_str.encode("utf-8"))
    except etree.XMLSyntaxError:
        return []

    elements: list[InteractiveElement] = []
    seen_bounds: set[tuple[int, int, int, int]] = set()

    for node in root.iter("node"):
        clickable  = node.get("clickable", "false") == "true"
        focusable  = node.get("focusable", "false") == "true"
        scrollable = node.get("scrollable", "false") == "true"

        if not (clickable or focusable or scrollable):
            continue

        bounds_str = node.get("bounds", "")
        bounds = _parse_bounds(bounds_str)
        if bounds is None:
            continue

        # deduplicate overlapping elements with same bounds
        if bounds in seen_bounds:
            continue
        seen_bounds.add(bounds)

        elements.append(InteractiveElement(
            id=0,  # assigned below
            bounds=bounds,
            class_name=node.get("class", ""),
            resource_id=node.get("resource-id", ""),
            content_desc=node.get("content-desc", ""),
            text=node.get("text", ""),
            clickable=clickable,
            focusable=focusable,
            scrollable=scrollable,
            checkable=node.get("checkable", "false") == "true",
            checked=node.get("checked", "false") == "true",
            password=node.get("password", "false") == "true",
        ))

    # sort top-to-bottom, left-to-right
    elements.sort(key=lambda e: (e.bounds[1], e.bounds[0]))

    # assign sequential IDs
    for i, elem in enumerate(elements, start=1):
        elem.id = i

    return elements

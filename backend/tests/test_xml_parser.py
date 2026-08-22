from perception.xml_parser import parse_interactive_elements

SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.FrameLayout"
        bounds="[0,0][1080,2340]" clickable="false" focusable="false" scrollable="false">
    <node index="0" text="Search" resource-id="com.linkedin:id/search_btn"
          class="android.widget.ImageButton" content-desc="Search jobs"
          bounds="[20,100][200,200]" clickable="true" focusable="true" scrollable="false"/>
    <node index="1" text="Jobs" resource-id="com.linkedin:id/jobs_tab"
          class="android.widget.TextView" content-desc="Jobs tab"
          bounds="[0,2200][270,2340]" clickable="true" focusable="false" scrollable="false"/>
    <node index="2" text="" resource-id="com.linkedin:id/feed"
          class="android.widget.ScrollView" content-desc=""
          bounds="[0,200][1080,2200]" clickable="false" focusable="false" scrollable="true"/>
    <node index="3" text="" resource-id=""
          class="android.view.View" content-desc=""
          bounds="[0,0][0,0]" clickable="false" focusable="false" scrollable="false"/>
  </node>
</hierarchy>"""


def test_parses_clickable_elements():
    elements = parse_interactive_elements(SAMPLE_XML)
    ids = {e.resource_id for e in elements}
    assert "com.linkedin:id/search_btn" in ids
    assert "com.linkedin:id/jobs_tab" in ids


def test_parses_scrollable_elements():
    elements = parse_interactive_elements(SAMPLE_XML)
    ids = {e.resource_id for e in elements}
    assert "com.linkedin:id/feed" in ids


def test_filters_zero_area():
    elements = parse_interactive_elements(SAMPLE_XML)
    for e in elements:
        x1, y1, x2, y2 = e.bounds
        assert x2 > x1 and y2 > y1


def test_sequential_ids():
    elements = parse_interactive_elements(SAMPLE_XML)
    assert [e.id for e in elements] == list(range(1, len(elements) + 1))


def test_sorted_top_to_bottom():
    elements = parse_interactive_elements(SAMPLE_XML)
    y_coords = [e.bounds[1] for e in elements]
    assert y_coords == sorted(y_coords)


def test_empty_xml_returns_empty():
    assert parse_interactive_elements("") == []


def test_center_property():
    elements = parse_interactive_elements(SAMPLE_XML)
    elem = next(e for e in elements if e.resource_id == "com.linkedin:id/search_btn")
    cx, cy = elem.center
    assert cx == (20 + 200) // 2
    assert cy == (100 + 200) // 2


def test_password_attribute_parsed_true():
    xml = """<hierarchy>
        <node class="android.widget.EditText" resource-id="pwd" text=""
              password="true" clickable="true" focusable="true"
              bounds="[0,0][100,50]" />
    </hierarchy>"""
    elements = parse_interactive_elements(xml)
    assert elements[0].password is True
    assert elements[0].to_dict()["password"] is True


def test_password_attribute_defaults_false():
    xml = """<hierarchy>
        <node class="android.widget.EditText" resource-id="username" text=""
              clickable="true" focusable="true"
              bounds="[0,0][100,50]" />
    </hierarchy>"""
    elements = parse_interactive_elements(xml)
    assert elements[0].password is False
    assert elements[0].to_dict()["password"] is False

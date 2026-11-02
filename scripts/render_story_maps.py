#!/usr/bin/env python3
"""Render the real maps of the robot face's location stories.

Owner 27 Oct: "พวกแมพเอาจากตรงนี้" -- the map export of the sales gallery
(EmbassyWorld_Map_Standalone_2026-10-27.zip: MapLibre, the project's 3D model,
its own OSM roads/buildings/places, wider tiles from OpenFreeMap) -- and he
chose "แผนที่จริง + motion ตามเสียง": the picture is the real map, the motion
(pin, routes along the real streets, the places as she names them) is drawn
over it by the robot face (emma-story.js TEMPLATES.map).

    python scripts/render_story_maps.py                 # every map in stories/maps/spec.json
    python scripts/render_story_maps.py --only nearby_7eleven
    python scripts/render_story_maps.py --check         # the place checks only, no browser, no network

Needs playwright, the export unzipped in .tmp/story-sources/map/EmbassyWorldMap
(the owner's material stays out of git, like every other story source), and
the network (OpenFreeMap tiles, the FOSSGIS car router -- one request a
second, each answer cached in .tmp/story-sources/maps/routes/).

Writes, per map:
  .tmp/story-sources/maps/<story>.png     the base (a recipe source, like every picture)
  stories/maps/<story>.json               where the project, each place and each
                                          route lie on that picture (% of it), and
                                          the PNG's sha256 -- tracked, reviewable
  stories/recipes/maps.json               the recipes, pinned to those PNGs
then `python scripts/build_story_assets.py` sizes and pins the pictures and
merges the overlays into stories.json (a draft until a person approves it).

Checks before anything is drawn (a pin on a public screen is a claim):
  - each place's straight-line km from the report's own start pin (Haversine,
    the report's method) against the report's figure;
  - each drive route's km against the report's road km (the report's routes
    came from OSRM too) -- a destination snapped to the wrong road shows up here;
  - every point inside the picture, clear of its edges.
"""
from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import math
import sys
import threading
import time
import urllib.request
from functools import partial
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import ACTIVE_PROJECT_ID  # noqa: E402

STORIES = ROOT / "data" / "projects" / ACTIVE_PROJECT_ID / "stories"
MAPS = STORIES / "maps"
SPEC = MAPS / "spec.json"
RECIPES = STORIES / "recipes" / "maps.json"
SOURCES = ROOT / ".tmp" / "story-sources"
OUT = SOURCES / "maps"
DEFAULT_WEB = SOURCES / "map" / "EmbassyWorldMap" / "web"
ROUTER = "https://routing.openstreetmap.de/routed-car/route/v1/driving/"
UA = "condo-voice-story-maps/1.0 (build script; one request a second, cached)"

#: CSS pixels of the map's width; device scale 1.5 makes the PNG 1080 wide
#: with the map's own labels half again as large (read from standing distance).
CSS_W = 720
SCALE = 1.5
#: room round the points for the robot face's labels (fraction of the side)
PAD = 0.15
#: a point must lie this far inside the picture (fraction of the side)
EDGE = 0.06

HIDE = """
header, footer, #places-panel, .map-tools, .time-controls, #map-hint, .map-attribution, #route-card,
#map-status, #close, button.close, .maplibregl-control-container, .maplibregl-marker, .poi-pin, #fatal,
#gallery, #video { display: none !important; }
#experience, #location { position: fixed !important; inset: 0 !important; padding: 0 !important;
                         margin: 0 !important; border: 0 !important; }
#map { position: fixed !important; inset: 0 !important; width: 100vw !important; height: 100vh !important;
       border-radius: 0 !important; }
"""


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def straight_ok(measured: float, report: float) -> bool:
    """The report prints 2 decimals under 1 km, 1 above: its rounding plus 5%."""
    half_digit = 0.005 if report < 1 else 0.05
    return abs(measured - report) <= half_digit + max(0.03, 0.05 * report)


def road_ok(measured: float, report: float) -> bool:
    """Two OSRM runs months apart, both free-flow: within 30%."""
    return abs(measured - report) <= 0.30 * report


def resolve_places(spec: dict, web: Path) -> tuple[dict, list[str]]:
    """{key: {lng, lat, ...}} for every place, and the problems (a place
    that fails its check is not returned)."""
    try:
        catalogue = {p["id"]: p for p in json.loads((web / "places.json").read_text(encoding="utf-8"))["places"]}
    except (OSError, ValueError, KeyError) as exc:
        return {}, ["the owner's map places.json is unreadable: %s" % exc]
    origin = spec["origin"]
    out, problems = {}, []
    for key, place in spec["places"].items():
        entry = dict(place)
        if "place_id" in place:
            got = catalogue.get(place["place_id"])
            if not got:
                problems.append("%s: %s is not in the owner's map" % (key, place["place_id"]))
                continue
            entry["lng"], entry["lat"] = float(got["lng"]), float(got["lat"])
            entry["name_en"] = (got.get("name") or {}).get("en", "")
        if not all(isinstance(entry.get(k), (int, float)) for k in ("lng", "lat")):
            problems.append("%s: no coordinate" % key)
            continue
        km = haversine_km(origin["lat"], origin["lng"], entry["lat"], entry["lng"])
        entry["straight_km_measured"] = round(km, 3)
        if place.get("straight_km") is not None and not straight_ok(km, float(place["straight_km"])):
            problems.append("%s: %.2f km in a straight line, the report says %s" % (key, km, place["straight_km"]))
            continue
        out[key] = entry
    return out, problems


def fetch_route(origin: dict, place: dict, key: str) -> dict:
    """The FOSSGIS car route (OSRM) from the report's pin to the place;
    cached per (origin, place) so a re-render does not ask again."""
    cache = OUT / "routes"
    cache.mkdir(parents=True, exist_ok=True)
    coords = "%.7f,%.7f;%.7f,%.7f" % (origin["lng"], origin["lat"], place["lng"], place["lat"])
    path = cache / ("%s-%s.json" % (key, hashlib.sha256(coords.encode()).hexdigest()[:12]))
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    time.sleep(1.1)                      # the operator's limit: one a second
    url = ROUTER + coords + "?overview=full&geometries=geojson"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    data = json.loads(urllib.request.urlopen(req, timeout=30).read().decode("utf-8"))
    if data.get("code") != "Ok" or not data.get("routes"):
        raise RuntimeError("%s: no car route (%s)" % (key, data.get("code")))
    route = data["routes"][0]
    keep = {"distance_m": route["distance"], "duration_s": route["duration"],
            "coordinates": route["geometry"]["coordinates"], "router": ROUTER, "requested": coords}
    path.write_text(json.dumps(keep), encoding="utf-8")
    return keep


def simplify(points: list, tol: float) -> list:
    """Ramer-Douglas-Peucker on screen pixels (a route of 300 OSRM points
    becomes the few dozen that change its shape on a 1080 px picture)."""
    if len(points) < 3:
        return points[:]

    def dist(p, a, b):
        (x, y), (x1, y1), (x2, y2) = p, a, b
        dx, dy = x2 - x1, y2 - y1
        if dx == dy == 0:
            return math.hypot(x - x1, y - y1)
        t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)))
        return math.hypot(x - (x1 + t * dx), y - (y1 + t * dy))

    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        i, j = stack.pop()
        best, at = 0.0, -1
        for k in range(i + 1, j):
            d = dist(points[k], points[i], points[j])
            if d > best:
                best, at = d, k
        if best > tol and at > 0:
            keep[at] = True
            stack.append((i, at))
            stack.append((at, j))
    return [p for p, k in zip(points, keep) if k]


def site_centre(web: Path) -> tuple[float, float]:
    """The middle of the project's buildings on the owner's map (its
    project-site.geojson): where the pin stands, on the 3D model."""
    data = json.loads((web / "assets" / "project-site.geojson").read_text(encoding="utf-8"))
    xs, ys = [], []
    for feature in data["features"]:
        for ring in feature["geometry"]["coordinates"]:
            for lng, lat in ring:
                xs.append(lng)
                ys.append(lat)
    return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2


def pct(v: float, side: float) -> float:
    return round(v / side * 100, 2)


def render(stories: list[str], spec: dict, places: dict, web: Path) -> list[str]:
    from playwright.sync_api import sync_playwright

    problems: list[str] = []
    origin = spec["origin"]
    project = site_centre(web)

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=str(web)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/index.html" % server.server_address[1]
    experience = (web / "experience.js").read_text(encoding="utf-8-sig")
    needle = "const map = activeMap = new maplibregl.Map("
    if needle not in experience:
        server.shutdown()
        return ["the owner's experience.js changed: the map is no longer created where this script looks"]
    # the export's files stay byte-identical on disk; only the served copy
    # hands this script the map
    exposed = experience.replace(needle, "const map = activeMap = window.__emmaMap = new maplibregl.Map(")
    recipes, overlays = [], {}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader",
                                              "--enable-unsafe-swiftshader"])
            for sid in stories:
                cfg = spec["maps"][sid]
                keys = [k for k in cfg["places"] if k in places]
                missing = [k for k in cfg["places"] if k not in places]
                if missing:
                    problems.append("%s: no checked coordinate for %s -- not rendered" % (sid, ", ".join(missing)))
                    continue
                aspect = float(cfg.get("aspect", 1.0))
                w, h = CSS_W, round(CSS_W / aspect)
                page = browser.new_page(viewport={"width": w, "height": h}, device_scale_factor=SCALE)
                page.route("**/experience.js", lambda route: route.fulfill(
                    body=exposed, content_type="application/javascript"))
                page.goto(url)
                page.add_style_tag(content=HIDE)
                page.wait_for_function("() => window.__emmaMap && window.EmbassyMap && EmbassyMap.getMapState()"
                                       " && EmbassyMap.getMapState().ready", timeout=90000)
                state = page.evaluate("""(palette) => {
                  const b = document.querySelector('[data-time="' + palette + '"]');
                  if (b) b.click();
                  const m = window.__emmaMap;
                  const layers = m.getStyle().layers;
                  // the places on the map are the story's own marks: the map's
                  // shop/POI icons and names would be a second, unchecked set;
                  // and its road-number shields drew as blank white boxes
                  // (their sprite does not load in this export -- first render)
                  layers.forEach(l => {
                    if (l.type === 'symbol' && /^(poi|airport|aerodrome)|shield/.test(l.id)) m.setLayoutProperty(l.id, 'visibility', 'none');
                    // the night palette leaves runways white: bright bars in the
                    // dark that read as something to look at (first render)
                    else if (/^aeroway/.test(l.id)) m.setLayoutProperty(l.id, 'visibility', 'none');
                    // ...and every other icon (one-way arrows drew as a stray
                    // white stroke): the labels stay, their icons go
                    else if (l.type === 'symbol' && l.layout && l.layout['icon-image']) m.setPaintProperty(l.id, 'icon-opacity', 0);
                    if (l.type === 'symbol' && l.layout && l.layout['text-field'])
                      m.setLayoutProperty(l.id, 'text-field', ['coalesce', ['get', 'name:en'], ['get', 'name:latin'], ['get', 'name']]);
                  });
                  return { layers: layers.length, online: layers.some(l => l.id === 'water') };
                }""", spec.get("palette", "night"))
                if not state["online"]:
                    problems.append("%s: the online map style did not load (offline fallback) -- not rendered" % sid)
                    page.close()
                    continue
                pts = [list(project)] + [[places[k]["lng"], places[k]["lat"]] for k in keys]
                page.wait_for_function("() => EmbassyMap.getMapState().projectModelReady", timeout=90000)
                zoom = page.evaluate("""([pts, pad, bearing, pitch]) => {
                  const m = window.__emmaMap; m.resize();
                  const lng = pts.map(p => p[0]), lat = pts.map(p => p[1]);
                  const W = m.getContainer().clientWidth, H = m.getContainer().clientHeight;
                  const cam = m.cameraForBounds([[Math.min(...lng), Math.min(...lat)], [Math.max(...lng), Math.max(...lat)]],
                    { padding: { top: H * pad, bottom: H * pad, left: W * pad, right: W * pad }, bearing });
                  m.jumpTo({ center: cam.center, zoom: cam.zoom, bearing, pitch });
                  return cam.zoom;
                }""", [pts, PAD, float(cfg.get("bearing", 0)), float(cfg.get("pitch", 0))])
                # a pitched view puts the near points lower and the far ones
                # nearer the horizon: step out until every one is inside
                for _ in range(12):
                    inside = page.evaluate("""([pts, edge]) => {
                      const m = window.__emmaMap, W = m.getContainer().clientWidth, H = m.getContainer().clientHeight;
                      return pts.every(p => { const q = m.project(p);
                        return q.x > W * edge && q.x < W * (1 - edge) && q.y > H * edge && q.y < H * (1 - edge); });
                    }""", [pts, EDGE + 0.04])
                    if inside:
                        break
                    zoom -= 0.2
                    page.evaluate("(z) => window.__emmaMap.jumpTo({ zoom: z })", zoom)
                page.evaluate("""() => new Promise(done => {
                  const m = window.__emmaMap;
                  const check = () => (m.loaded() && m.areTilesLoaded()) ? done() : m.once('idle', check);
                  check();
                })""")
                page.wait_for_timeout(2500)      # the 3D model's own frame after the tiles
                shot = page.screenshot(type="png")
                OUT.mkdir(parents=True, exist_ok=True)
                (OUT / ("%s.png" % sid)).write_bytes(shot)

                def at(lng, lat):
                    q = page.evaluate("(p) => { const q = window.__emmaMap.project(p); return [q.x, q.y]; }", [lng, lat])
                    return q

                over = {"story": sid, "image": "maps/%s.png" % sid,
                        "image_sha256": hashlib.sha256(shot).hexdigest(),
                        "w": round(w * SCALE), "h": round(h * SCALE),
                        "camera": {"zoom": round(zoom, 3), "pitch": cfg.get("pitch", 0), "bearing": cfg.get("bearing", 0)},
                        "attribution_en": spec["attribution_en"],
                        "palette": spec.get("palette", "night"),
                        "places": {}}
                px, py = at(*project)
                over["project"] = {"x": pct(px, w), "y": pct(py, h)}
                for k in keys:
                    place = places[k]
                    qx, qy = at(place["lng"], place["lat"])
                    if not (w * EDGE <= qx <= w * (1 - EDGE) and h * EDGE <= qy <= h * (1 - EDGE)):
                        problems.append("%s: %s lies outside the picture" % (sid, k))
                    item = {"x": pct(qx, w), "y": pct(qy, h), "straight_km": place["straight_km_measured"],
                            "source": place["source"]}
                    if place.get("route") != "none":
                        route = fetch_route(origin, place, k)
                        km = route["distance_m"] / 1000
                        if place.get("road_km") is not None and not road_ok(km, float(place["road_km"])):
                            problems.append("%s: the car route to %s is %.1f km, the report says %s km"
                                            % (sid, k, km, place["road_km"]))
                        line = [at(lng, lat) for lng, lat in route["coordinates"]]
                        # from the pin on the model to the mark on the place: the
                        # route's own ends are where the router met a road
                        line = [[px, py]] + line + [[qx, qy]]
                        item["route"] = [[pct(x, w), pct(y, h)] for x, y in simplify(line, 0.9)]
                        item["route_km"] = round(km, 2)
                    if place.get("walk_km") is not None:
                        walk = page.evaluate("""async (p) => {
                          const graph = await (await fetch('assets/city/pedestrian-graph.json')).json();
                          const r = EmbassyWalkingGraph.create(graph, null).localRoute({ id: 'story', lng: p[0], lat: p[1] });
                          return r && r.available ? { m: r.metres, c: r.coordinates } : null;
                        }""", [place["lng"], place["lat"]])
                        if not walk:
                            problems.append("%s: no walking route to %s on the owner's pedestrian graph" % (sid, k))
                        else:
                            if not road_ok(walk["m"] / 1000, float(place["walk_km"])):
                                problems.append("%s: the walk to %s is %.2f km, the report says %s km"
                                                % (sid, k, walk["m"] / 1000, place["walk_km"]))
                            wline = [at(lng, lat) for lng, lat in walk["c"]] + [[qx, qy]]
                            item["walk_route"] = [[pct(x, w), pct(y, h)] for x, y in simplify(wline, 0.9)]
                            item["walk_km"] = round(walk["m"] / 1000, 2)
                    over["places"][k] = item
                overlays[sid] = over
                recipes.append({
                    "id": "map_%s" % sid,
                    "source": "maps/%s.png" % sid,
                    "source_sha256": over["image_sha256"],
                    "max_w": 1080,
                    "max_kb": 320,
                    "format": "webp",
                    "why": "The real map under %s (owner 27 Oct: \"พวกแมพเอาจากตรงนี้\"), rendered from the sales gallery's "
                           "map export by scripts/render_story_maps.py: OSM roads and buildings, the project's 3D model, "
                           "the map's own night palette, its shop icons hidden (the story's places are drawn over it). "
                           "Its overlay is stories/maps/%s.json -- re-render both together." % (sid, sid),
                })
                page.close()
            browser.close()
    finally:
        server.shutdown()
    MAPS.mkdir(parents=True, exist_ok=True)
    for sid, over in overlays.items():
        (MAPS / ("%s.json" % sid)).write_text(json.dumps(over, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if overlays:
        try:
            old = json.loads(RECIPES.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            old = []
        mine = {r["id"]: r for r in recipes}
        merged = [mine.pop(r["id"], r) for r in old if isinstance(r, dict)] + list(mine.values())
        merged.sort(key=lambda r: r["id"])
        RECIPES.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", default="", help="comma-separated story ids")
    ap.add_argument("--map-source", type=Path, default=DEFAULT_WEB, help="the export's web/ folder")
    ap.add_argument("--check", action="store_true", help="check the places only (no browser, no network)")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    places, problems = resolve_places(spec, args.map_source)
    for key, place in places.items():
        print("%-22s %7.3f km  %s" % (key, place["straight_km_measured"], place.get("name_en", "")))
    wanted = [s for s in args.only.split(",") if s] or list(spec["maps"])
    unknown = [s for s in wanted if s not in spec["maps"]]
    if unknown:
        problems.append("no map for: " + ", ".join(unknown))
        wanted = [s for s in wanted if s in spec["maps"]]
    if not args.check and not problems:
        problems += render(wanted, spec, places, args.map_source)
    for line in problems:
        print("PROBLEM  " + line)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())

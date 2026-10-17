#!/usr/bin/env python3
"""
make_profile.py - build a COMPLETE vehicle-profile.json from a .glb (no TODOs).

It measures everything from the 3D model itself (pure Python 3, no packages):
  * real metre scale of the model (this Golf GLB is 100x too small: 0.043 "units" long)
  * front wheel groups (tyre + rim + brake parts) and their true wheel-centre PIVOTS
  * wheelbase, front track, rear-axle origin, ground level
  * steering wheel: all its meshes, ring centre (pivot) and the tilted column axis

Usage (run from the VideoStreamingServer folder):
    python tools/make_profile.py tools/2025_volkswagen_golf_r.glb vehicle-profile.json

All coordinates in the output are in the "scene world frame, metres":
    (glTF world space of the loaded model) x unitScaleToMetres
    +Y up, car FRONT = +Z, car LEFT = +X   (detected from the model, written to model.frame)
"""
import json, math, os, re, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import glb_inspect as G


# ---------------------------------------------------------------- reading vertex data
def read_bin(path):
    data = open(path, 'rb').read()
    off = 12
    while off + 8 <= len(data):
        clen, ctype = struct.unpack_from('<II', data, off)
        if ctype == 0x004E4942:                      # 'BIN\0'
            return data, off + 8
        off += 8 + clen
    sys.exit("no BIN chunk in the .glb")


def mesh_points(g, data, binoff, node, scale):
    """All vertices of a mesh node in scene-world space, in metres."""
    out = []
    w = node.world
    for prim in g['meshes'][node.raw['mesh']]['primitives']:
        a = g['accessors'][prim['attributes']['POSITION']]
        bv = g['bufferViews'][a['bufferView']]
        base = binoff + bv.get('byteOffset', 0) + a.get('byteOffset', 0)
        stride = bv.get('byteStride', 12)
        for i in range(a['count']):
            x, y, z = struct.unpack_from('<fff', data, base + i * stride)
            p = G.apply(w, (x, y, z))
            out.append([p[0] * scale, p[1] * scale, p[2] * scale])
    return out


# ---------------------------------------------------------------- tiny 3x3 PCA (Jacobi)
def smallest_axis(pts):
    n = len(pts)
    c = [sum(p[k] for p in pts) / n for k in range(3)]
    cov = [[sum((p[i] - c[i]) * (p[j] - c[j]) for p in pts) / n for j in range(3)] for i in range(3)]
    v = [[1.0 if i == j else 0.0 for j in range(3)] for i in range(3)]
    for _ in range(60):
        pi, pj, big = 0, 1, 0.0
        for i in range(3):
            for j in range(i + 1, 3):
                if abs(cov[i][j]) > big: big, pi, pj = abs(cov[i][j]), i, j
        if big < 1e-14: break
        th = 0.5 * math.atan2(2 * cov[pi][pj], cov[pj][pj] - cov[pi][pi])
        cs, sn = math.cos(th), math.sin(th)
        for k in range(3):                                   # rotate columns pi, pj
            a, b = cov[k][pi], cov[k][pj]
            cov[k][pi], cov[k][pj] = cs * a - sn * b, sn * a + cs * b
        for k in range(3):                                   # rotate rows pi, pj
            a, b = cov[pi][k], cov[pj][k]
            cov[pi][k], cov[pj][k] = cs * a - sn * b, sn * a + cs * b
        for k in range(3):
            a, b = v[k][pi], v[k][pj]
            v[k][pi], v[k][pj] = cs * a - sn * b, sn * a + cs * b
    ev = [cov[i][i] for i in range(3)]
    i = ev.index(min(ev))
    return [v[k][i] for k in range(3)], c


def r3(v): return [round(x, 3) for x in v]


# ---------------------------------------------------------------- main
def main():
    if len(sys.argv) < 3: sys.exit(__doc__)
    path, out = sys.argv[1], sys.argv[2]
    g = G.read_glb(path)
    data, binoff = read_bin(path)
    nodes, roots = G.analyse(g)

    # 1. scale to metres: pick 1/10/100/1000 so the car is 3..7 m long
    boxes = [n.bbox for n in nodes if n.bbox]
    mn = [min(b[0][k] for b in boxes) for k in range(3)]
    mx = [max(b[1][k] for b in boxes) for k in range(3)]
    length_raw = max(mx[0] - mn[0], mx[2] - mn[2])
    scale = next((s for s in (1, 10, 100, 1000, 10000) if 3.0 <= length_raw * s <= 7.0), None)
    if scale is None: sys.exit("cannot find a metre scale (length %.5f)" % length_raw)
    print("model scale to metres: x%d   (length %.3f m, height %.3f m, width incl. mirrors %.3f m)" %
          (scale, (mx[2] - mn[2]) * scale, (mx[1] - mn[1]) * scale, (mx[0] - mn[0]) * scale))

    def match(n, corner):
        low = n.name.lower()
        return (('_%s_' % corner) in low and
                any(w in low for w in ('rim', 'tire', 'tyre', 'wheel', 'brake', 'rotor', 'calip', 'callip')))

    def top_only(corner):
        res = []
        for n in nodes:
            if match(n, corner) and n.tree_bbox and not (n.parent and match(n.parent, corner)):
                res.append(n)
        return res

    groups = {c: top_only(c) for c in ('fl', 'fr', 'rl', 'rr')}
    for c, v in groups.items():
        if not v: sys.exit("no nodes found for wheel corner %s" % c)

    def tyre_centre(c):
        t = [n for n in groups[c] if 'tire' in n.name.lower() or 'tyre' in n.name.lower()]
        if not t: sys.exit("no tyre node for %s" % c)
        b = t[0].tree_bbox
        return [(b[0][k] + b[1][k]) / 2 * scale for k in range(3)], (b[1][1] - b[0][1]) * scale / 2

    ctr = {c: tyre_centre(c)[0] for c in groups}
    radius = tyre_centre('fl')[1]
    ground = min(ctr[c][1] for c in ctr) - radius

    # 2. orientation (front = side of the FL/FR wheels along the length axis)
    z_front = (ctr['fl'][2] + ctr['fr'][2]) / 2
    z_rear = (ctr['rl'][2] + ctr['rr'][2]) / 2
    fwd_sign = 1 if z_front > z_rear else -1
    left_sign = 1 if ctr['fl'][0] > ctr['fr'][0] else -1
    fwd_name = '+Z' if fwd_sign > 0 else '-Z'
    left_name = '+X' if left_sign > 0 else '-X'
    wheelbase = abs(z_front - z_rear)
    track_f = abs(ctr['fl'][0] - ctr['fr'][0])
    track_r = abs(ctr['rl'][0] - ctr['rr'][0])
    origin = [(ctr['rl'][0] + ctr['rr'][0]) / 2, ground, z_rear]
    print("front = %s, left = %s   wheelbase %.3f m, front track %.3f m, rear track %.3f m, tyre radius %.3f m" %
          (fwd_name, left_name, wheelbase, track_f, track_r, radius))

    # 3. steering wheel (all Int_SW_* meshes rotate together around the column axis)
    sw_re = re.compile(sys.argv[3] if len(sys.argv) > 3 else r'Int_SW_', re.I)
    sw = [n for n in nodes if sw_re.search(n.name) and n.tree_bbox and not (n.parent and sw_re.search(n.parent.name))]
    if not sw: sys.exit("no steering-wheel nodes (regex %s)" % sw_re.pattern)
    ring = max(sw, key=lambda n: max(n.tree_bbox[1][k] - n.tree_bbox[0][k] for k in range(3)))
    mesh_node = ring if 'mesh' in ring.raw else nodes[ring.children[0]]
    pts = mesh_points(g, data, binoff, mesh_node, scale)
    axis, cen = smallest_axis(pts)
    if axis[2] * fwd_sign > 0: axis = [-a for a in axis]          # point TOWARD the driver (rearward)
    # ring centre = middle of the in-plane extent, mean along the column
    u = [1, 0, 0] if abs(axis[0]) < 0.9 else [0, 1, 0]
    e1 = [u[1] * axis[2] - u[2] * axis[1], u[2] * axis[0] - u[0] * axis[2], u[0] * axis[1] - u[1] * axis[0]]
    l1 = math.sqrt(sum(x * x for x in e1)); e1 = [x / l1 for x in e1]
    e2 = [axis[1] * e1[2] - axis[2] * e1[1], axis[2] * e1[0] - axis[0] * e1[2], axis[0] * e1[1] - axis[1] * e1[0]]
    def proj(p, e): return sum((p[k] - cen[k]) * e[k] for k in range(3))
    a1 = [proj(p, e1) for p in pts]; a2 = [proj(p, e2) for p in pts]
    mid1 = (max(a1) + min(a1)) / 2; mid2 = (max(a2) + min(a2)) / 2
    sw_pivot = [cen[k] + e1[k] * mid1 + e2[k] * mid2 for k in range(3)]
    diam = max(max(a1) - min(a1), max(a2) - min(a2))
    print("steering wheel: %d nodes, diameter %.3f m, pivot %s, column axis %s" %
          (len(sw), diam, r3(sw_pivot), r3(axis)))

    # 4. write the profile
    def wheel_binding(c, source):
        return {"nodes": [n.name for n in groups[c]], "node": tyre_node(groups[c]),
                "pivotM": r3([ctr[c][0], ctr[c][1], ctr[c][2]]),
                "source": source, "axis": [0, 1, 0], "sign": 1, "offsetDeg": 0, "minDeg": -45, "maxDeg": 45}

    def tyre_node(lst):
        for n in lst:
            if 'tire' in n.name.lower() or 'tyre' in n.name.lower(): return n.name
        return lst[0].name

    prof = {
        "_comment": "Generated by tools/make_profile.py from the GLB. All coordinates: scene-world frame in METRES "
                    "(glTF world space x model.unitScaleToMetres). To rotate a part: translate to pivotM, rotate "
                    "angleDeg about axis (right-hand rule), translate back; apply to EVERY node in 'nodes'. "
                    "Positive angle = LEFT turn. If a part turns the wrong way flip 'sign'.",
        "profileName": os.path.basename(path).rsplit('.', 1)[0].replace('_', '-'),
        "model": {
            "file": os.path.basename(path),
            "unitScaleToMetres": scale,
            "pivotFrame": "scene-world-metres",
            "frame": {"forward": fwd_name, "left": left_name, "up": "+Y"},
            "rearAxleOriginM": r3(origin),
            "sizeM": {"length": round((mx[2] - mn[2]) * scale, 3), "height": round((mx[1] - mn[1]) * scale, 3),
                      "widthInclMirrors": round((mx[0] - mn[0]) * scale, 3)}
        },
        "vehicle": {"wheelbaseM": round(wheelbase, 3), "trackWidthM": round(track_f, 3),
                    "vehicleWidthM": 1.789, "steeringRatio": 15.0, "maxSteeringWheelDeg": 540,
                    "tyreRadiusM": round(radius, 3)},
        "steering": {"deadbandDeg": 0.5, "heartbeatMs": 1000, "pathLengthM": 5.0, "pathPoints": 12},
        "zones": {"dangerM": 0.5, "warningM": 1.0, "maxRangeM": 2.5, "hysteresisM": 0.05, "staleMs": 500},
        "colors": {"DANGER": "#FF3B30", "WARNING": "#FFCC00", "SAFE": "#34C759", "NONE": "#9E9E9E"},
        "bindings": {
            "steeringWheel": {"nodes": [n.name for n in sw], "node": ring.name,
                              "pivotM": r3(sw_pivot), "source": "steeringWheelDeg",
                              "axis": r3(axis), "sign": 1, "offsetDeg": 0, "minDeg": -540, "maxDeg": 540},
            "wheelFrontLeft": wheel_binding('fl', 'leftWheelDeg'),
            "wheelFrontRight": wheel_binding('fr', 'rightWheelDeg')
        },
        "panoramaMap": {}
    }
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(prof, f, indent=2)
    print("written", out)
    print("CHECK BY HAND: vehicle.vehicleWidthM (spec value, mirrors excluded), steeringRatio and "
          "maxSteeringWheelDeg (take from the VW data sheet / measure on the car).")


if __name__ == '__main__':
    main()

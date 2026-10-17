#!/usr/bin/env python3
"""
glb_inspect.py - read a .glb (binary glTF) and print what WP-5 needs:
  * the node tree (names, mesh yes/no)             -> node names for vehicle-profile.json
  * wheel / steering-wheel candidates + pivot check -> which node to rotate
  * overall size, wheelbase, track width, rear-axle origin (from the model itself)
  * optional starter profile:  --write-profile vehicle-profile.json

Only needs Python 3 (standard library). Usage:
    python tools/glb_inspect.py 2025_volkswagen_golf_r.glb
    python tools/glb_inspect.py 2025_volkswagen_golf_r.glb --write-profile vehicle-profile.json
    python tools/glb_inspect.py 2025_volkswagen_golf_r.glb --all      (print every node, not only the first 400)
"""
import json, math, struct, sys

# ------------------------------------------------------------------ GLB reading
def read_glb(path):
    with open(path, 'rb') as f:
        data = f.read()
    if data[:4] != b'glTF':
        sys.exit("Not a .glb file (missing 'glTF' header). If it is a .gltf, convert or open the JSON directly.")
    off = 12
    while off + 8 <= len(data):
        clen, ctype = struct.unpack_from('<II', data, off)
        if ctype == 0x4E4F534A:                      # 'JSON'
            return json.loads(data[off + 8: off + 8 + clen].decode('utf-8'))
        off += 8 + clen
    sys.exit("No JSON chunk found in the .glb")

# ------------------------------------------------------------------ 4x4 matrix helpers (column-major like glTF)
def ident():
    return [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]

def mul(a, b):                                       # a * b (both column-major)
    r = [0]*16
    for c in range(4):
        for rr in range(4):
            r[c*4+rr] = sum(a[k*4+rr] * b[c*4+k] for k in range(4))
    return r

def trs(t, q, s):
    x, y, z, w = q
    xx, yy, zz = x*x, y*y, z*z
    xy, xz, yz = x*y, x*z, y*z
    wx, wy, wz = w*x, w*y, w*z
    r00, r01, r02 = 1-2*(yy+zz), 2*(xy-wz), 2*(xz+wy)
    r10, r11, r12 = 2*(xy+wz), 1-2*(xx+zz), 2*(yz-wx)
    r20, r21, r22 = 2*(xz-wy), 2*(yz+wx), 1-2*(xx+yy)
    return [r00*s[0], r10*s[0], r20*s[0], 0,
            r01*s[1], r11*s[1], r21*s[1], 0,
            r02*s[2], r12*s[2], r22*s[2], 0,
            t[0], t[1], t[2], 1]

def apply(m, p):
    return [m[0]*p[0]+m[4]*p[1]+m[8]*p[2]+m[12],
            m[1]*p[0]+m[5]*p[1]+m[9]*p[2]+m[13],
            m[2]*p[0]+m[6]*p[1]+m[10]*p[2]+m[14]]

def local_matrix(n):
    if 'matrix' in n:
        return list(n['matrix'])
    return trs(n.get('translation', [0,0,0]), n.get('rotation', [0,0,0,1]), n.get('scale', [1,1,1]))

# ------------------------------------------------------------------ scene analysis
class Node:
    def __init__(self, idx, raw):
        self.idx, self.raw = idx, raw
        self.name = raw.get('name', 'node_%d' % idx)
        self.children = raw.get('children', [])
        self.parent = None
        self.world = ident()
        self.bbox = None          # own mesh bbox in world space: (min[3], max[3])
        self.tree_bbox = None     # incl. descendants

def analyse(g):
    nodes = [Node(i, n) for i, n in enumerate(g.get('nodes', []))]
    for n in nodes:
        for c in n.children:
            nodes[c].parent = n
    roots = []
    scenes = g.get('scenes', [])
    if scenes:
        roots = scenes[g.get('scene', 0)].get('nodes', [])
    else:
        roots = [n.idx for n in nodes if n.parent is None]

    def walk(i, parent_world):
        n = nodes[i]
        n.world = mul(parent_world, local_matrix(n.raw))
        if 'mesh' in n.raw:
            n.bbox = mesh_bbox(g, g['meshes'][n.raw['mesh']], n.world)
        for c in n.children:
            walk(c, n.world)
    for r in roots:
        walk(r, ident())

    def tree_box(n):
        boxes = [n.bbox] if n.bbox else []
        for c in n.children:
            b = tree_box(nodes[c])
            if b: boxes.append(b)
        if not boxes:
            n.tree_bbox = None
            return None
        mn = [min(b[0][k] for b in boxes) for k in range(3)]
        mx = [max(b[1][k] for b in boxes) for k in range(3)]
        n.tree_bbox = (mn, mx)
        return n.tree_bbox
    for r in roots:
        tree_box(nodes[r])
    return nodes, roots

def mesh_bbox(g, mesh, world):
    mn, mx = [1e30]*3, [-1e30]*3
    ok = False
    for prim in mesh.get('primitives', []):
        ai = prim.get('attributes', {}).get('POSITION')
        if ai is None: continue
        a = g['accessors'][ai]
        if 'min' not in a or 'max' not in a: continue
        lo, hi = a['min'], a['max']
        for cx in (lo[0], hi[0]):
            for cy in (lo[1], hi[1]):
                for cz in (lo[2], hi[2]):
                    p = apply(world, (cx, cy, cz))
                    for k in range(3):
                        mn[k] = min(mn[k], p[k]); mx[k] = max(mx[k], p[k])
                    ok = True
    return (mn, mx) if ok else None

def center(box): return [(box[0][k] + box[1][k]) / 2 for k in range(3)]
def fmt(v): return '[' + ', '.join('%.3f' % x for x in v) + ']'

WHEEL_WORDS = ('wheel', 'tire', 'tyre', 'rim', 'felge', 'reifen', 'rad_', 'hub')
STEER_WORDS = ('steering', 'lenkrad', 'steer')

def is_wheelish(name):
    n = name.lower()
    return any(w in n for w in WHEEL_WORDS) and not any(s in n for s in ('steering', 'lenk'))

def is_steering_wheel(name):
    n = name.lower()
    return (any(s in n for s in STEER_WORDS) and ('wheel' in n or 'lenkrad' in n or 'rad' in n)) or 'lenkrad' in n

def corner_of(name, c):
    """Guess FL/FR/RL/RR from the name. Returns None if unclear."""
    n = name.lower().replace('-', '_').replace('.', '_').replace(' ', '_')
    toks = set(n.split('_'))
    for tag in ('fl', 'fr', 'rl', 'rr', 'lf', 'rf', 'lr', 'rr'):
        if tag in toks:
            return {'lf': 'fl', 'rf': 'fr', 'lr': 'rl'}.get(tag, tag)
    f = 'front' in n; r = 'rear' in n or 'back' in n
    l = 'left' in n; rt = 'right' in n
    if (f or r) and (l or rt):
        return ('f' if f else 'r') + ('l' if l else 'r')
    return None

# ------------------------------------------------------------------ main
def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not args:
        sys.exit(__doc__)
    path = args[0]
    write = None
    if '--write-profile' in sys.argv:
        write = sys.argv[sys.argv.index('--write-profile') + 1]
    show_all = '--all' in sys.argv

    g = read_glb(path)
    nodes, roots = analyse(g)
    print("File: %s   nodes: %d   meshes: %d   (glTF: Y is UP, the car normally faces +Z, its LEFT side is +X)" %
          (path, len(nodes), len(g.get('meshes', []))))

    # ---- tree
    print("\n=== NODE TREE (M = has mesh) ===")
    count = [0]
    def pr(i, d):
        if count[0] >= 400 and not show_all:
            return
        n = nodes[i]; count[0] += 1
        print("%s%s%s  (#%d)" % ('  ' * d, n.name, '  [M]' if 'mesh' in n.raw else '', i))
        for c in n.children: pr(c, d + 1)
    for r in roots: pr(r, 0)
    if count[0] >= 400 and not show_all:
        print("... (stopped at 400 lines, use --all to print everything)")

    # ---- overall size
    boxes = [n.bbox for n in nodes if n.bbox]
    if boxes:
        mn = [min(b[0][k] for b in boxes) for k in range(3)]
        mx = [max(b[1][k] for b in boxes) for k in range(3)]
        size = [mx[k] - mn[k] for k in range(3)]
        print("\n=== MODEL SIZE (in model units; check they are metres!) ===")
        print("X: %.3f   Y: %.3f   Z: %.3f      (min %s  max %s)" % (size[0], size[1], size[2], fmt(mn), fmt(mx)))
        horiz = {'X': size[0], 'Z': size[2]}
        longest = max(horiz, key=horiz.get)
        print("Longest horizontal axis = %s -> that is the car's length axis (width axis is the other one)." % longest)
        if max(size) > 20:
            print("WARNING: the model is > 20 units long. Probably millimetres or centimetres - scale before use.")
        if size[1] > size[0] and size[1] > size[2]:
            print("WARNING: height is the largest dimension. The model may be Z-up or rotated; check 'model.frame' in the profile.")

    # ---- metre scale (Sketchfab/FBX exports are often 100x too small or too big)
    unit = 1.0
    if boxes:
        L = max(size[0], size[2])
        unit = next((s for s in (1, 10, 100, 1000, 10000) if 3.0 <= L * s <= 7.0), 1.0)
        if unit != 1.0:
            print("NOTE: multiply every coordinate below by %g to get metres (car length is %.3f units = %.3f m)." % (unit, L, L * unit))
            print("      Run  python tools/make_profile.py <file.glb> vehicle-profile.json  to get a finished profile in metres.")

    # ---- steering wheel
    print("\n=== STEERING WHEEL candidates ===")
    sw = [n for n in nodes if is_steering_wheel(n.name)]
    for n in sw:
        print("  %-40s world origin %s %s" % (n.name, fmt([n.world[12], n.world[13], n.world[14]]),
              '[M]' if 'mesh' in n.raw else ''))
    if not sw:
        print("  none found by name. Search the tree above for the node that holds the steering wheel mesh.")

    # ---- wheels
    print("\n=== WHEEL candidates (tree-bbox centre = true wheel centre) ===")
    cands = []
    for n in nodes:
        if is_wheelish(n.name) and n.tree_bbox:
            # keep only the top-most wheelish node of a branch
            p, top = n.parent, True
            while p is not None:
                if is_wheelish(p.name) and p.tree_bbox: top = False; break
                p = p.parent
            if top: cands.append(n)
    wheel_pts = []
    for n in cands:
        c = center(n.tree_bbox)
        org = [n.world[12], n.world[13], n.world[14]]
        off = math.dist(c, org) * unit
        sz = [n.tree_bbox[1][k] - n.tree_bbox[0][k] for k in range(3)]
        tag = corner_of(n.name, c)
        print("  %-36s centre %s  size %s  corner:%s" % (n.name, fmt(c), fmt(sz), tag or '?'))
        print("  %-36s node origin %s  pivot offset %.3f m  %s" %
              ('', fmt(org), off,
               'OK (origin ~ wheel centre)' if off < 0.03 else '<-- origin is NOT at the wheel centre (%.2f m away): rotate around the pivot point (pivotM in the profile), not around the node origin' % off))
        wheel_pts.append((n, c, tag))

    # ---- geometry
    origin_hint = None
    geo = {}
    big = [(n, c, t) for (n, c, t) in wheel_pts if max(n.tree_bbox[1][k] - n.tree_bbox[0][k] for k in range(3)) < 1.2]
    if len(big) >= 4 and boxes:
        horiz = {'X': size[0], 'Z': size[2]}
        longest = max(horiz, key=horiz.get)
        fi = 0 if longest == 'X' else 2
        li = 2 if fi == 0 else 0
        fwd = sorted(c[fi] for _, c, _ in big)
        lat = sorted(c[li] for _, c, _ in big)
        # cluster into 2 groups by the largest gap
        def split(vals):
            gaps = [(vals[i+1] - vals[i], i) for i in range(len(vals) - 1)]
            _, k = max(gaps)
            a, b = vals[:k+1], vals[k+1:]
            return sum(a) / len(a), sum(b) / len(b)
        f_lo, f_hi = split(fwd); l_lo, l_hi = split(lat)
        wheelbase = abs(f_hi - f_lo); track = abs(l_hi - l_lo)
        ground = min(mn[1], min(c[1] for _, c, _ in big) - 0)  # lowest point of the model
        print("\n=== GEOMETRY FROM YOUR MODEL ===")
        print("wheelbaseM  = %.3f" % wheelbase)
        print("trackWidthM = %.3f   (distance between left and right wheel centres)" % track)
        print("vehicleWidthM ~ %.3f (model width incl. mirrors/wheels: use body width without mirrors if you want exact lines)" % size[li])
        geo = {'wheelbaseM': round(wheelbase, 3), 'trackWidthM': round(track, 3), 'vehicleWidthM': round(size[li], 3)}
        rear_f = f_lo  # assumes the front of the car is +%s (glTF convention)
        o = [0, ground, 0]
        o[fi] = rear_f
        o[li] = (l_lo + l_hi) / 2
        origin_hint = o
        print("rearAxleOriginM (assuming the car FRONT is +%s, glTF standard) = %s" % ('Z' if fi == 2 else 'X', fmt(o)))
        o2 = list(o); o2[fi] = f_hi
        print("   if your car front is -%s instead, use %s" % ('Z' if fi == 2 else 'X', fmt(o2)))
    else:
        print("\n(Could not derive wheelbase/track: need 4 separate wheel nodes with meshes. Measure by hand or read them from the VW data sheet.)")

    print("\n=== NEXT STEPS ===")
    print(" 1. For each front wheel pick the node that (a) contains tyre+rim and (b) has its origin at the wheel centre.")
    print("    That is the node Android rotates around the UP axis (steer), NOT the node that spins the wheel.")
    print(" 2. Copy the node names into vehicle-profile.json -> bindings.")
    print(" 3. Run the middleware, look at the model in Android, flip \"sign\" if a part turns the wrong way.")

    if write:
        write_profile(write, path, sw, cands, wheel_pts, origin_hint, g, geo)
        print("\nStarter profile written to %s - open it and check every 'TODO'." % write)

def write_profile(out, glb, sw, cands, wheel_pts, origin, g, geo):
    def pick(corner):
        for n, c, t in wheel_pts:
            if t == corner: return n.name
        return 'TODO_%s_steer_node' % corner.upper()
    prof = {
        "_comment": "Generated by tools/glb_inspect.py. Every TODO must be checked against the model. Rotations are applied as restRotation * quat.",
        "profileName": glb.split('/')[-1].split('\\')[-1].replace('.glb', ''),
        "model": {
            "file": glb.split('/')[-1].split('\\')[-1],
            "frame": {"forward": "+Z", "left": "+X", "up": "+Y"},
            "rearAxleOriginM": [round(v, 3) for v in origin] if origin else [0, 0, 0]
        },
        "vehicle": {"wheelbaseM": geo.get('wheelbaseM', 2.63), "trackWidthM": geo.get('trackWidthM', 1.54),
                    "vehicleWidthM": geo.get('vehicleWidthM', 1.79),
                    "steeringRatio": 15.0, "maxSteeringWheelDeg": 540},
        "steering": {"deadbandDeg": 0.5, "heartbeatMs": 1000, "pathLengthM": 5.0, "pathPoints": 12},
        "zones": {"dangerM": 0.5, "warningM": 1.0, "maxRangeM": 2.5, "hysteresisM": 0.05, "staleMs": 500},
        "colors": {"DANGER": "#FF3B30", "WARNING": "#FFCC00", "SAFE": "#34C759", "NONE": "#9E9E9E"},
        "bindings": {
            "steeringWheel":   {"node": sw[0].name if sw else "TODO_steering_wheel_node",
                                "source": "steeringWheelDeg", "axis": [0, 0, -1], "sign": 1, "minDeg": -540, "maxDeg": 540},
            "wheelFrontLeft":  {"node": pick('fl'), "source": "leftWheelDeg",  "axis": [0, 1, 0], "sign": 1, "minDeg": -45, "maxDeg": 45},
            "wheelFrontRight": {"node": pick('fr'), "source": "rightWheelDeg", "axis": [0, 1, 0], "sign": 1, "minDeg": -45, "maxDeg": 45}
        },
        "panoramaMap": {}
    }
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(prof, f, indent=2)

if __name__ == '__main__':
    main()

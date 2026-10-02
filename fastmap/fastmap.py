#!/usr/bin/env python3
"""Fast Rust map render without starting RustDedicated.

Reads a .map file and reproduces `world.rendermap` (MapImageRenderer.Render, scale 1,
oceanMargin 500) plus monument labels compatible with MapLabels.json and LootSpawns.json
(crates, barrels and ore nodes placed in the map file, plus player spawn points
computed like SpawnHandler.GetSpawnPoint).

usage: fastmap.py MAP_FILE OUT_DIR [--labels-cs MapLabels.cs] [--font font.ttf]
"""
import argparse, hashlib, itertools, json, math, os, re, struct, sys, time

import cv2
import lz4.block
import numpy as np

T0 = time.time()


def log(*a):
    print(f'[{time.time() - T0:6.2f}s]', *a, flush=True)


# ---------------------------------------------------------------- .map reading
def _varint(b, i):
    r = s = 0
    while True:
        x = b[i]; i += 1; r |= (x & 0x7F) << s; s += 7
        if x < 0x80:
            return r, i


def read_map(path):
    """uint32 version + int64 timestamp + LZ4Stream(lz4net) wrapped protobuf WorldData."""
    raw = open(path, 'rb').read()
    if len(raw) < 16:
        raise ValueError('map file too small')
    version, _ = struct.unpack_from('<Iq', raw, 0)
    i, parts = 12, []
    while i < len(raw):
        flags, i = _varint(raw, i)
        orig, i = _varint(raw, i)
        comp = orig
        if flags & 1:
            comp, i = _varint(raw, i)
        chunk = raw[i:i + comp]; i += comp
        parts.append(lz4.block.decompress(chunk, uncompressed_size=orig) if flags & 1 else chunk)
    return version, b''.join(parts)


def _fields(b, i=0, end=None):
    end = len(b) if end is None else end
    while i < end:
        key, i = _varint(b, i); f, wt = key >> 3, key & 7
        if wt == 0:
            v, i = _varint(b, i)
        elif wt == 2:
            n, i = _varint(b, i); v = (i, i + n); i += n
        elif wt == 5:
            v = b[i:i + 4]; i += 4
        elif wt == 1:
            v = b[i:i + 8]; i += 8
        else:
            raise ValueError(f'unsupported wire type {wt}')
        yield f, wt, v


def _vec(b, s, e):
    out = [0.0, 0.0, 0.0]
    for f, _, v in _fields(b, s, e):
        if 1 <= f <= 3:
            out[f - 1] = struct.unpack('<f', v)[0]
    return out


def parse_world(b):
    w = {'size': 0, 'maps': {}, 'prefabs': []}
    for f, _, v in _fields(b):
        if f == 1:
            w['size'] = v
        elif f == 2:
            name = data = None
            for f2, _, v2 in _fields(b, *v):
                if f2 == 1: name = b[v2[0]:v2[1]].decode('utf-8', 'replace')
                elif f2 == 2: data = v2
            w['maps'][name] = data
        elif f == 3:
            p = {'category': '', 'id': 0, 'pos': [0, 0, 0], 'rot': [0, 0, 0], 'scale': [1, 1, 1]}
            for f2, _, v2 in _fields(b, *v):
                if f2 == 1: p['category'] = b[v2[0]:v2[1]].decode('utf-8', 'replace')
                elif f2 == 2: p['id'] = v2
                elif f2 == 3: p['pos'] = _vec(b, *v2)
                elif f2 == 4: p['rot'] = _vec(b, *v2)
                elif f2 == 5: p['scale'] = _vec(b, *v2)
            w['prefabs'].append(p)
    return w


# ---------------------------------------------------------------- rendering
def _c(*v):
    return np.array(v, dtype=np.float32)


START = _c(0.28627452, 23 / 85, 0.24705884)
WATER = _c(0.16941601, 0.31755757, 0.36200002)
OFFSHORE = _c(0.04090196, 0.22060032, 14 / 51)
# splat channel index (TerrainSplat.TypeToIndex): Dirt0 Snow1 Sand2 Rock3 Grass4 Forest5 Stones6 Gravel7
SPLAT_ORDER = [  # same lerp order as MapImageRenderer
    (7, _c(0.25, 37 / 152, 0.22039475)),          # Gravel
    (6, _c(7 / 51, 0.2784314, 0.2761563)),        # Pebble
    (3, _c(0.4, 0.39379844, 0.37519377)),         # Rock
    (0, _c(0.6, 0.47959462, 0.33)),               # Dirt
    (4, _c(0.35486364, 0.37, 0.2035)),            # Grass
    (5, _c(0.24843751, 0.3, 9 / 128)),            # Forest
    (2, _c(0.7, 0.65968585, 0.5277487)),          # Sand
    (1, _c(0.86274517, 0.9294118, 0.94117653)),   # Snow
]
SUN = _c(0.95, 2.87, 2.37); SUN /= np.linalg.norm(SUN)
SHORT2FLOAT = np.float32(3.051944e-05)
BYTE2FLOAT = np.float32(0.003921569)
TERRAIN_Y, TERRAIN_H = -500.0, 1000.0
GAUSS = np.zeros(13, np.float32)
GAUSS[::2] = [1 / 32, 7 / 64, 7 / 32, 9 / 32, 7 / 32, 7 / 64, 1 / 32]


def _layer(b, w, name, dtype):
    if name not in w['maps'] or w['maps'][name] is None:
        raise ValueError(f'map layer "{name}" is missing')
    s, e = w['maps'][name]
    return np.frombuffer(b, dtype=dtype, count=(e - s) // np.dtype(dtype).itemsize, offset=s)


def _square(a, channels=1):
    res = int(round(math.sqrt(a.size / channels)))
    if res * res * channels != a.size:
        raise ValueError('unexpected layer size')
    return a.reshape((channels, res, res) if channels > 1 else (res, res)), res


def _axis(u, res, clamp_frac):
    """Index/weight arrays for bilinear sampling along one axis (Rust's GetHeight01 style)."""
    f = u * (res - 1)
    tr = np.trunc(f)
    i0 = np.clip(tr.astype(np.int32), 0, res - 1)
    i1 = np.where(f < res - 1, np.minimum(i0 + 1, res - 1), i0)
    t = (f - tr).astype(np.float32)
    if clamp_frac:
        t = np.clip(t, 0, 1)
    return i0, i1, t


def _bil(a, zs, xs):
    (z0, z1, tz), (x0, x1, tx) = zs, xs
    # float32 обязательно: сплат хранится в uint8, и разность p01 - p00 в uint8 переполняется
    # (0 - 255 = 1) — на карте это давало полосы через пиксель и «точки» на границах текстур.
    f = lambda zi, xi: a[np.ix_(zi, xi)].astype(np.float32, copy=False)
    p00 = f(z0, x0); p01 = f(z0, x1)
    p10 = f(z1, x0); p11 = f(z1, x1)
    tx = tx[None, :]; tz = tz[:, None]
    if p00.ndim == 3:
        tx = tx[..., None]; tz = tz[..., None]
    top = p00 + (p01 - p00) * tx
    bot = p10 + (p11 - p10) * tx
    return top + (bot - top) * tz


def _shore_distance(Hm, Wm, hres, size):
    ss = 1 << (int(round(math.log2(hres - 1))) - 1)   # ClosestPowerOfTwo(res) >> 1
    scale = size / ss
    c = ((np.arange(ss) + 0.5) * scale / size * (hres - 1)).astype(np.float32)
    mx, mz = np.meshgrid(c, c)
    th = cv2.remap(Hm, mx, mz, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    wh = np.maximum(0.0, cv2.remap(Wm, mx, mz, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE))
    land = np.maximum(wh - th, 0) <= 0
    edge = np.zeros_like(land)
    inner = land[1:-2, 1:-2]
    diff = ((land[1:-2, :-3] != inner) | (land[1:-2, 2:-1] != inner)
            | (land[:-3, 1:-2] != inner) | (land[2:-1, 1:-2] != inner))
    edge[1:-2, 1:-2] = inner & diff
    if not edge.any():
        return np.full((ss, ss), 10000.0 / scale, np.float32), ss, scale
    d = cv2.distanceTransform((~edge).astype(np.uint8), cv2.DIST_L2, cv2.DIST_MASK_PRECISE).astype(np.float32)
    d[land] *= -1
    d = cv2.sepFilter2D(d, -1, GAUSS, GAUSS, borderType=cv2.BORDER_REPLICATE)
    return d, ss, scale


def render(b, w, scale=1.0, margin=500, band=256):
    size = float(w['size'])
    H, hres = _square(_layer(b, w, 'height', '<i2').astype(np.float32) * SHORT2FLOAT)
    Hm = (TERRAIN_Y + H * TERRAIN_H).astype(np.float32)
    try:
        Wr, _ = _square(_layer(b, w, 'water', '<i2').astype(np.float32) * SHORT2FLOAT)
        Wm = (TERRAIN_Y + Wr * TERRAIN_H).astype(np.float32)
    except ValueError:
        Wm = np.full_like(Hm, TERRAIN_Y)
    SP, sres = _square(_layer(b, w, 'splat', 'u1'), 8)
    TP, tres = _square(_layer(b, w, 'topology', '<i4'))

    # per-vertex normals (TerrainHeightMapJobs.HeightMapData.GetNormal stencil)
    norm_y = size / TERRAIN_H / hres
    Hp = np.pad(H, 1, mode='edge')
    nx = -(Hp[:-2, 2:] - Hp[:-2, :-2]) * 0.5
    nz = -(Hp[2:, :-2] - Hp[:-2, :-2]) * 0.5
    ny = np.full_like(nx, norm_y)
    inv = 1.0 / np.sqrt(nx * nx + ny * ny + nz * nz)
    N = np.stack([nx * inv, ny * inv, nz * inv], -1).astype(np.float32)

    D, ss, dscale = _shore_distance(Hm, Wm, hres, size)

    # GetTopology(x, y, 16f): any river/riverside bit within 16 m
    r = max(1, int(16.0 / size * tres))
    river = cv2.dilate(((TP & 0x180) != 0).astype(np.uint8), np.ones((2 * r + 1, 2 * r + 1), np.uint8))
    log('prepared layers', f'height {hres}, splat {sres}, topology {tres}, shore {ss}')

    map_res = int(size * min(max(scale, 0.1), 4.0))
    W = map_res + 2 * margin
    u = ((np.arange(W) - margin) / map_res).astype(np.float32)
    ax_h = _axis(u, hres, True)
    ax_n = _axis(u, hres, False); ax_n = (ax_n[0], ax_n[1], np.clip(ax_n[2], 0, 1))
    ax_s = _axis(u, sres, False); ax_s = (ax_s[0], ax_s[1], np.clip(ax_s[2], 0, 1))
    ax_d = _axis(u, ss, False)
    it = np.clip((u * tres).astype(np.int32), 0, tres - 1)

    out = np.empty((W, W, 3), np.uint8)
    for y0 in range(0, W, band):
        sl = slice(y0, min(W, y0 + band))
        rows = lambda ax: (ax[0][sl], ax[1][sl], ax[2][sl])
        height = TERRAIN_Y + _bil(H, rows(ax_h), ax_h) * TERRAIN_H
        nrm = _bil(N, rows(ax_n), ax_n)
        nrm /= np.linalg.norm(nrm, axis=-1, keepdims=True)
        shore = _bil(D, rows(ax_d), ax_d) * dscale
        is_river = river[np.ix_(it[sl], it)] != 0

        col = np.broadcast_to(START, height.shape + (3,)).copy()
        for ch, colour in SPLAT_ORDER:
            s = _bil(SP[ch], rows(ax_s), ax_s).astype(np.float32) * BYTE2FLOAT
            col += (colour - col) * s[..., None]

        neg_h = -height
        depth = np.where(shore > 0,
                         np.where((neg_h <= 0) | ~is_river, np.maximum(neg_h, 0.1 * shore), neg_h),
                         0.0)
        a1 = np.clip(0.5 + depth / 5.0, 0, 1)[..., None]
        a2 = np.clip(depth / 50.0, 0, 1)[..., None]
        wet = col + (WATER - col) * a1
        wet = wet + (OFFSHORE - wet) * a2
        lam = np.maximum((nrm * SUN).sum(-1), 0)[..., None]
        dry = col + (lam - 0.5) * 0.65 * col
        dry = (dry - 0.5) * 0.94 + 0.5
        col = np.where((depth > 0)[..., None], wet, dry) * 1.05
        out[sl] = np.clip(col * 255.0 + 0.5, 0, 255).astype(np.uint8)
    return out[::-1]  # Unity texture row 0 is the bottom of the PNG


# ---------------------------------------------------------------- labels
MONUMENT_ROOT = 'assets/bundled/prefabs/autospawn/monument/'
MONUMENT_DIRS = ('large medium small xlarge harbor fishing_village lighthouse offshore roadside swamp cave tiny '
                 'arctic_bases military_bases ice_lakes lakes jungle railside mountain power_substations '
                 'water_wells underwater_lab train_tunnel_entrances tunnel_entrance desert').split()
EXTRA_NAMES = {  # monuments not listed in MapLabels.cs; names follow MonumentFinder aliases
    'nuclear_missile_silo': 'Missile Silo', 'military_tunnel_1': 'Military Tunnel',
    'trainyard_1': 'Train Yard', 'powerplant_1': 'Power Plant', 'water_treatment_plant_1': 'Water Treatment Plant',
    'radtown_small_3': 'Sewer Branch', 'desert_military_base_d': 'Desert Military Base',
    'cave_large_medium': 'Cave', 'cave_large_hard': 'Cave', 'cave_medium_easy': 'Cave', 'cave_medium_hard': 'Cave',
    'ice_lake_3': 'Ice Lake', 'ice_lake_4': 'Ice Lake', 'abandoned_military_base_a': 'Abandoned Military Base',
}
SKIP_PARTS = ('module_', 'tube_', 'moonpool_', 'prevent_building')
# RustEdit custom monuments: a monument_marker prefab whose category holds the monument name.
# MonumentFinder reports them with ShortName = PrefabName = that name.
MONUMENT_MARKER = 'assets/bundled/prefabs/modding/volumes_and_triggers/monument_marker.prefab'


def manifest_hash(s):
    """UnityEngine.StringEx.ManifestHash: first 4 bytes of MD5(lowercase utf-8) as uint32 LE."""
    return struct.unpack('<I', hashlib.md5(s.lower().encode('utf-8')).digest()[:4])[0]


def read_label_config(path):
    names, hidden, offsets = {}, set(), {}
    if path and os.path.exists(path):
        src = open(path, encoding='utf-8-sig').read()

        def block(field):
            m = re.search(field + r'\s*=\s*new[^{]*\{(.*?)\n\s*\};', src, re.S)
            return re.sub(r'//[^\n]*', '', m.group(1)) if m else ''

        names = dict(re.findall(r'\["([^"]+)"\]\s*=\s*"([^"]*)"', block('MonumentDisplayNames')))
        hidden = set(re.findall(r'"([^"]+)"', block('HiddenMonuments')))
        for k, x, y, z in re.findall(r'\["([^"]+)"\]\s*=\s*new Vector3\(\s*([-\d.]+)f?\s*,\s*([-\d.]+)f?\s*,\s*([-\d.]+)f?\s*\)',
                                     block('MonumentOffsets')):
            offsets[k] = (float(x), float(y), float(z))
    return ({k.lower(): v for k, v in names.items()}, {h.lower() for h in hidden},
            {k.lower(): v for k, v in offsets.items()})


def _transform_point(pos, rot_deg, scale, off):
    """Unity Transform.TransformPoint with Euler rotation (Z, X, Y order)."""
    x, y, z = (off[i] * scale[i] for i in range(3))
    rx, ry, rz = (math.radians(a) for a in rot_deg)
    x, y = x * math.cos(rz) - y * math.sin(rz), x * math.sin(rz) + y * math.cos(rz)
    y, z = y * math.cos(rx) - z * math.sin(rx), y * math.sin(rx) + z * math.cos(rx)
    x, z = x * math.cos(ry) + z * math.sin(ry), -x * math.sin(ry) + z * math.cos(ry)
    return pos[0] + x, pos[1] + y, pos[2] + z


def build_labels(w, cfg):
    names, hidden, offsets = cfg
    short_names = set(names) | hidden | set(offsets) | set(EXTRA_NAMES)
    table = {}
    for d, n in itertools.product(MONUMENT_DIRS, short_names):
        p = f'{MONUMENT_ROOT}{d}/{n}.prefab'
        table[manifest_hash(p)] = (p, n)
    labels = []
    marker_id = manifest_hash(MONUMENT_MARKER)
    for pf in w['prefabs']:
        if pf['id'] == marker_id:
            name = (pf['category'] or '').strip()
            if not name or any(s in name for s in SKIP_PARTS):
                continue
            key = name.lower()
            labels.append({'Name': names.get(key) or name, 'X': round(pf['pos'][0], 3), 'Z': round(pf['pos'][2], 3),
                           'IsCustom': False, 'ShortName': name, 'PrefabName': name,
                           'HideOnMap': key in hidden})
            continue
        hit = table.get(pf['id'])
        if not hit:
            continue
        prefab, short = hit
        if any(s in short for s in SKIP_PARTS) or short.startswith('train_tunnel_double_entrance'):
            continue
        display = names.get(short) or EXTRA_NAMES.get(short) or short
        pos = pf['pos']
        if short in offsets:
            pos = _transform_point(pos, pf['rot'], pf['scale'], offsets[short])
        labels.append({'Name': display, 'X': round(pos[0], 3), 'Z': round(pos[2], 3), 'IsCustom': False,
                       'ShortName': short, 'PrefabName': prefab, 'HideOnMap': short in hidden})
    labels.sort(key=lambda l: (l['ShortName'], l['X'], l['Z']))
    return labels


def draw_labels(img, labels, font_path):
    from PIL import Image, ImageDraw, ImageFont
    im = Image.fromarray(img)
    h, w_ = img.shape[:2]
    # GDI+: Font(family, max(14, imgW/180)) in points at 96 dpi
    px = max(14.0, w_ / 180.0) * 96 / 72
    if font_path:
        # тот же Permanent Marker, что у main.yml; без него — ошибка, а не тихая подмена шрифта
        font = ImageFont.truetype(font_path, int(round(px)))
    else:
        print('WARNING: --font not given, using the default font', flush=True)
        font = ImageFont.load_default(px)
    dr = ImageDraw.Draw(im)
    for lb in labels:
        if lb['HideOnMap']:
            continue
        dr.text((lb['X'] + w_ / 2.0, h / 2.0 - lb['Z']), lb['Name'], font=font, fill=(0, 0, 0), anchor='mm')
    return np.asarray(im)


# ---------------------------------------------------------------- loot
# Ящики, бочки, руда и спавнеры, которые автор карты поставил в RustEdit, лежат в .map обычными префабами.
# Список путей сверен с GameManifest текущей версии игры (assets/manifest.asset из content.bundle).
# Спавнеры (modding/lootables/*_spawner, underwater_labs/spawners/*) сами ничего не содержат: сервер ставит
# на их место ящик/бочку/руду при старте — поэтому на карте они показываются тем, что спавнят.
# Типы и названия — как у LootSpawns.cs (полный рендер), чтобы просмотрщик показывал их одинаково.
# Лут внутри стандартных монументов в .map не хранится (он внутри префаба монумента) — его даёт только полный режим.
_P = 'assets/bundled/prefabs/'
LOOT_PREFABS = [_P + 'radtown/' + n + '.prefab' for n in (
    'crate_basic crate_basic_jungle crate_cannons crate_elite crate_mine crate_normal crate_normal_2 '
    'crate_normal_2_food crate_normal_2_medical crate_shore crate_tools crate_underwater_advanced '
    'crate_underwater_basic desk_bluecard desk_greencard desk_redcard foodbox loot_barrel_1 loot_barrel_2 '
    'loot_trash minecart oil_barrel ore_metal ore_stone ore_sulfur vehicle_parts vehicle_parts_advanced').split()]
LOOT_PREFABS += [_P + 'radtown/underwater_labs/' + n + '.prefab' for n in (
    'crate_ammunition crate_elite crate_food_1 crate_food_2 crate_fuel crate_medical crate_normal crate_normal_2 '
    'crate_tools tech_parts_1 tech_parts_2 vehicle_parts').split()]
LOOT_PREFABS += [_P + 'radtown/underwater_labs/spawners/spawner_' + n + '.prefab' for n in (
    'ammo_crate card_blue card_green card_red crate_normal_old_style elite_crate food_crates fuel medical_crate '
    'normal_crates oil_barrel tech_parts_crate tools_crate vehicle_parts_crate').split()]
LOOT_PREFABS += [_P + 'modding/lootables/invisible/invisible_lootable_prefabs/invisible_' + n + '.prefab' for n in (
    'crate_basic crate_elite crate_normal crate_normal_2 crate_normal_2_food crate_normal_2_medical crate_tools '
    'foodbox vehicle_parts').split()]
LOOT_PREFABS += [_P + 'modding/lootables/invisible/invisible_' + n + '_spawner.prefab' for n in (
    'crate_basic crate_elite crate_food crate_medical crate_normal_2 crate_normal crate_tools foodbox '
    'vehicle_parts').split()]
LOOT_PREFABS += [_P + 'modding/lootables/' + n + '.prefab' for n in (
    'barrel_spawner basic_crate_spawner blue_card_spawner codelockedhackablecrate_min_build_radius '
    'codelockedhackablecrate_no_build_radius crate_normal_2_spawner crate_normal_random_spawner '
    'crate_normal_spawner diesel_barrel_spawner elite_crate_spawner food_crate_spawner green_card_spawner '
    'med_crate_spawner mine_cart_spawner mine_crate_spawner oil_barrel_spawner ore_hqm_spawner ore_metal_spawner '
    'ore_metal_spawner_small ore_random_spawner ore_random_spawner_small ore_stone_spawner ore_stone_spawner_small '
    'ore_sulfur_spawner ore_sulfur_spawner_small red_card_spawner tool_crate_spawner '
    'underwater_advanced_crate_spawner underwater_basic_crate_spawner vehicle_parts_crate_spawner').split()]
LOOT_PREFABS += [_P + 'modding/lootables/food_box_spawner .prefab']  # пробел перед .prefab — так в игре
LOOT_PREFABS += [_P + f'modding/lootables/nonspacecheckingspawners/ore_{o}_spawner{s}_nsc.prefab'
                 for o in ('metal', 'random', 'stone', 'sulfur') for s in ('', '_small')]
LOOT_PREFABS += [_P + 'autospawn/resource/loot/' + n + '.prefab' for n in ('loot-barrel-1', 'loot-barrel-2', 'trash-pile-1')]
LOOT_PREFABS += [_P + f'autospawn/resource/{d}/{o}-ore.prefab'
                 for d in ('ores', 'ores_sand', 'ores_snow') for o in ('stone', 'metal', 'sulfur')]
LOOT_PREFABS += [_P + 'autospawn/resource/ore_hqm/hqm-ore.prefab',
                 _P + 'modding/asset_store/hiddenhackablecrate.prefab',
                 'assets/prefabs/deployable/chinooklockedcrate/codelockedhackablecrate.prefab',
                 'assets/content/structures/excavator/prefabs/diesel_collectable.prefab']
LOOT_PREFABS += [f'assets/prefabs/tools/keycard/keycard_{c}_pickup.entity.prefab' for c in ('green', 'blue', 'red')]


def _short(path):
    return path.rsplit('/', 1)[1][:-len('.prefab')].strip()


def loot_type(short):
    """Те же типы и правила, что у LootSpawns.GetSpawnType/GetOreType (полный рендер).
    Спавнеры RustEdit (elite_crate_spawner, spawner_food_crates и т.п.) сводятся к тому ящику, который они ставят."""
    s = short.lower()
    for c in ('green', 'blue', 'red'):
        if f'{c}_card' in s or f'card_{c}' in s or f'{c}card' in s:
            return c.capitalize() + ' Card'
    if 'crate_elite' in s or 'elite_crate' in s: return 'Elite Crate'
    # crate_normal_2_food / crate_normal_2_medical — это еда и медицина, не военный ящик
    if 'crate_normal_2_food' in s: return 'Food Crate'
    if 'crate_normal_2_medical' in s: return 'Medical Crate'
    if 'crate_normal_2' in s: return 'Military Crate'
    if 'crate_normal' in s or 'normal_crates' in s: return 'Normal Crate'
    if 'crate_food' in s or 'food_crate' in s: return 'Food Crate'
    if 'crate_medical' in s or 'medical_crate' in s or 'med_crate' in s: return 'Medical Crate'
    if 'crate_tools' in s or 'tools_crate' in s or 'tool_crate' in s: return 'Tool Crate'
    if 'diesel' in s: return 'Diesel Barrel'
    if 'oil_barrel' in s: return 'Oil Barrel'
    if 'barrel' in s: return 'Barrel'
    if 'minecart' in s or 'mine_cart' in s: return 'Minecart'
    if 'crate_underwater' in s or ('underwater' in s and 'crate' in s): return 'Underwater Crate'
    if 'hqm-ore' in s or 'ore_hqm' in s: return 'HQM Node'
    for k, t in (('stone', 'Stone Node'), ('metal', 'Metal Node'), ('sulfur', 'Sulfur Node')):
        if f'{k}-ore' in s or f'ore_{k}' in s:
            return t
    return None


def build_loot(w):
    table = {manifest_hash(p): _short(p) for p in LOOT_PREFABS}
    half = w['size'] / 2.0
    spawns = []
    for pf in w['prefabs']:
        short = table.get(pf['id'])
        kind = short and loot_type(short)
        if not kind:
            continue
        x, z = pf['pos'][0], pf['pos'][2]
        if abs(x) > half + 1000 or abs(z) > half + 1000:
            continue
        spawns.append({'Type': kind, 'X': round(x, 3), 'Z': round(z, 3), 'PrefabName': short})
    spawns.sort(key=lambda s: (s['Type'], s['X'], s['Z']))
    return spawns


# ---------------------------------------------------------------- player spawns
# Точки появления игроков — как SpawnHandler.GetSpawnPointStandard (префаб spawn.procmap.v3 из "world setup"):
# CharDistribution строится на сетке NextPowerOfTwo(size*0.5), фильтр CharacterSpawn (значения из ассетов игры):
#   биом Temperate|Tundra, топология Any=Tier0|Tier1, All=Mainland|Oceanside|Beach,
#   Not=Cliff|Monument|Road|Swamp|River|Riverside|Lake|Rail|Cliffside, сплат — любой, cutoff 0;
# плюс точка отбрасывается, если до монумента меньше 50 м (MonumentInfo.Distance по OBB — тут по топологии Monument).
# Игра выбирает случайную точку из этой области, поэтому на карту выводим её равномерную выборку с шагом SPAWN_STEP.
SPAWN_BIOME = 0x2 | 0x4
SPAWN_TOPO_ANY = 0x4000000 | 0x8000000
SPAWN_TOPO_ALL = 0x20000000 | 0x100 | 0x10
SPAWN_TOPO_NOT = 0x2 | 0x400 | 0x800 | 0x2000 | 0x4000 | 0x8000 | 0x10000 | 0x80000 | 0x400000
SPAWN_MONUMENT_DIST = 50.0
SPAWN_STEP = 60.0
# RustEdit: ручные точки спавна (их использует расширение RustEdit вместо процедурных).
CUSTOM_SPAWN_PREFABS = ['assets/bundled/prefabs/modding/spawn_point.prefab',
                        'assets/bundled/prefabs/modding/volumes_and_triggers/spawn_point.prefab']


def spawn_area(b, w):
    """Маска CharDistribution (char_res x char_res, [z, x]) — где игра может заспавнить игрока."""
    size = float(w['size'])
    TP, tres = _square(_layer(b, w, 'topology', '<i4'))
    raw = _layer(b, w, 'biome', 'u1')
    nch = next((c for c in (5, 4, 6) if raw.size % c == 0 and round(math.sqrt(raw.size / c)) ** 2 * c == raw.size), 4)
    BI, bres = _square(raw, nch)
    char_res = 1 << max(0, int(size * 0.5) - 1).bit_length()          # Mathf.NextPowerOfTwo
    norm = (np.arange(char_res, dtype=np.float32) + 0.5) / char_res

    def idx(res):                                                    # TerrainMap.Index
        return np.clip((norm * res).astype(np.int32), 0, res - 1)

    ti = idx(tres)
    topo = TP[np.ix_(ti, ti)]
    ok = ((topo & SPAWN_TOPO_ANY) != 0) & ((topo & SPAWN_TOPO_NOT) == 0) & ((topo & SPAWN_TOPO_ALL) == SPAWN_TOPO_ALL)
    bi = idx(bres)
    B = BI[:, bi][:, :, bi]                                          # [channel, z, x]
    best = (B.shape[0] - 1) - np.argmax(B[::-1], axis=0)             # GetBiomeMaxIndex: при равенстве — последний
    ok &= ((1 << best) & SPAWN_BIOME) != 0
    # 50 м от монументов: расширяем топологию Monument
    mon = ((TP & 0x400) != 0).astype(np.uint8)
    r = int(math.ceil(SPAWN_MONUMENT_DIST / size * tres))
    if mon.any() and r > 0:
        mon = cv2.dilate(mon, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)))
        ok &= mon[np.ix_(ti, ti)] == 0
    return ok, char_res


def build_player_spawns(b, w, step=SPAWN_STEP):
    size = float(w['size'])
    half = size / 2.0
    custom = {manifest_hash(p) for p in CUSTOM_SPAWN_PREFABS}
    out = [{'Type': 'Player Spawn', 'X': round(p['pos'][0], 3), 'Z': round(p['pos'][2], 3), 'PrefabName': 'spawn_point'}
           for p in w['prefabs'] if p['id'] in custom]
    if out:
        return out, 'custom'
    ok, res = spawn_area(b, w)
    zs, xs = np.nonzero(ok)
    if not len(xs):
        return [], 'none'
    cell = size / res
    px = xs.astype(np.float64) * cell + cell / 2 - half
    pz = zs.astype(np.float64) * cell + cell / 2 - half
    # по одной точке на клетку step x step — ближайшая к центру масс подходящих пикселей клетки
    n = int(math.ceil(size / step))
    cid = np.minimum(((pz + half) // step).astype(np.int64), n - 1) * n + np.minimum(((px + half) // step).astype(np.int64), n - 1)
    cnt = np.bincount(cid, minlength=n * n)
    cx = np.bincount(cid, px, n * n) / np.maximum(cnt, 1)
    cz = np.bincount(cid, pz, n * n) / np.maximum(cnt, 1)
    d = (px - cx[cid]) ** 2 + (pz - cz[cid]) ** 2
    order = np.lexsort((d, cid))
    first = order[np.r_[True, cid[order][1:] != cid[order][:-1]]]
    first = first[cnt[cid[first]] >= 4]                               # отбрасываем одиночные пиксели-огрызки
    # прореживание: соседние клетки не ближе step/2
    pts, keep = [], []
    grid = {}
    min_d2 = (step / 2) ** 2
    for i in first[np.argsort(-cnt[cid[first]], kind='stable')]:
        x, z = px[i], pz[i]
        gx, gz = int((x + half) // step), int((z + half) // step)
        if any((x - a) ** 2 + (z - c) ** 2 < min_d2
               for dx in (-1, 0, 1) for dz in (-1, 0, 1) for a, c in grid.get((gx + dx, gz + dz), ())):
            continue
        grid.setdefault((gx, gz), []).append((x, z))
        keep.append(i)
    keep.sort(key=lambda i: (px[i], pz[i]))
    out = [{'Type': 'Player Spawn', 'X': round(float(px[i]), 3), 'Z': round(float(pz[i]), 3), 'PrefabName': 'procedural'}
           for i in keep]
    return out, f'procedural, {int(ok.sum() * cell * cell)} m2 area'


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('map'); ap.add_argument('out')
    ap.add_argument('--labels-cs', default='')
    ap.add_argument('--font', default='')
    ap.add_argument('--png-level', type=int, default=3)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    version, data = read_map(a.map)
    log(f'map v{version}, {len(data) / 1e6:.1f} MB decompressed')
    w = parse_world(data)
    log(f'world size {w["size"]}, {len(w["prefabs"])} prefabs')
    if not 1000 <= w['size'] <= 8000:
        raise SystemExit(f'unexpected world size {w["size"]}')

    img = render(data, w)
    log(f'rendered {img.shape[1]}x{img.shape[0]}')
    png = [cv2.IMWRITE_PNG_COMPRESSION, a.png_level]
    cv2.imwrite(os.path.join(a.out, 'output_no_labels.png'), img[..., ::-1], png)

    labels = build_labels(w, read_label_config(a.labels_cs))
    json.dump({'WorldSize': float(w['size']), 'Labels': labels},
              open(os.path.join(a.out, 'MapLabels.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    log(f'{len(labels)} labels ({sum(not l["HideOnMap"] for l in labels)} visible)')

    spawns = build_loot(w)
    players, how = build_player_spawns(data, w)
    log(f'{len(players)} player spawns ({how})')
    spawns += players
    json.dump({'WorldSize': int(w['size']), 'Source': 'map', 'Spawns': spawns},
              open(os.path.join(a.out, 'LootSpawns.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    counts = {}
    for s in spawns:
        counts[s['Type']] = counts.get(s['Type'], 0) + 1
    log(f'{len(spawns)} loot spawns from the map file', dict(sorted(counts.items())))

    cv2.imwrite(os.path.join(a.out, 'output.png'), draw_labels(img, labels, a.font)[..., ::-1], png)
    log('done')


if __name__ == '__main__':
    main()

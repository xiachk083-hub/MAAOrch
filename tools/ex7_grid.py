# -*- coding: utf-8 -*-
"""EX-7(act14side_ex07) 地图格网工具：ASCII 地图 + 帧上叠加格子坐标。

标定来源：bi7c 跑局 12 次部署 MAA 拖拽落点（作业 [col,row] -> 像素）。
  x = 78.4567*col + 1.5641*row + 276.11
  y = -9.0487*col + 64.2118*row + 150.46
"""
import io, json, sys, argparse

LEV = 'data/_level_act14side_ex07.json'
A = (78.4567, 1.5641, 276.11)
B = (-9.0487, 64.2118, 150.46)


def to_px(col, row):
    return (A[0] * col + A[1] * row + A[2], B[0] * col + B[1] * row + B[2])


def load():
    d = json.load(io.open(LEV, encoding='utf-8'))
    md = d['mapData']
    mp, tiles = md['map'], md['tiles']
    rows, cols = len(mp), len(mp[0])
    return d, mp, tiles, rows, cols


def sym(t):
    k = t['tileKey']
    if k == 'tile_start':
        return 'S'
    if k == 'tile_end':
        return 'E'
    if k == 'tile_forbidden':
        return '#' if t['heightType'] == 'HIGHLAND' else '?'
    if k == 'tile_wall':
        return '^'          # 高台 RANGED
    if k == 'tile_icestr':
        return 'i'          # 冰面 可近战
    if k == 'tile_road':
        return '.'
    return 'o'


def grid():
    d, mp, tiles, rows, cols = load()
    g = [[sym(tiles[mp[r][c]]) for c in range(cols)] for r in range(rows)]
    return d, g, rows, cols


def routes(d):
    out = []
    for i, rt in enumerate(d['routes']):
        cps = rt.get('checkpoints') or []
        pts = [(p['position']['col'], p['position']['row']) for p in cps]
        sp, ep = rt['startPosition'], rt['endPosition']
        pts = [(sp['col'], sp['row'])] + pts + [(ep['col'], ep['row'])]
        out.append((i, pts))
    return out


def counts_by_route(d):
    c = {}
    for w in d['waves']:
        for f in w['fragments']:
            for a in f['actions']:
                if a['actionType'] != 'SPAWN':
                    continue
                ri = a['routeIndex']
                c[ri] = c.get(ri, 0) + a['count']
    return c


def show_map():
    d, g, rows, cols = grid()
    rt = routes(d)
    cnt = counts_by_route(d)
    # 路线经过次数
    pass_n = {}
    for i, pts in rt:
        for (c, r) in pts:
            pass_n.setdefault((c, r), []).append(i)
    print('   ' + ''.join('%-4d' % c for c in range(cols)))
    for r in range(rows):
        line = '%2d ' % r
        for c in range(cols):
            ch = g[r][c]
            n = len(pass_n.get((c, r), []))
            line += ('%s%-3s' % (ch, n if n else ''))[:4]
        print(line)
    print()
    print('符号: S起点 E终点 #禁放高台 ^可放高台(墙) i冰面 .公路 o其他')
    print()
    total = 0
    for i, pts in rt:
        n = cnt.get(i, 0)
        total += n
        print('route %2d  敌人%2d  %s' % (i, n, ' -> '.join('%d,%d' % p for p in pts)))
    print('总敌人', total)
    print()
    # 路线并集：每格覆盖的敌人总数
    print('每格覆盖敌人（按路线去重，路线内每格记1次）:')
    for r in range(rows):
        line = '%2d ' % r
        for c in range(cols):
            s = set()
            for i, pts in rt:
                if (c, r) in pts:
                    s.add(i)
            line += '%4d' % len(s)
        print(line)


def annotate(frame, out):
    from PIL import Image, ImageDraw
    d, g, rows, cols = grid()
    im = Image.open(frame).convert('RGB')
    dr = ImageDraw.Draw(im, 'RGBA')
    colmap = {'S': (0, 220, 0), 'E': (255, 0, 0), '#': (60, 60, 60),
              '^': (0, 120, 255), 'i': (0, 220, 220), '.': (255, 255, 255),
              '?': (120, 0, 120)}
    for r in range(rows):
        for c in range(cols):
            x, y = to_px(c, r)
            s = g[r][c]
            col = colmap.get(s, (255, 200, 0))
            dr.rectangle([x - 38, y - 30, x + 38, y + 30], outline=col + (200,), width=2)
            dr.rectangle([x - 30, y - 30, x - 2, y - 16], fill=(0, 0, 0, 190))
            dr.text((x - 29, y - 29), '%d,%d' % (c, r), fill=col)
    im.save(out)
    print('saved', out)


def crop(frame, col, row, out, half=170):
    from PIL import Image
    x, y = to_px(col, row)
    im = Image.open(frame).convert('RGB')
    box = (max(0, int(x - half)), max(0, int(y - half)), min(im.width, int(x + half)), min(im.height, int(y + half)))
    im.crop(box).resize(((box[2] - box[0]) * 2, (box[3] - box[1]) * 2)).save(out)
    print('saved', out, box)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--frame')
    ap.add_argument('--out')
    ap.add_argument('--crop', nargs=2, type=int)
    a = ap.parse_args()
    if a.frame and a.crop:
        crop(a.frame, a.crop[0], a.crop[1], a.out or 'runtime/_ce6/crop.png')
    elif a.frame:
        annotate(a.frame, a.out or 'runtime/_ce6/anno.png')
    else:
        show_map()

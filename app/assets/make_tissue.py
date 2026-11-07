"""Procedural clear-cell RCC histology field as SVG (H&E colors). Deterministic."""
import numpy as np
from scipy.spatial import Voronoi
import sys

def tissue(w=560, h=520, n=150, seed=7, path_cells=()):
    rng = np.random.default_rng(seed)
    pts = rng.uniform([0, 0], [w, h], size=(n, 2))
    for _ in range(2):  # Lloyd relaxation for even, cell-like packing
        vor = Voronoi(np.vstack([pts, [[-w, -h], [3*w, -h], [-w, 3*h], [3*w, 3*h]]]))
        new = []
        for i in range(n):
            reg = vor.regions[vor.point_region[i]]
            if -1 in reg or not reg: new.append(pts[i]); continue
            poly = vor.vertices[reg]; new.append(np.clip(poly.mean(0), 0, [w, h]))
        pts = np.array(new)
    vor = Voronoi(np.vstack([pts, [[-w, -h], [3*w, -h], [-w, 3*h], [3*w, 3*h]]]))
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" role="img" '
           f'aria-label="Illustration of clear cell kidney cancer tissue: pale cells with violet nuclei and thin pink vessels">',
           '<defs><clipPath id="c"><rect width="100%" height="100%" rx="10"/></clipPath>'
           '<filter id="s"><feTurbulence type="fractalNoise" baseFrequency=".9" numOctaves="2" seed="3"/>'
           '<feColorMatrix values="0 0 0 0 .83  0 0 0 0 .33  0 0 0 0 .48  0 0 0 .05 0"/><feComposite in2="SourceGraphic" operator="in"/></filter></defs>',
           '<g clip-path="url(#c)">', f'<rect width="{w}" height="{h}" fill="#f6e9ee"/>']
    cells = []
    for i in range(n):
        reg = vor.regions[vor.point_region[i]]
        if -1 in reg or not reg: continue
        poly = vor.vertices[reg]
        c = poly.mean(0)
        shrink = poly + (c - poly) * 0.06
        d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in shrink) + "Z"
        fill = "#fdfbfd" if rng.random() > 0.12 else "#f9edf2"
        out.append(f'<path d="{d}" fill="{fill}" stroke="#d98aa6" stroke-width="1.6" stroke-linejoin="round"/>')
        cells.append((c, poly))
    # nuclei: small, eccentric, violet
    for c, poly in cells:
        off = (poly[rng.integers(len(poly))] - c) * rng.uniform(0.25, 0.45)
        r = rng.uniform(3.2, 5.2)
        out.append(f'<ellipse cx="{c[0]+off[0]:.1f}" cy="{c[1]+off[1]:.1f}" rx="{r:.1f}" ry="{r*rng.uniform(.75,1):.1f}" '
                   f'fill="#4f3fbf" opacity="{rng.uniform(.7,.95):.2f}"/>')
    # thin capillaries along some cell borders (the delicate vascular network of ccRCC)
    edges = [(a, b) for (a, b) in vor.ridge_vertices if a >= 0 and b >= 0]
    rng.shuffle(edges)
    for a, b in edges[: n // 3]:
        (x1, y1), (x2, y2) = vor.vertices[a], vor.vertices[b]
        out.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="#c23c6a" stroke-width="2.6" '
                   'stroke-linecap="round" opacity=".55"/>')
    out.append(f'<rect width="{w}" height="{h}" filter="url(#s)" fill="#fff"/>')
    out.append('</g></svg>')
    return "\n".join(out)

if __name__ == "__main__":
    open(sys.argv[1], "w").write(tissue())

"""Headless check of the web viewer — asserts the map actually drew.

Screenshots alone are not enough here. MapLibre decodes vector tiles on worker
threads, and Chrome's --virtual-time-budget advances timers without waiting for
that work, so a screenshot can come back showing a bare basemap while the data
is perfectly fine. This drives a real browser, waits on `idle`, then queries the
loaded source and reports feature counts.

Uses the system Chrome (channel="chrome"), so there is no browser download.

    python src/webcheck.py                        # against the dev server
    python src/webcheck.py --url http://homeweb.lan/poudremap/
    python src/webcheck.py --shot out/web.png
"""

from __future__ import annotations

import argparse
import sys

import sources as S

PROBE = """
async () => {
  const m = window.poudreMap;
  if (!m) return {fatal: 'window.poudreMap missing'};
  if (!m.isStyleLoaded()) await new Promise(r => m.once('idle', r));
  const counts = {};
  for (const sl of ['basin','huc10','huc12','flowlines','waterbodies',
                    'canals','gages','highways','nldi_basin']) {
    counts[sl] = m.querySourceFeatures('poudre', {sourceLayer: sl}).length;
  }
  const h12 = m.querySourceFeatures('poudre', {sourceLayer:'huc12'});
  const uniq = new Map(); h12.forEach(f => uniq.set(f.properties.huc12, f.properties));
  const fl = m.querySourceFeatures('poudre', {sourceLayer:'flowlines'});
  const seen = new Set(); let nat = 0, art = 0;
  fl.forEach(f => { const k = f.properties.permanent_identifier;
    if (seen.has(k)) return; seen.add(k); f.properties.natural ? nat++ : art++; });
  return {
    zoom: +m.getZoom().toFixed(2),
    layers: m.getStyle().layers.map(l => l.id),
    counts,
    distinct_huc12: uniq.size,
    wy_huc12: [...uniq.values()].filter(p => String(p.states||'').includes('WY')).length,
    natural: nat, artificial: art,
    labels: document.querySelectorAll('.lbl').length,
    shields: [...document.querySelectorAll('.shield')].map(e => e.textContent),
    helpBtn: !!document.getElementById('helpbtn'),
    err: (document.getElementById('err').style.display || 'none')
  };
}
"""

EXPECT = {"distinct_huc12": 53, "wy_huc12": 7}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default="http://localhost:8137/poudremap/")
    ap.add_argument("--shot", default=None, help="also save a screenshot here")
    ap.add_argument("--timeout", type=int, default=90_000)
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    problems: list[str] = []
    with sync_playwright() as pw:
        # ANGLE over SwiftShader is the combination that compiles MapLibre's
        # shaders headlessly. Plain --use-gl=swiftshader fails with "Could not
        # compile fragment shader" and the map silently renders nothing but the
        # basemap — which looks exactly like a data problem and is not one.
        browser = pw.chromium.launch(
            channel="chrome", headless=True,
            args=["--no-sandbox", "--enable-unsafe-swiftshader",
                  "--use-gl=angle", "--use-angle=swiftshader"])
        page = browser.new_page(viewport={"width": 1500, "height": 950})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}")
                if m.type == "error" else None)

        print(f"loading {args.url}")
        page.goto(args.url, wait_until="load", timeout=args.timeout)
        res = page.evaluate(PROBE)

        if res.get("fatal"):
            print(f"FATAL: {res['fatal']}")
            browser.close()
            return 1

        print(f"\nzoom {res['zoom']}  ·  {len(res['layers'])} layers")
        print("\nfeatures loaded from the PMTiles source:")
        for k, v in res["counts"].items():
            flag = "" if v else "   <-- EMPTY"
            print(f"  {k:<14} {v:>7}{flag}")
            if not v:
                problems.append(f"{k} rendered no features")

        print(f"\ndistinct HUC12   {res['distinct_huc12']}")
        print(f"WY-designated    {res['wy_huc12']}")
        print(f"flowlines        {res['natural']} natural, "
              f"{res['artificial']} artificial")
        print(f"place labels     {res['labels']}")
        print(f"shields          {', '.join(res['shields']) or 'none'}")
        print(f"help button      {'present' if res['helpBtn'] else 'MISSING'}")

        for key, want in EXPECT.items():
            if res.get(key) != want:
                problems.append(f"{key} = {res.get(key)}, expected {want}")
        if res["err"] != "none":
            problems.append("error banner is visible")
        if not res["labels"]:
            problems.append("no place labels rendered")
        if errors:
            problems += [f"page error: {e}" for e in errors[:5]]

        if args.shot:
            out = S.ROOT / args.shot
            out.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(out))
            print(f"\nwrote {out.relative_to(S.ROOT)}")

        browser.close()

    if problems:
        print("\nPROBLEMS")
        for p in problems:
            print(f"  ✗ {p}")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

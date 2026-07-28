#!/usr/bin/env python3
# scripts/gen_sbom.py — generate a minimal CycloneDX-style JSON SBOM from the
# installed Python environment, for vulnerability / dependency review by a
# customer compliance team (e.g. Dexcom).
#
# Usage: python scripts/gen_sbom.py [--out sbom.json] [--format json|txt]
# ponytail: stdlib importlib.metadata only — no cyclonedx-bom dependency. Add it
# if a customer requires a strictly spec-valid CycloneDX/Syft BOM.
import argparse, json, pathlib, importlib.metadata as md
from datetime import datetime, timezone

SCHEMA = "https://cyclonedx.org/schema/bom-1.5.schema.json"


def _license(dist: md.Distribution) -> str:
    # ponytail: best-effort license string from metadata; fall back to "UNKNOWN".
    for key in ("License", "License-Expression"):
        val = dist.metadata.get(key)
        if val:
            return val.strip() or "UNKNOWN"
    classifiers = dist.metadata.get_all("Classifier") or []
    for c in classifiers:
        if c.startswith("License ::"):
            return c.split("::")[-1].strip()
    return "UNKNOWN"


def build_components():
    comps = []
    for dist in sorted(md.distributions(), key=lambda d: (d.metadata["Name"] or "").lower()):
        name = dist.metadata["Name"] or "unknown"
        ver = dist.version
        if not ver:
            continue
        try:
            reqs = sorted((r.split(";")[0].strip() for r in dist.requires or []), )
        except Exception:
            reqs = []
        comps.append({
            "type": "library",
            "bom-ref": f"pkg:pypi/{name}@{ver}",
            "name": name,
            "version": ver,
            "licenses": [{"license": {"name": _license(dist)}}],
            "purl": f"pkg:pypi/{name}@{ver}",
            "dependencies": [r for r in reqs if r],
        })
    return comps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/sbom.json")
    ap.add_argument("--format", choices=("json", "txt"), default="json")
    a = ap.parse_args()

    comps = build_components()
    # ponytail: deterministic timestamp via UTC (no local tz); timestamp is fine here
    # because this is a build artifact, not a workflow script.
    bom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": "urn:uuid:",
        "version": 1,
        "metadata": {"timestamp": datetime.now(timezone.utc).isoformat(),
                     "tools": [{"vendor": "Smriti", "name": "gen_sbom.py"}]},
        "components": comps,
    }

    out = pathlib.Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if a.format == "json":
        out.write_text(json.dumps(bom, indent=2))
    else:
        lines = [f"{c['name']}=={c['version']}  ({c['licenses'][0]['license']['name']})" for c in comps]
        out.write_text("\n".join(lines) + "\n")
    print(f"SBOM: {len(comps)} components → {out}")
    # surface any UNKNOWN licenses so they get human review before sending to a customer
    unknown = [c["name"] for c in comps if c["licenses"][0]["license"]["name"] == "UNKNOWN"]
    if unknown:
        print(f"  ⚠ {len(unknown)} packages with UNKNOWN license (review): {', '.join(unknown[:10])}")


if __name__ == "__main__":
    main()
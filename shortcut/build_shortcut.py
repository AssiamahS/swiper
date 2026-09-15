#!/usr/bin/env python3
"""Build and sign the Swiper iOS Shortcut.

Shape (same as Swiperino's): Receive Safari web page from the share sheet ->
Run JavaScript on Web Page -> fetch swiper.js and eval it inside tinder.com.

Usage:  python3 shortcut/build_shortcut.py            # writes shortcut/Swiper.shortcut (signed)
        python3 shortcut/build_shortcut.py --url URL  # point at a different script host
"""
import argparse, plistlib, subprocess, sys, uuid
from pathlib import Path

DEFAULT_URL = "https://raw.githubusercontent.com/AssiamahS/swiper/main/swiper.js"

LOADER = """(function(){
  var u = '%s?t=' + Date.now();
  var x = new XMLHttpRequest();
  x.onreadystatechange = function(){
    if (x.readyState !== 4) return;
    if (x.status === 200) {
      try { (0, eval)(x.responseText); completion('swiper loaded'); }
      catch (e) { completion('swiper error: ' + e); }
    } else { completion('swiper fetch failed: ' + x.status); }
  };
  x.open('GET', u); x.send();
})();"""


def build(url: str) -> dict:
    return {
        "WFWorkflowClientVersion": "2607.0.3",
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowIcon": {"WFWorkflowIconStartColor": 4292093695, "WFWorkflowIconGlyphNumber": 59771},
        "WFWorkflowImportQuestions": [],
        "WFWorkflowTypes": ["ActionExtension"],
        "WFWorkflowInputContentItemClasses": ["WFSafariWebPageContentItem"],
        "WFWorkflowHasShortcutInputVariables": True,
        "WFWorkflowHasOutputFallback": False,
        "WFWorkflowNoInputBehavior": {"Name": "WFWorkflowNoInputBehaviorContinue"},
        "WFWorkflowActions": [
            {
                "WFWorkflowActionIdentifier": "is.workflow.actions.runjavascriptonwebpage",
                "WFWorkflowActionParameters": {
                    "UUID": str(uuid.uuid4()).upper(),
                    "WFJavaScript": LOADER % url,
                    "WFInput": {
                        "Value": {"Type": "ExtensionInput"},
                        "WFSerializationType": "WFTextTokenAttachment",
                    },
                },
            }
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--out", default=str(Path(__file__).parent / "Swiper.shortcut"))
    a = ap.parse_args()
    out = Path(a.out)
    raw = out.with_suffix(".unsigned.shortcut")
    raw.write_bytes(plistlib.dumps(build(a.url), fmt=plistlib.FMT_BINARY))
    (out.parent / "loader.js").write_text(LOADER % a.url + "\n")
    r = subprocess.run(["shortcuts", "sign", "--mode", "anyone", "--input", str(raw), "--output", str(out)],
                       capture_output=True, text=True)
    raw.unlink(missing_ok=True)
    if r.returncode != 0:
        print("sign failed:", r.stderr.strip() or r.stdout.strip(), file=sys.stderr)
        return 1
    print("signed:", out, out.stat().st_size, "bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

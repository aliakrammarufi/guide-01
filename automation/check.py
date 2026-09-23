#!/usr/bin/env python3
"""Small independent checks before an automated task is accepted."""

from __future__ import annotations

from html.parser import HTMLParser
import json
from pathlib import Path
import sys
from urllib.parse import urlparse


PROJECT = Path(__file__).resolve().parents[1]
SITE = PROJECT / "dist"


class AssetParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.assets: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "link" and values.get("rel") == "stylesheet":
            self.assets.add(values.get("href") or "")
        if tag == "script":
            self.assets.add(values.get("src") or "")
        if tag == "img":
            self.assets.add(values.get("src") or "")
        if tag == "source":
            self.assets.add(values.get("srcset") or "")


def stan_link(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = urlparse(value)
        return (
            parsed.scheme == "https"
            and parsed.hostname is not None
            and (parsed.hostname == "stan.store" or parsed.hostname.endswith(".stan.store"))
            and not parsed.username
            and not parsed.password
            and not parsed.port
            and parsed.path not in {"", "/"}
        )
    except ValueError:
        return False


def main() -> int:
    errors: list[str] = []
    try:
        catalog = json.loads((SITE / "guides.json").read_text(encoding="utf-8"))
        if not isinstance(catalog, dict):
            errors.append("guides.json must be a JSON object")
        else:
            if not isinstance(catalog.get("storeName"), str) or not catalog["storeName"].strip():
                errors.append("guides.json needs a nonempty storeName")
            if catalog.get("stanUrl") and not stan_link(catalog["stanUrl"]):
                errors.append("stanUrl must be a Stan HTTPS storefront link")
            guides = catalog.get("guides")
            if not isinstance(guides, list):
                errors.append("guides must be a list")
            else:
                for index, guide in enumerate(guides):
                    if not isinstance(guide, dict):
                        errors.append(f"guide {index} must be an object")
                        continue
                    for field in ("title", "description", "country", "state"):
                        if not isinstance(guide.get(field), str) or not guide[field].strip():
                            errors.append(f"guide {index} needs {field}")
                    if not stan_link(guide.get("stanUrl")):
                        errors.append(f"guide {index} needs a Stan HTTPS product link")
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"Could not load guides.json: {exc}")

    try:
        html = (SITE / "index.html").read_text(encoding="utf-8")
        parser = AssetParser()
        parser.feed(html)
        for required in ("styles.css", "site.js", "assets/travel-planning.webp", "assets/travel-planning-800.webp"):
            if required not in parser.assets or not (SITE / required).is_file():
                errors.append(f"Missing site asset: {required}")
        if "guides.json" not in (SITE / "site.js").read_text(encoding="utf-8"):
            errors.append("site.js no longer loads guides.json")
        for image in ("travel-planning.webp", "travel-planning-800.webp"):
            path = SITE / "assets" / image
            if path.is_file() and path.stat().st_size > 400_000:
                errors.append(f"{image} is over 400 KB")
    except OSError as exc:
        errors.append(f"Could not read site files: {exc}")

    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("Site catalog, references, and image sizes passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

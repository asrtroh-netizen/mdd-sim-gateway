"""Map a user-supplied Apple IPCC (or a zip/dir of plists) into this repo's profile schema.

Only the fields this gateway actually uses are imported: MCC-MNC, ePDG host, IMS realm,
SMSC, APN, and a coarse IPv4/IPv6 IMS hint when the bundle names one. The rest of an
iPhone carrier bundle is ignored on purpose — this is not a dump of Apple IPCC and not
a VoCat carrier database.

The mapper is heuristic. It walks plist keys/values that Apple and public carrier-bundle
discussions already expose (MCC, MNC, APN, hostnames) plus any hostname that is clearly
an ePDG / IMS realm. Secret AKA material is rejected.
"""
from __future__ import annotations

import argparse
import io
import os
import plistlib
import re
import zipfile
from pathlib import Path
from typing import Any

from . import carrier_profile

_EPDG_HOST = re.compile(
    r"(?i)\b(?:epdg(?:\.[a-z0-9.-]+)?|[a-z0-9.-]*epdg[a-z0-9.-]*"
    r"|epdg\.epc\.mnc[0-9]{3}\.mcc[0-9]{3}\.pub\.3gppnetwork\.org)\b")
_REALM_HOST = re.compile(
    r"(?i)\b(?:ims\.mnc[0-9]{3}\.mcc[0-9]{3}\.3gppnetwork\.org|[a-z0-9.-]*ims[a-z0-9.-]*)\b")
_SMSC = re.compile(r"^\+?[0-9]{6,15}$")
_MCC = re.compile(r"^[0-9]{3}$")
_MNC = re.compile(r"^[0-9]{2,3}$")


def _walk(obj: Any, path: str = ""):
    yield path, obj
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield from _walk(value, f"{path}.{key}" if path else str(key))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            yield from _walk(value, f"{path}[{index}]")


def _leaf_strings(root: Any) -> list[tuple[str, str]]:
    out = []
    for path, value in _walk(root):
        if isinstance(value, (str, int)):
            out.append((path.lower(), str(value).strip()))
    return out


def _collect_plists(source: Path | bytes, *, name: str = "bundle") -> list[dict]:
    documents = []
    if isinstance(source, bytes):
        if source[:4] == b"PK\x03\x04":
            with zipfile.ZipFile(io.BytesIO(source)) as archive:
                for info in archive.infolist():
                    if info.is_dir():
                        continue
                    if not info.filename.lower().endswith((".plist", ".ipcc")):
                        continue
                    raw = archive.read(info)
                    documents.extend(_collect_plists(raw, name=info.filename))
            return documents
        try:
            documents.append(plistlib.loads(source))
        except Exception as exc:
            raise carrier_profile.ProfileError(
                f"{name}: not a zip or plist ({exc})") from exc
        return documents

    path = Path(source)
    if path.is_dir():
        for child in sorted(path.rglob("*")):
            if child.suffix.lower() in {".plist", ".ipcc", ".zip"}:
                documents.extend(_collect_plists(child, name=str(child)))
        return documents
    if not path.is_file():
        raise carrier_profile.ProfileError(f"{name}: file not found")
    data = path.read_bytes()
    if path.suffix.lower() in {".zip", ".ipcc"} or data[:4] == b"PK\x03\x04":
        return _collect_plists(data, name=path.name)
    return _collect_plists(data, name=path.name)


def _pick_plmn(pairs: list[tuple[str, str]]) -> list[dict]:
    mccs, mncs = [], []
    matches = []
    for path, value in pairs:
        tail = path.rsplit(".", 1)[-1]
        if tail in {"mcc", "mobilecountrycode"} and _MCC.match(value):
            mccs.append(value)
        elif tail in {"mnc", "mobilenetworkcode"} and _MNC.match(value):
            mncs.append(value)
    for mcc, mnc in zip(mccs, mncs):
        row = {"mcc": mcc, "mnc": mnc}
        if row not in matches:
            matches.append(row)
    return matches


def _looks_like_host(value: str) -> bool:
    return "." in value and " " not in value and not value.startswith("/")


def extract_overrides(documents: list[dict]) -> dict:
    pairs = []
    for doc in documents:
        carrier_profile._reject_secrets(doc, "ipcc")
        pairs.extend(_leaf_strings(doc))

    overrides: dict[str, Any] = {}
    matches = _pick_plmn(pairs)
    for path, value in pairs:
        tail = path.rsplit(".", 1)[-1]
        if not overrides.get("epdg") and (
                "epdg" in tail or _EPDG_HOST.search(value)) and _looks_like_host(value):
            overrides["epdg"] = value.lower()
        if not overrides.get("realm") and (
                tail in {"realm", "imsrealm", "ims_realm"} or _REALM_HOST.fullmatch(value)):
            if _looks_like_host(value) and "epdg" not in value.lower():
                overrides["realm"] = value.lower()
        if not overrides.get("smsc") and ("smsc" in tail or "smsp" in tail) and _SMSC.match(value):
            overrides["smsc"] = value
        if not overrides.get("apn") and tail in {"apn", "apnname", "attachapnname"}:
            if value and " " not in value:
                overrides["apn"] = value.lower()
        if not overrides.get("ims_af"):
            blob = f"{path} {value}".lower()
            if "ipv6" in blob and "ims" in blob:
                overrides["ims_af"] = "v6"
            elif "ipv4" in blob and "ims" in blob:
                overrides["ims_af"] = "v4"
    if not matches:
        raise carrier_profile.ProfileError(
            "IPCC did not name an MCC-MNC; add matches manually after import")
    return {"matches": matches, "overrides": overrides}


def profile_from_ipcc(source: Path | bytes, *, profile_id: str | None = None,
                      name: str = "") -> dict:
    documents = _collect_plists(source)
    if not documents:
        raise carrier_profile.ProfileError("no plist documents found in the IPCC")
    extracted = extract_overrides(documents)
    first = extracted["matches"][0]
    ident = (profile_id or f"ipcc-{first['mcc']}-{first['mnc']}").lower()
    return carrier_profile.normalize_profile({
        "version": 1,
        "id": ident,
        "name": name or ident,
        "matches": extracted["matches"],
        "overrides": extracted["overrides"],
        "source": {"kind": "ipcc", "note": "mapped fields this gateway uses; other IPCC keys ignored"},
    })


def import_ipcc(source: Path | bytes, *, profile_id: str | None = None,
                name: str = "", persist: bool = True) -> dict:
    profile = profile_from_ipcc(source, profile_id=profile_id, name=name)
    if persist:
        carrier_profile.save_profile(profile)
    return profile


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Map a user-supplied IPCC zip/plist into an MDD carrier profile.")
    parser.add_argument("source", help="path to .ipcc, .zip, .plist, or a directory")
    parser.add_argument("--id", dest="profile_id", help="profile id to write")
    parser.add_argument("--name", default="", help="display name")
    parser.add_argument("--stdout", action="store_true",
                        help="print YAML instead of writing $MDD_DATA/carrier-profiles")
    args = parser.parse_args(argv)
    profile = import_ipcc(Path(args.source), profile_id=args.profile_id,
                          name=args.name, persist=not args.stdout)
    if args.stdout:
        print(carrier_profile.dump_document(profile), end="")
    else:
        print(f"wrote {profile['id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

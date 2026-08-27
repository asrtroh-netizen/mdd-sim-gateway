# Carrier profiles

This gateway owns a small interoperability profile format. A profile can override
the values a line would otherwise derive from the SIM (3GPP ePDG/realm) or from
the in-tree MCC-MNC hints (`CARRIER_CP_PREF`, `CARRIER_SIP_PROFILES`).

It is **not** a copy of VoCat / MengMengCode `carrier_profiles.json`, and it is
**not** an Apple IPCC bundle. Those files stay outside this repository.

## Where files live

| Location | Loaded at runtime? |
|---|---|
| `$MDD_DATA/carrier-profiles/*.yaml` (also `.yml` / `.json`) | Yes |
| `examples/carrier-profiles/` in this repo | No — copy a file into `$MDD_DATA` to use it |

One profile per file. AKA material (`Ki`, `OP`, `OPc`, …) is rejected.

## Schema (version 1)

```yaml
version: 1
id: example-test-lab          # required, lowercase token
name: Example 3GPP test lab
matches:                      # required, at least one PLMN
  - mcc: "001"
    mnc: "01"
overrides:
  epdg: epdg.example.test     # host; empty → 3GPP IMSI-derived FQDN
  realm: ims.example.test     # IMS realm; empty → 3GPP formula
  ims_af: auto                # auto | v4 | v6 | dual
  probe_order: [v6, dual, v4] # first family the engine tries in auto
  pani_country: GB            # ISO 3166-1 alpha-2 for P-Access-Network-Info
  pani_bssid_policy: derived  # derived (stable hash) | placeholder (all-f)
  access_type: wlan1
  user_eq_phone: true
  smsc: "+447700900111"       # used only when the line has no SMSC yet
  apn: ims
  idr_mode: apn               # apn | fqdn
source:
  kind: manual                # manual | ipcc
  note: optional
```

Precedence for each field: **explicit line setting → matching profile → in-tree
hint → 3GPP IMSI-derived default**.

## IPCC import

`python -m control.app.carrier_ipcc path/to/bundle.ipcc`

The importer reads a user-supplied `.ipcc` / `.zip` / `.plist` / directory and
maps **only** MCC-MNC, ePDG host, IMS realm, SMSC, APN, and a coarse IPv4/IPv6
IMS hint when the bundle names one. Other iPhone keys are ignored.

Host API: `GET /api/carrier-profiles`, `POST /api/carrier-profiles/import-ipcc`
with `{"path": "/absolute/or/relative.ipcc"}`.

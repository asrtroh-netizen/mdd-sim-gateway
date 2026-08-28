/**
 * Flag-only port of OneBoard resolveSemanticVisual.
 * Keyword / Clash / brand theme SVGs are intentionally omitted.
 */
import {
  COUNTRY_DEFS,
  CODE_ALIASES,
  getFlagAssetPath,
  getFlagWatermarkTuning,
  GLOBAL_FLAG_PATH,
  hasFlagAsset,
  normalizeCountryCode,
  REGION_KEYWORDS,
} from './countryRegistry.js'

export function guessCountryCode(text, fallback = '') {
  const raw = String(text || '').trim()
  if (raw) {
    const upper = raw.toUpperCase()
    if (CODE_ALIASES[upper] && hasFlagAsset(CODE_ALIASES[upper])) return CODE_ALIASES[upper]
    if (/^[A-Z]{2}$/.test(upper) && hasFlagAsset(upper)) return upper
    for (const country of COUNTRY_DEFS) {
      if (country.en && raw.toLowerCase() === country.en.toLowerCase()) return country.iso
      if (country.zh && raw === country.zh) return country.iso
    }
    for (const { code, patterns } of REGION_KEYWORDS) {
      if (patterns.some((pattern) => pattern.test(raw))) return code
    }
    for (const country of COUNTRY_DEFS) {
      if (country.en && raw.toLowerCase().includes(country.en.toLowerCase())) return country.iso
    }
  }
  if (fallback && fallback !== text) return guessCountryCode(fallback)
  return 'GLOBAL'
}

export function resolveSemanticVisual(code = '', label = '') {
  let resolved = normalizeCountryCode(code)
  if (!hasFlagAsset(resolved)) {
    const guessed = guessCountryCode(label || code, code)
    if (hasFlagAsset(guessed)) resolved = guessed
  }
  if (hasFlagAsset(resolved)) {
    const flagUrl = getFlagAssetPath(resolved)
    return {
      kind: 'flag',
      category: 'country',
      logo: flagUrl,
      background: flagUrl,
      image: flagUrl,
      thumb: flagUrl,
      theme: resolved.toLowerCase(),
    }
  }
  return {
    kind: 'flag',
    category: 'country',
    logo: GLOBAL_FLAG_PATH,
    background: GLOBAL_FLAG_PATH,
    image: GLOBAL_FLAG_PATH,
    thumb: GLOBAL_FLAG_PATH,
    theme: 'global',
  }
}

export function resolveDeviceCountryCode(device) {
  const sources = [
    device?.egress?.detected_country,
    device?.egress?.country,
    device?.egress?.node,
    device?.sim?.country,
    device?.country,
    device?.name,
  ]
  for (const source of sources) {
    if (source == null || source === '') continue
    const guessed = guessCountryCode(source)
    if (hasFlagAsset(guessed)) return guessed
  }
  return 'GLOBAL'
}

export function resolveDeviceVisual(device) {
  const code = resolveDeviceCountryCode(device)
  return { visual: resolveSemanticVisual(code, code), code }
}

/** OneBoard proxyFlagWatermarkStyle — crop/scale tokens for cine-bg-flag */
export function proxyFlagWatermarkStyle(code) {
  const tuning = getFlagWatermarkTuning(code)
  return {
    '--flag-focus-x': tuning.x,
    '--flag-focus-y': tuning.y,
    '--ob-flag-scale-min': String(tuning.scaleMin),
    '--ob-flag-scale-max': String(tuning.scaleMax),
  }
}

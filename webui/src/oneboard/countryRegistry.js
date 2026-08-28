/**
 * Ported from OneBoard src/utils/countryRegistry.js — flag registry only.
 */
export const GLOBAL_FLAG_PATH = '/maps/global.svg'
export const FLAG_ASSET_PREFIX = '/flags'

/** @typedef {{ iso: string, zh: string, en: string, emoji: string, aliases?: string[], patterns?: RegExp[], flagFocus?: { x: string, y: string, scaleMin?: number, scaleMax?: number } }} CountryDef */

/** @type {CountryDef[]} */
const COUNTRY_DEFS = [
  { iso: 'US', zh: '美国', en: 'United States', emoji: '🇺🇸', aliases: ['USA'], patterns: [/\bUS\b/i, /美国/, /🇺🇸/, /\bUSA\b/i], flagFocus: { x: '34%', y: '34%' } },
  { iso: 'GB', zh: '英国', en: 'United Kingdom', emoji: '🇬🇧', aliases: ['UK'], patterns: [/\bGB\b/i, /\bUK\b/i, /英国/, /🇬🇧/], flagFocus: { x: '50%', y: '50%' } },
  { iso: 'HK', zh: '香港', en: 'Hong Kong', emoji: '🇭🇰', patterns: [/\bHK\b/i, /香港/, /🇭🇰/], flagFocus: { x: '56%', y: '42%' } },
  { iso: 'JP', zh: '日本', en: 'Japan', emoji: '🇯🇵', patterns: [/\bJP\b/i, /日本/, /🇯🇵/], flagFocus: { x: '58%', y: '50%' } },
  { iso: 'SG', zh: '新加坡', en: 'Singapore', emoji: '🇸🇬', patterns: [/\bSG\b/i, /新加坡/, /🇸🇬/], flagFocus: { x: '50%', y: '50%' } },
  { iso: 'CN', zh: '中国', en: 'China', emoji: '🇨🇳', patterns: [/\bCN\b/i, /中国/, /🇨🇳/] },
  { iso: 'TW', zh: '台湾', en: 'Taiwan', emoji: '🇹🇼', patterns: [/\bTW\b/i, /台湾/, /🇹🇼/], flagFocus: { x: '48%', y: '50%' } },
  { iso: 'KR', zh: '韩国', en: 'South Korea', emoji: '🇰🇷', patterns: [/\bKR\b/i, /韩国/, /🇰🇷/], flagFocus: { x: '52%', y: '50%' } },
  { iso: 'DE', zh: '德国', en: 'Germany', emoji: '🇩🇪', patterns: [/\bDE\b/i, /德国/, /🇩🇪/], flagFocus: { x: '50%', y: '46%' } },
  { iso: 'NL', zh: '荷兰', en: 'Netherlands', emoji: '🇳🇱', patterns: [/\bNL\b/i, /荷兰/, /🇳🇱/] },
  { iso: 'FR', zh: '法国', en: 'France', emoji: '🇫🇷', patterns: [/\bFR\b/i, /法国/, /🇫🇷/] },
  { iso: 'CA', zh: '加拿大', en: 'Canada', emoji: '🇨🇦', patterns: [/\bCA\b/i, /加拿大/, /🇨🇦/] },
  { iso: 'AU', zh: '澳大利亚', en: 'Australia', emoji: '🇦🇺', patterns: [/\bAU\b/i, /澳大利亚/, /澳洲/, /🇦🇺/] },
  { iso: 'NZ', zh: '新西兰', en: 'New Zealand', emoji: '🇳🇿', patterns: [/\bNZ\b/i, /新西兰/, /🇳🇿/], flagFocus: { x: '72%', y: '48%' } },
  { iso: 'MY', zh: '马来西亚', en: 'Malaysia', emoji: '🇲🇾', patterns: [/\bMY\b/i, /马来西亚/, /🇲🇾/] },
  { iso: 'TH', zh: '泰国', en: 'Thailand', emoji: '🇹🇭', patterns: [/\bTH\b/i, /泰国/, /🇹🇭/] },
  { iso: 'VN', zh: '越南', en: 'Vietnam', emoji: '🇻🇳', patterns: [/\bVN\b/i, /越南/, /🇻🇳/] },
  { iso: 'PH', zh: '菲律宾', en: 'Philippines', emoji: '🇵🇭', patterns: [/\bPH\b/i, /菲律宾/, /🇵🇭/] },
  { iso: 'ID', zh: '印度尼西亚', en: 'Indonesia', emoji: '🇮🇩', patterns: [/\bID\b/i, /印度尼西亚/, /印尼/, /🇮🇩/] },
  { iso: 'IN', zh: '印度', en: 'India', emoji: '🇮🇳', patterns: [/\bIN\b/i, /印度/, /🇮🇳/] },
  { iso: 'TR', zh: '土耳其', en: 'Turkey', emoji: '🇹🇷', patterns: [/\bTR\b/i, /土耳其/, /🇹🇷/] },
  { iso: 'BR', zh: '巴西', en: 'Brazil', emoji: '🇧🇷', patterns: [/\bBR\b/i, /巴西/, /🇧🇷/] },
  { iso: 'AR', zh: '阿根廷', en: 'Argentina', emoji: '🇦🇷', patterns: [/\bAR\b/i, /阿根廷/, /🇦🇷/] },
  { iso: 'MX', zh: '墨西哥', en: 'Mexico', emoji: '🇲🇽', patterns: [/\bMX\b/i, /墨西哥/, /🇲🇽/] },
  { iso: 'CL', zh: '智利', en: 'Chile', emoji: '🇨🇱', patterns: [/\bCL\b/i, /智利/, /🇨🇱/] },
  { iso: 'CO', zh: '哥伦比亚', en: 'Colombia', emoji: '🇨🇴', patterns: [/\bCO\b/i, /哥伦比亚/, /🇨🇴/] },
  { iso: 'PE', zh: '秘鲁', en: 'Peru', emoji: '🇵🇪', patterns: [/\bPE\b/i, /秘鲁/, /🇵🇪/] },
  { iso: 'RU', zh: '俄罗斯', en: 'Russia', emoji: '🇷🇺', patterns: [/\bRU\b/i, /俄罗斯/, /🇷🇺/] },
  { iso: 'UA', zh: '乌克兰', en: 'Ukraine', emoji: '🇺🇦', patterns: [/\bUA\b/i, /乌克兰/, /🇺🇦/] },
  { iso: 'PL', zh: '波兰', en: 'Poland', emoji: '🇵🇱', patterns: [/\bPL\b/i, /波兰/, /🇵🇱/] },
  { iso: 'SE', zh: '瑞典', en: 'Sweden', emoji: '🇸🇪', patterns: [/\bSE\b/i, /瑞典/, /🇸🇪/] },
  { iso: 'NO', zh: '挪威', en: 'Norway', emoji: '🇳🇴', patterns: [/\bNO\b/i, /挪威/, /🇳🇴/] },
  { iso: 'FI', zh: '芬兰', en: 'Finland', emoji: '🇫🇮', patterns: [/\bFI\b/i, /芬兰/, /🇫🇮/] },
  { iso: 'DK', zh: '丹麦', en: 'Denmark', emoji: '🇩🇰', patterns: [/\bDK\b/i, /丹麦/, /🇩🇰/] },
  { iso: 'ES', zh: '西班牙', en: 'Spain', emoji: '🇪🇸', patterns: [/\bES\b/i, /西班牙/, /🇪🇸/] },
  { iso: 'PT', zh: '葡萄牙', en: 'Portugal', emoji: '🇵🇹', patterns: [/\bPT\b/i, /葡萄牙/, /🇵🇹/] },
  { iso: 'IT', zh: '意大利', en: 'Italy', emoji: '🇮🇹', patterns: [/\bIT\b/i, /意大利/, /🇮🇹/] },
  { iso: 'CH', zh: '瑞士', en: 'Switzerland', emoji: '🇨🇭', patterns: [/\bCH\b/i, /瑞士/, /🇨🇭/] },
  { iso: 'AT', zh: '奥地利', en: 'Austria', emoji: '🇦🇹', patterns: [/\bAT\b/i, /奥地利/, /🇦🇹/] },
  { iso: 'BE', zh: '比利时', en: 'Belgium', emoji: '🇧🇪', patterns: [/\bBE\b/i, /比利时/, /🇧🇪/] },
  { iso: 'IE', zh: '爱尔兰', en: 'Ireland', emoji: '🇮🇪', patterns: [/\bIE\b/i, /爱尔兰/, /🇮🇪/] },
  { iso: 'CZ', zh: '捷克', en: 'Czechia', emoji: '🇨🇿', patterns: [/\bCZ\b/i, /捷克/, /🇨🇿/] },
  { iso: 'HU', zh: '匈牙利', en: 'Hungary', emoji: '🇭🇺', patterns: [/\bHU\b/i, /匈牙利/, /🇭🇺/] },
  { iso: 'RO', zh: '罗马尼亚', en: 'Romania', emoji: '🇷🇴', patterns: [/\bRO\b/i, /罗马尼亚/, /🇷🇴/] },
  { iso: 'GR', zh: '希腊', en: 'Greece', emoji: '🇬🇷', patterns: [/\bGR\b/i, /希腊/, /🇬🇷/] },
  { iso: 'IL', zh: '以色列', en: 'Israel', emoji: '🇮🇱', patterns: [/\bIL\b/i, /以色列/, /🇮🇱/] },
  { iso: 'AE', zh: '阿联酋', en: 'United Arab Emirates', emoji: '🇦🇪', patterns: [/\bAE\b/i, /阿联酋/, /🇦🇪/] },
  { iso: 'SA', zh: '沙特阿拉伯', en: 'Saudi Arabia', emoji: '🇸🇦', patterns: [/\bSA\b/i, /沙特/, /沙特阿拉伯/, /🇸🇦/] },
  { iso: 'ZA', zh: '南非', en: 'South Africa', emoji: '🇿🇦', patterns: [/\bZA\b/i, /南非/, /🇿🇦/] },
  { iso: 'EG', zh: '埃及', en: 'Egypt', emoji: '🇪🇬', patterns: [/\bEG\b/i, /埃及/, /🇪🇬/] },
  { iso: 'NG', zh: '尼日利亚', en: 'Nigeria', emoji: '🇳🇬', patterns: [/\bNG\b/i, /尼日利亚/, /🇳🇬/] },
  { iso: 'KE', zh: '肯尼亚', en: 'Kenya', emoji: '🇰🇪', patterns: [/\bKE\b/i, /肯尼亚/, /🇰🇪/] },
  { iso: 'MA', zh: '摩洛哥', en: 'Morocco', emoji: '🇲🇦', patterns: [/\bMA\b/i, /摩洛哥/, /🇲🇦/] },
  { iso: 'PK', zh: '巴基斯坦', en: 'Pakistan', emoji: '🇵🇰', patterns: [/\bPK\b/i, /巴基斯坦/, /🇵🇰/] },
  { iso: 'BD', zh: '孟加拉国', en: 'Bangladesh', emoji: '🇧🇩', patterns: [/\bBD\b/i, /孟加拉/, /🇧🇩/] },
  { iso: 'LK', zh: '斯里兰卡', en: 'Sri Lanka', emoji: '🇱🇰', patterns: [/\bLK\b/i, /斯里兰卡/, /🇱🇰/] },
  { iso: 'NP', zh: '尼泊尔', en: 'Nepal', emoji: '🇳🇵', patterns: [/\bNP\b/i, /尼泊尔/, /🇳🇵/] },
  { iso: 'KH', zh: '柬埔寨', en: 'Cambodia', emoji: '🇰🇭', patterns: [/\bKH\b/i, /柬埔寨/, /🇰🇭/] },
  { iso: 'LA', zh: '老挝', en: 'Laos', emoji: '🇱🇦', patterns: [/\bLA\b/i, /老挝/, /🇱🇦/] },
  { iso: 'MM', zh: '缅甸', en: 'Myanmar', emoji: '🇲🇲', patterns: [/\bMM\b/i, /缅甸/, /🇲🇲/] },
]

export const GLOBAL_ENTRY = { iso: 'GLOBAL', zh: '全球', en: 'Global', emoji: '🌍' }

const REGISTRY = Object.fromEntries(COUNTRY_DEFS.map((c) => [c.iso, c]))

export const CODE_ALIASES = { UK: 'GB', USA: 'US' }
export const FLAG_CODE_ALIASES = { UM: 'US' }

const KNOWN_ISO = new Set(COUNTRY_DEFS.map((c) => c.iso))

export const REGION_KEYWORDS = COUNTRY_DEFS.map((c) => ({
  code: c.iso,
  patterns: c.patterns || [
    new RegExp(`\\b${c.iso}\\b`, 'i'),
    new RegExp(c.zh),
    new RegExp(c.emoji),
    ...(c.aliases || []).map((a) => new RegExp(`\\b${a}\\b`, 'i')),
  ],
}))

export function normalizeCountryCode(code) {
  if (!code) return 'GLOBAL'
  const upper = String(code).toUpperCase()
  if (CODE_ALIASES[upper]) return CODE_ALIASES[upper]
  if (upper === 'UK') return 'GB'
  if (/^[A-Z]{2}$/.test(upper) && KNOWN_ISO.has(upper)) return upper
  if (upper === 'GLOBAL') return 'GLOBAL'
  return 'GLOBAL'
}

export function getCountry(code) {
  const iso = normalizeCountryCode(code)
  if (iso === 'GLOBAL') return GLOBAL_ENTRY
  return REGISTRY[iso] || GLOBAL_ENTRY
}

export function hasFlagAsset(code) {
  const iso = normalizeCountryCode(code)
  return iso !== 'GLOBAL' && KNOWN_ISO.has(iso)
}

export function getFlagAssetPath(code) {
  const iso = normalizeCountryCode(code)
  if (iso === 'GLOBAL' || !KNOWN_ISO.has(iso)) return GLOBAL_FLAG_PATH
  return `${FLAG_ASSET_PREFIX}/${iso.toLowerCase()}.svg`
}

export function getFlagWatermarkTuning(code) {
  const iso = normalizeCountryCode(code)
  const entry = REGISTRY[iso]
  if (entry?.flagFocus) {
    return {
      x: entry.flagFocus.x,
      y: entry.flagFocus.y,
      scaleMin: entry.flagFocus.scaleMin ?? 0.7,
      scaleMax: entry.flagFocus.scaleMax ?? 0.74,
    }
  }
  let h = 0
  for (const c of iso || '') h = (h + c.charCodeAt(0)) % 24
  return { x: `${46 + h}%`, y: '50%', scaleMin: 0.68, scaleMax: 0.72 }
}

export { COUNTRY_DEFS, KNOWN_ISO, REGISTRY }

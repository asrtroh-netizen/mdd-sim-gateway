// Vite-dev screenshot aid only. Production builds drop `import.meta.env.DEV`, so this
// never changes a shipped WebUI. Open `/?ui-preview=1#/overview` after `npm run dev`.

export const isUiPreview = () => (
  import.meta.env.DEV && new URLSearchParams(window.location.search).has('ui-preview')
)

export const previewAuth = { configured: true, authenticated: true, csrf: 'preview' }

export const previewSystemMeta = {
  version: '1.5.2',
  repository_url: 'https://github.com/asrtroh-netizen/mdd-sim-gateway',
}

export const previewCalls = [
  { id: 'c1', peer: '+15550001999', direction: 'in', status: 'answered', start_ts: 1756200000, transport: 'vowifi' },
  { id: 'c2', peer: '+15550001888', direction: 'out', status: 'missed', start_ts: 1756192800, transport: 'vowifi' },
  { id: 'c3', peer: '10086', direction: 'out', status: 'answered', start_ts: 1756106400, transport: 'vowifi' },
]

export const previewSettings = {
  timezone: 'Europe/London', bind: '0.0.0.0', http_port: 8443, ring_timeout: 35,
  max_sim_lines: 5, allow_external_sip: false, allow_telegram_commands: false, persist_asterisk_debug: false,
  tls: { self_signed: true, domain: '', cert_path: '', key_path: '' },
  retry: { max: 3, interval: 30 }, rekey: { minutes: 30 },
  security: { audit_enabled: true, trusted_proxies: [] },
  device_defaults: { cellular_enabled: true, vowifi_enabled: true },
  hardware: { modem_backend: 'auto' },
  updates: { proxy_mode: 'auto', update_mode: 'notify', version_scope: 'all' },
  proxy: { profiles: {}, exits: {} },
  vm_enabled: true, vm_ring_seconds: 25, vm_max_seconds: 120,
}

export const previewStatus = {
  version: '1.5.2',
  security: { https: true, certificate_mode: 'self-signed' },
  backups: [{ name: 'mdd-2026-08-26', size: 2_458_000, created_at: 1756200000 }],
}

export const previewInstances = [
  {
    id: '1', name: 'Example Mobile', carrier: 'Example Mobile', msisdn: '+15550000123', iccid: '8900000000000000123',
    status: { state: 'OK', label: 'Working', activity: { current: 'IMS registered; monitoring the line.' } },
    last_sms: { instance: '1', transport: 'vowifi', requested_transport: 'auto', ok: true, uncertain: false, error: '', ts: 1756200000 },
  },
  {
    id: '2', name: 'Example Mobile', carrier: 'Example Mobile', msisdn: '+15550000456', iccid: '8900000000000000456',
    status: { state: 'OK', label: 'Working', activity: { current: 'IMS registered; monitoring the line.' } },
  },
]

export const previewCards = [
  { present: true, matched: '1', name: 'Demo Modem', display_name: 'Demo Modem', iccid: '8900000000000000001' },
  { present: true, matched: '2', name: 'Demo Reader', display_name: 'Demo Reader', iccid: '8900000000000000002' },
]

export const previewDevices = [
  {
    id: 'demo-modem', name: 'Demo Modem', instance_id: '1', device_type: 'modem', present: true,
    sim: { name: 'Example Mobile', number: '+15550000123', iccid: '8900000000000000123', present: true, carrier: { name: 'Example Mobile', plmn: '234-99' } },
    status: { state: 'OK', label: 'Working', activity: { current: 'IMS registered; monitoring the line.' } },
    capabilities: {
      cellular: { desired: true, actual: 'on' },
      vowifi: { desired: true, actual: 'on', reason: 'Working — connected to the carrier over Wi-Fi.' },
    },
    vowifi: { ims: 'Registered', epdg: { pcscf: true } },
    egress: { node: 'London 01', pinned_node: 'London 01', detected_country: 'United Kingdom' },
    cellular: { registration: 'Registered', operator: 'Example Mobile', signal: 78, rsrp: -92 },
  },
  {
    id: 'demo-reader', name: 'Demo Reader', instance_id: '2', device_type: 'reader', present: true,
    sim: { name: 'Example Mobile', number: '+15550000456', iccid: '8900000000000000456', present: true, carrier: { name: 'Example Mobile', plmn: '234-99' } },
    status: { state: 'OK', label: 'Working', activity: { current: 'IMS registered; monitoring the line.' } },
    capabilities: {
      cellular: { desired: false, actual: 'unsupported' },
      vowifi: { desired: true, actual: 'on' },
    },
    vowifi: { ims: 'Registered' },
    egress: { node: 'New York 02', pinned_node: 'New York 02', detected_country: 'United States' },
  },
]

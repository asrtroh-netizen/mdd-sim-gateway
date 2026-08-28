import React, { useMemo } from 'react'
import SemanticCardLayers from './SemanticCardLayers.jsx'
import SemanticCardLogo from './SemanticCardLogo.jsx'
import { proxyFlagWatermarkStyle, resolveDeviceVisual } from '../semanticVisual.js'

/**
 * React port of OneBoard CountryNodeCard structure, bound to MDD line data.
 * Does not import the Vue SFC.
 */
export default function LineCountryCard({
  device,
  title,
  compact = false,
  active = false,
  selectable = false,
  muted = false,
  statusLabel,
  statusClass = 'success',
  aside,
  children,
  onClick,
}) {
  const { visual, code } = useMemo(() => resolveDeviceVisual(device), [device])
  const displayCode = !code || code === 'GLOBAL' ? '' : (code === 'GB' ? 'UK' : code)
  const nodeAtmospheric = compact ? active : true

  return (
    <SemanticCardLayers
      visual={visual}
      rootTag={selectable ? 'button' : 'article'}
      atmospheric={!compact}
      nodeAtmospheric={nodeAtmospheric}
      className={[
        'country-node-card',
        compact ? 'is-compact' : 'mdd-line-card',
        selectable ? 'is-selectable' : '',
        active ? 'is-active' : '',
        muted ? 'is-muted' : '',
        'variant-node',
      ].filter(Boolean).join(' ')}
      data-region={code}
      style={proxyFlagWatermarkStyle(code)}
      type={selectable ? 'button' : undefined}
      tabIndex={selectable ? 0 : undefined}
      onClick={onClick}
    >
      <div className="cnc-body">
        <div className="cnc-info">
          <div className="cnc-info-row">
            <SemanticCardLogo visual={visual} alt={title} />
            <div className="cnc-meta">
              <span className="cnc-title">{title}</span>
              {displayCode ? <span className="cnc-code">{displayCode}</span> : null}
            </div>
          </div>
        </div>
        <div className="cnc-aside">
          {aside}
          {statusLabel ? (
            <span className={`cnc-status ${statusClass}`}>
              <span className="cnc-status-dot" />
              {statusLabel}
            </span>
          ) : null}
        </div>
      </div>
      {children}
    </SemanticCardLayers>
  )
}

export function DeviceFlagLogo({ device, alt = '' }) {
  const { visual } = useMemo(() => resolveDeviceVisual(device), [device])
  return <SemanticCardLogo visual={visual} alt={alt} />
}

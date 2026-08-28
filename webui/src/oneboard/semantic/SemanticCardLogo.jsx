import React from 'react'

/** React port of OneBoard SemanticCardLogo.vue */
export default function SemanticCardLogo({ visual, alt = '' }) {
  return (
    <span
      className={`semantic-card-logo${visual?.kind === 'flag' ? ' is-flag' : ' is-brand'}`}
    >
      <img src={visual?.logo} alt={alt} loading="lazy" />
    </span>
  )
}

import React from 'react'

/** React port of OneBoard SemanticCardLayers.vue */
export default function SemanticCardLayers({
  visual,
  rootTag = 'article',
  atmospheric = false,
  nodeAtmospheric = false,
  className = '',
  style,
  children,
  ...attrs
}) {
  const Tag = rootTag
  const classes = [
    'semantic-card-layers',
    'has-semantic-bg',
    `semantic-${visual?.category || 'country'}`,
    visual?.kind === 'flag' ? 'is-flag' : 'is-semantic',
    atmospheric ? 'is-atmospheric' : '',
    nodeAtmospheric ? 'is-node-atmo' : '',
    className,
  ].filter(Boolean).join(' ')

  return (
    <Tag
      className={classes}
      data-semantic-theme={visual?.theme}
      data-semantic-category={visual?.category}
      style={style}
      {...attrs}
    >
      <img
        className="semantic-card-layers__bg cine-bg-flag"
        src={visual?.background}
        alt=""
        loading="lazy"
        aria-hidden="true"
      />
      <div className="semantic-card-layers__overlay cine-flag-blend" aria-hidden="true" />
      <div className="semantic-card-layers__noise" aria-hidden="true" />
      <div className="semantic-card-layers__rim" aria-hidden="true" />
      <div className="semantic-card-layers__content">{children}</div>
    </Tag>
  )
}

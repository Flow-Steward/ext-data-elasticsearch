# Elasticsearch official product icon

Source: https://raw.githubusercontent.com/elastic/eui/b62846be1b9b08d44ad4222e72f6dededacca478/packages/eui/src/components/icon/svgs/logo_elasticsearch.svg
Reference: https://eui.elastic.co/docs/components/display/icons/
Retrieved 2026-11-03. Official Elastic-owned GitHub repository, immutable revision.
Source SHA-256: `da0f9bd7336d98ec56fad9f5079816eb62433c73df4958c7e9c13ac2bb9794e1`.

The catalog previously used the third-party Simple Icons monochrome interpretation. This replacement uses Elastic's original SVG paths and yellow/turquoise fills unchanged. The middle path depends on Elastic's euiIcon__fillNegative CSS. For faithful light-mode rendering the class resolves to euiTheme.colors.darkestShade, SEMANTIC_COLORS.shade120, PRIMITIVE_COLORS.blueGrey120 = #2B394F. This is an official rendering rule, not an invented recoloring. References at the same pinned revision:

- https://github.com/elastic/eui/blob/b62846be1b9b08d44ad4222e72f6dededacca478/packages/eui/src/components/icon/icon.styles.ts
- https://github.com/elastic/eui/blob/b62846be1b9b08d44ad4222e72f6dededacca478/packages/eui-theme-borealis/src/variables/colors/_colors_light.ts
- https://github.com/elastic/eui/blob/b62846be1b9b08d44ad4222e72f6dededacca478/packages/eui-theme-borealis/src/variables/colors/_semantic_colors.ts
- https://github.com/elastic/eui/blob/b62846be1b9b08d44ad4222e72f6dededacca478/packages/eui-theme-borealis/src/variables/colors/_primitive_colors.ts

The render SVG adds only that resolved CSS fill. It retains every original path, viewBox, transform and explicit fill. ImageMagick rasterization at density 2304, proportional resize to 224x224, transparent padding centered to 256x256:

`magick -background none -density 2304 icon.svg -resize 224x224 -gravity center -extent 256x256 icon.png`

Prepared PNG SHA-256: `062e8e5ff64d67ecbb2148e72f0e4879c29de1d55dfbef49e5804362050b35a3`.
Visually inspected at 256x256. No generated or traced artwork.

Copyright Elasticsearch B.V. EUI source header provides Elastic License 2.0 or SSPL v1; trademarks remain with their owner. Official EUI documentation permits these logos when referencing Elastic products. This identifies the supported Elasticsearch product and does not imply endorsement.

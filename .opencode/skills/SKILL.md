---
name: awesome-design-md
description: Use when designing or implementing UI and the user wants design-system inspiration, DESIGN.md guidance, a style reference from a known product/site, or help choosing/copying/adapting a DESIGN.md from VoltAgent/awesome-design-md. Supports browsing the bundled index and fetching specific DESIGN.md references when needed.
---

# Awesome DESIGN.md

This skill helps use the VoltAgent `awesome-design-md` collection as design-system inspiration for UI work.

The repository is not a native Codex skill; it is a curated index of `DESIGN.md` references inspired by well-known websites and products. Use it as a reference layer when selecting a visual direction, creating a project `DESIGN.md`, or guiding UI implementation.

## Workflow

1. Identify the product context and desired UI feel.
2. Read `references/awesome-design-md-README.md` to find candidate design references by category.
3. If a specific reference is needed, inspect `references/design-md-index/<slug>/README.md` for its canonical `getdesign.md` URL.
4. If the local reference only points to a URL and the task requires the full DESIGN.md content, fetch the linked `https://getdesign.md/<slug>/design-md` page.
5. Adapt the reference to the current product; do not copy another product's brand identity directly unless the user explicitly asks for that style study.
6. When creating frontend/UI code, translate the reference into concrete tokens: colors, typography, spacing, component shape, elevation, motion, density, and responsive behavior.

## Choosing References

Prefer references by product need:

- AI / developer tool: `claude`, `cursor`, `vercel`, `mintlify`, `replicate`, `voltagent`
- Dense dashboard / technical product: `clickhouse`, `sentry`, `posthog`, `mongodb`, `ibm`
- Polished productivity app: `linear.app`, `notion`, `raycast`, `superhuman`, `cal`
- Friendly consumer product: `airbnb`, `pinterest`, `spotify`, `intercom`
- Premium monochrome / cinematic: `apple`, `spacex`, `bugatti`, `tesla`

For Android or Material You apps, use these references only for mood and density. Respect the platform's Material 3 interaction patterns unless the user asks otherwise.

## Guardrails

- Do not blindly clone a brand's visual identity.
- Do not introduce a one-note palette just because a reference uses one; adapt for the app's domain.
- Prefer a small number of strong design tokens over copying every surface detail.
- If the user already has a design system, treat these references as secondary inspiration.
- If exact DESIGN.md content is not bundled locally, fetch it only when needed and cite the source URL in any design rationale.

## Bundled References

- `references/awesome-design-md-README.md`: upstream collection index and usage notes.
- `references/design-md-index/`: local mirror of the repo's per-site README links.

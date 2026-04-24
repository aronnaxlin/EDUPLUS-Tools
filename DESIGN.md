# EDUPLUS Web UI Design

## Visual Theme & Atmosphere

- Claude-inspired warmth adapted for a utility product, not a chat clone.
- The interface should feel editorial, calm, and trustworthy rather than dashboard-heavy.
- Surfaces use parchment and cream tones with soft translucency.
- Operational areas such as logs use a contrasting dark code surface.

## Color Palette & Roles

- Background: `#f5efe6`
- Soft background: `#fbf7f2`
- Panel surface: `rgba(255, 251, 246, 0.84)`
- Main text: `#2d211c`
- Muted text: `#715d53`
- Accent: `#c46e4d`
- Accent strong: `#ab593b`
- Success: `#3d7a56`
- Danger: `#9e4b45`
- Log surface: `#241b17`
- Log text: `#f7efe7`

## Typography Rules

- Use clean system sans-serif typography for UI controls and body copy.
- Use tight tracking and larger display sizes in hero headlines.
- Use monospace only for job identifiers and logs.

## Component Stylings

- Panels are rounded, lightly bordered, and softly elevated.
- Primary buttons are terracotta gradient pills.
- Secondary buttons are neutral light surfaces with subtle outlines.
- Inputs are warm light fields with visible accent focus rings.
- Status badges communicate server state and job state with restrained semantic color.

## Layout Principles

- Desktop layout uses a two-column shell: narrative sidebar and main operational canvas.
- Mobile collapses into a single column with the sidebar becoming an intro section.
- Avoid noisy iconography or dense table layouts.
- Preserve readable whitespace around forms and logs.

## Depth & Elevation

- Use one primary shadow token for panels and accent cards.
- Avoid stacked heavy shadows.
- Visual hierarchy should come more from spacing and tonal contrast than depth.

## Responsive Behavior

- Collapse sidebar below `960px`.
- Form fields stack to one column on smaller screens.
- Keep action buttons large enough for touch.

## Do's and Don'ts

- Do keep the interface warm, quiet, and text-forward.
- Do separate operational logs visually from form controls.
- Do not copy Claude branding, logos, or exact layouts.
- Do not use neon accents, glassmorphism overload, or enterprise dashboard chrome.

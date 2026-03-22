# Visual Identity — LaPapelera
*Project-specific context handed to ArtDirector and TeamLead at runtime.*
*Edit this file per project. Do not bake into manifests.*

## Brand
LaPapelera — premium cardboard packaging company.
Scheduling assistant for their internal operations team.

## Mood
Tactile, trustworthy, premium. The feeling of handling a well-made box —
kraft paper, ink stamps, clean production labels. Not rustic or craft-fair.
Think luxury packaging: the cardboard that comes with an expensive product.

Material references: premium kraft paper, rubber stamp ink impressions,
corrugated texture, clean production labels, warm warehouse light.

It must NOT feel like: Amazon shipping, craft fair, generic brown corporate,
overly rustic or handmade. It should feel like a company that takes pride
in their product.

## Color System — use these Tailwind classes exactly

### Backgrounds
- Page background:     bg-amber-50       (#fffbeb) — warm near-white
- Card/surface:        bg-amber-100      (#fef3c7) — kraft paper warmth
- Input/chat area:     bg-stone-50       (#fafaf9) — clean writing surface
- Hero overlay:        bg-amber/80               — dark overlay on hero image

### Text — CONTRAST CRITICAL
- Primary headings:    text-stone-900    (#1c1917) — near black, 18:1 on amber-50
- Body text:           text-stone-700    (#44403c) — dark brown, 9:1 on amber-50
- Secondary/labels:    text-stone-500    (#78716c) — medium, 4.6:1 on amber-50
- On dark/hero:        text-amber-50     (#fffbeb) — light on dark backgrounds

### Borders and dividers
- Strong border:       border-stone-400  (#a8a29e)
- Subtle border:       border-stone-200  (#e7e5e4)
- Stamp accent:        border-stone-800  (#292524)

### Interactive — deliberate break from earth tones for contrast
- Primary button bg:   bg-stone-800      (#292524) — near black
- Primary button text: text-amber-50     (#fffbeb) — warm white
- Button hover:        bg-stone-900      (#1c1917)
- Focus ring:          ring-stone-800

### Do NOT use these combinations — contrast failures
- text-amber-* on bg-amber-*   (same family, low contrast)
- text-stone-400 on bg-amber-50 (fails 4.5:1)
- text-amber-700 on bg-amber-100 (brown on brown — Garden Tracker mistake)
- Any earth tone text on earth tone background

## Typography

### Fonts
- Headings: font-serif (Georgia or system serif)
- Body/UI:  font-sans  (system sans)
- Labels:   font-mono  (stamp/label feel for metadata)

### Scale
- Hero headline:  text-5xl font-serif font-bold text-stone-900
- Section head:   text-2xl font-serif font-semibold text-stone-900
- Body:           text-base font-sans text-stone-700
- Label/caption:  text-sm font-mono text-stone-500 uppercase tracking-widest

## Layout
- Dense, not sparse. Content should feel substantial.
- Max content width: max-w-6xl mx-auto
- Section padding: py-12 px-8 (not py-24 — avoid empty feel)
- Card padding: p-6
- Grid gap: gap-6

## Placement type → treatment
| placement   | treatment                                                     |
|-------------|---------------------------------------------------------------|
| hero bg     | full bleed image, dark overlay, light text on top            |
| calendar    | bg-amber-100 border-stone-400, serif labels, grid structure  |
| chat/notes  | bg-stone-50 border-stone-300, mono font for metadata         |
| buttons     | bg-stone-800 text-amber-50, never amber-on-amber             |

## AI Asset Generation
Hero background: premium kraft paper texture, macro photography feel,
warm amber and brown tones, subtle corrugated texture visible,
NOT dark, NOT busy — this sits BEHIND text so must be subtle enough
that stone-900 text or amber-50 text reads clearly on top of it.
Guidance: "texture only, no objects, seamless, muted, photorealistic"
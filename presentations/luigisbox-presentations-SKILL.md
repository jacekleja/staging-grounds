---
name: luigisbox-presentations
description: Use when creating LuigisBox client presentations, AB test reports, pitch decks, or any PPTX with Luigi's Box branding. Triggers on "presentation", "slides", "AB test report", "client deck", "LuigisBox PPTX".
---

# LuigisBox Presentations

## Overview

Create professional LuigisBox-branded presentations following official brand guidelines. This skill covers AB test reports, client pitches, and internal presentations.

Source: **LBX Master Deck Template** (167 slides, 2 slide masters, 38 layouts).

## Master Template Guidelines

These rules come verbatim from the official LBX Master Deck Template (slides 2–7). Follow them literally.

### Visual Consistency Tips (Slide 3)

| Rule | Description |
|------|-------------|
| **Use existing slides** | Feel free to copy relevant slides from this presentation to maintain a consistent look and feel. |
| **Maintain brand elements** | Do not change fonts or colors—please preserve the existing visual style. |
| **Consistent formatting** | Ensure text alignment, spacing, and layout remain clean and consistent throughout. |
| **Brand-aligned visuals** | Use icons and imagery that align with the brand's visual identity and overall tone. |

### Text Rules (Slide 4)

1. Use **Title Case** for main headlines & CTAs.
2. Use **Sentence case** for everything else.
3. Use **"Inter"** font everywhere.
4. Stick to **2–3 font sizes** per slide.
5. Stick to max. **3–6 bullet points** per slide.
6. Don't put too much text on each slide.

### Slide Colors and Emphasis (Slide 5)

| Context | Rule |
|---------|------|
| **Dark background slides** | Use for intro & section headlines. |
| **Light background slides** | Use for regular content. |
| **Headline highlight on light BG** | Use **#2DA1BA** color. |
| **Headline highlight on dark BG** | Use **#35C2D6** color (brand teal). |

### Logo and Pictogram Placement (Slide 6)

| Element | When | Where |
|---------|------|-------|
| **Primary logo** (icon + "LUIGI'S BOX" text) | On intro slides or dark section divider slides with headings | Top-left corner |
| **Pictogram** (icon only) | On regular content slides with a light background | Top-right corner |

### Alignment Rules (Slide 7)

1. Use a **consistent layout grid** to align all elements. Avoid placing items randomly on the slide.
2. Headlines and logos should be aligned **the same way on every slide** to maintain visual consistency.
3. Use presentation tools' **smart guides** to help you snap items into place.

## Brand Specifications

### Color Palette — Primary Theme ("Luigi's Box Template")

This is the **official** color scheme from the master template (Theme 1, "Simple Light").

| Scheme Name | Hex | Usage |
|-------------|-----|-------|
| **dk1** | `#0D0D0F` | Near-black — primary text on light BG |
| **lt1** | `#F4FBFC` | Near-white — text on dark BG, contact info |
| **dk2** | `#111A21` | Dark navy — secondary text |
| **lt2** | `#4E6172` | Muted blue-gray — captions, footnotes |
| **accent1** | `#35C2D6` | **LuigisBox brand teal** — accent bar, headline highlight on dark BG |
| **accent2** | `#2DA1BA` | **Darker teal** — headline highlight on light BG, labels, citations |
| **accent3** | `#007E90` | Deep teal |
| **accent4** | `#C6F0F5` | Light teal — card/box fills |
| **accent5** | `#E5F8FA` | Very light teal — card/box fills |
| **accent6** | `#E22077` | Magenta/pink — special accent |
| **hlink** | `#2DA1BA` | Hyperlink color |
| **folHlink** | `#0097A7` | Followed hyperlink |

**Fonts:** Arial (theme default), **Inter** (used on all actual slides — always use Inter).

### Color Palette — Legacy Theme (AB Test Report Overrides)

These colors from Theme 2 are used specifically for **data/metric slides** (positive/negative values, tables). They complement the primary theme for AB test reports.

| Color | Hex | Usage |
|-------|-----|-------|
| **green** | `#50B432` | Positive metric values (+3.47%, +390K) |
| **orange** | `#ED561B` | Negative metric values (-2.1%) |

### Dark Background — Gradient (REQUIRED)

Dark slides MUST use a **top-to-bottom gradient**, not a flat color:

| Position | Hex | Description |
|----------|-----|-------------|
| **Top** | `#0D0E0F` | Near-black |
| **Bottom** | `#0C272F` | Dark teal-tinted |

This subtle gradient gives dark slides depth and aligns with the master template's visual style. Never use a flat dark background.

**pptxgenjs implementation:**

> **IMPORTANT:** pptxgenjs does NOT support gradient fills on shapes. `ShapeFillProps.type` only accepts `'solid'` or `'none'`. Setting `type: 'linear'`, `stops`, `direction` is **silently ignored** and produces a flat color. You MUST use a pre-rendered gradient PNG as the slide background image instead.

**Pre-built asset:** `/assets/dark-gradient-bg.png` (1000×562px, #0D0E0F top → #0C272F bottom) is ready to use.

```javascript
function addDarkGradient(s) {
  // pptxgenjs cannot do gradient shape fills — use pre-rendered PNG
  s.background = {
    path: path.join(ASSETS_DIR, 'dark-gradient-bg.png'),
  };
}
```

**If the gradient asset is missing or needs regeneration**, create it with sharp:

```javascript
import sharp from 'sharp';

const width = 1000, height = 562; // must be full-size, not a thin strip
const topR = 0x0D, topG = 0x0E, topB = 0x0F;
const botR = 0x0C, botG = 0x27, botB = 0x2F;
const buf = Buffer.alloc(width * height * 3);
for (let y = 0; y < height; y++) {
  const t = y / (height - 1);
  const r = Math.round(topR + (botR - topR) * t);
  const g = Math.round(topG + (botG - topG) * t);
  const b = Math.round(topB + (botB - topB) * t);
  for (let x = 0; x < width; x++) {
    const idx = (y * width + x) * 3;
    buf[idx] = r; buf[idx+1] = g; buf[idx+2] = b;
  }
}
await sharp(buf, { raw: { width, height, channels: 3 } })
  .png()
  .toFile(path.join(ASSETS_DIR, 'dark-gradient-bg.png'));
```

> **Size matters:** The image must be full-size (1000×562 or larger). Narrow strips (e.g. 10px wide) may not render correctly as PowerPoint backgrounds.

### Typography (Exact Specifications from Template)

| Element | Font | Weight | Size | Notes |
|---------|------|--------|------|-------|
| **Main Title** | Inter | Bold | 45pt | Title/section slides |
| **Live Presentation Title** | Inter | Bold | 40pt | Intro slides with speaker |
| **Subheadline** | Inter | Regular | 20pt | Below main title on intro slides |
| **Slide Title** | Inter | Bold | 24pt | Content slide headers |
| **Feature Title** | Inter | Bold | 16pt | Feature card headers |
| **Body Text** | Inter | Regular | 12–13pt | Paragraph text, card descriptions |
| **Body Level 1** | Inter | Regular | 14pt | Standard bullet items |
| **Metric Big** | Inter ExtraBold | ExtraBold | 36–40pt | Key metrics (+X.X%) |
| **Metric Description** | Inter | Regular | 12pt | Labels under metrics |
| **Footnote/Caption** | Inter | Regular | 11pt | Notes, captions, table text |
| **Feature Description** | Inter | Regular | 9–11pt | Small text in feature cards |
| **Date/Author** | Inter | Regular | 14pt | Metadata on title slides |
| **Speaker Name** | Inter | Bold | 15pt | Name on intro/closing slides |
| **Speaker Title** | Inter | Regular | 12pt | Role/position on intro/closing slides |
| **Contact Info** | Inter SemiBold | SemiBold | 12pt | Phone, email on closing slides |
| **"luigisbox.com"** | Inter | Bold | 11pt | Top-right branding text |
| **Section Label** | Inter | Bold | 11pt | ALL CAPS labels (e.g., "WHAT YOU SEE") |

**Fallback:** Arial (theme default)

### Bullet Formatting (from Slide Master)

| Level | Bullet | Size | Color | Indent |
|-------|--------|------|-------|--------|
| 1 | ● (solid circle) | 14pt | accent2 (#2DA1BA teal) | 457200 EMU |
| 2 | ○ (empty circle) | 14pt | accent1 (#35C2D6 brand) | 914400 EMU |
| 3 | ■ (solid square) | 14pt | accent2 (#2DA1BA teal) | 1371600 EMU |

**Line spacing:** 115% for body text, 125% for card descriptions, 150% for bullet lists, 200% for spaced bullet lists (slide 4 style)

### Logo Usage

| Slide Type | Element | Position | Details |
|------------|---------|----------|---------|
| **Dark intro/section slides** | Full logo (icon + "LUIGI'S BOX") | Top-left, ~(0.65", 0.62"), 1.45"×0.49" | `logo-full-white.png` on dark BG |
| **Dark intro/section slides** | "luigisbox.com" text | Top-right, (6.32", 0.52"), right-aligned | Inter Bold 11pt, white |
| **Light content slides** | Pictogram (icon only) | Top-right, ~(9.03", 0.62"), 0.32"×0.32" | `icon-color.png` |

### Logo Assets

All logo files are in `/assets/` subdirectory of this skill.

**Full Logos (icon + text):**

| File | Description | Use On |
|------|-------------|--------|
| `logo-full-color.png` | Teal icon + teal "LUIGI'S BOX" text | Light backgrounds |
| `logo-full-dark.png` | Icon + dark/black text | Light backgrounds |
| `logo-full-white.png` | Icon + white text | Dark backgrounds |

**Icons Only (small flavor):**

| File | Description | Use On |
|------|-------------|--------|
| `icon-color.png` | Teal filled checkmark box | Light or dark backgrounds |
| `icon-dark.png` | Black filled checkmark box | Light backgrounds |
| `icon-white.png` | White checkmark box (transparent BG) | Dark backgrounds |

**Usage Guidelines (from Master Template Slide 6):**
- **Dark intro/section slides:** Full logo (`logo-full-white.png`) in top-left + "luigisbox.com" text in top-right
- **Dark CONTENT slides (summary, highlights, conclusions):** NO logo, NO "luigisbox.com" — only gradient + accent bar
- **Light content slides:** Pictogram only (`icon-color.png`) in top-right corner
- **Logo aspect ratio:** `logo-full-white.png` is 900×306px (ratio 2.94:1) — always preserve this ratio

**CRITICAL — Which dark slides get the logo:**

The full logo + "luigisbox.com" text appears ONLY on dark **intro/section/closing** slides — specifically:
- Title slide (first slide)
- Section divider slides (standalone heading slides between sections)
- Closing/contact slide (last slide)

Dark slides that contain **content** (bullet lists, metric cards, data) do NOT get the logo. These include:
- AB test summary (bullet list of test parameters)
- Result highlights (metric cards)
- Conclusions & next steps (bullet list of findings)

**Rule of thumb:** If a dark slide has a two-tone section title AND substantial content below it, it's a content slide — no logo. If a dark slide is primarily a big heading with minimal supporting text, it's an intro/section slide — add the logo.

### Feature & Benefit Icons

All icons are in `/assets/icons/` subdirectory of this skill. Teal rounded-rect background, white symbol. Size: 0.43"×0.43" on slides (300×300px or 400×400px source).

**Product Icons:**

| File | Symbol | Use For |
|------|--------|---------|
| `product-search.png` | Magnifying glass | Search product |
| `product-recommender.png` | Three overlapping circles | Recommender product |
| `product-listing.png` | 2×2 grid squares | Product Listing product |
| `product-analytics.png` | Pie/donut chart | Analytics product |
| `product-shopping-assistant.png` | Shopping bag with smile | Shopping Assistant product |

**Benefit Icons:**

| File | Symbol | Use For |
|------|--------|---------|
| `benefit-chart-growth.png` | Bar chart growing | Higher conversion, revenue growth |
| `benefit-smiley-ux.png` | Smiley face in circle | Better UX, customer experience |
| `benefit-cart.png` | Shopping cart | Higher cart value, additional sales |
| `benefit-trophy.png` | Trophy cup | Competitive advantage |
| `benefit-trend-up.png` | Line chart trending up | Better utilization, traffic growth |

**Special Icons:**

| File | Symbol | Use For |
|------|--------|---------|
| `ai-sparkle.png` | Three sparkle stars (teal, transparent BG) | "Powered by AI" tagline, 512×512px |
| `checkmark-teal.png` | Solid teal checkmark (no BG rect) | Bullet-style checkmark lists, 375×300px |
| `challenge-icon.png` | Teal rounded-rect icon | Challenge section header |
| `solution-icon.png` | Teal rounded-rect icon | Solution section header |
| `mission-icon.png` | Teal rounded-rect icon | Mission statements |
| `vision-icon.png` | Teal rounded-rect icon | Vision statements |
| `stat-icon-generic.png` | Teal rounded-rect icon | Generic stat/metric cards |
| `problem-link-magenta.png` | Magenta (#E22077) rounded-rect icon | Problem/error indicators |
| `browser-frame.png` | Mac browser window frame (traffic light dots, dark title bar, light gray body) | Layered screenshot slides (Layout 22), 2340×2020px |
| `gradient-band-bottom.png` | White-to-transparent gradient band | Bottom fade effect on layered screenshots, 1920×557px |

**Generic Feature Card Icons:**

| File | Symbol |
|------|--------|
| `generic-copy.png` | Copy/duplicate |
| `generic-lock.png` | Lock/security |
| `generic-align.png` | Alignment/layout |
| `generic-image.png` | Image/visual |

**Code Example (pptxgenjs):**
```javascript
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ASSETS_DIR = path.join(__dirname, '../../.claude/skills/luigisbox-presentations/assets');

// Dark slide: full logo top-left + "luigisbox.com" top-right
slide.addImage({
  path: path.join(ASSETS_DIR, 'logo-full-white.png'),
  x: 0.65, y: 0.62, w: 1.45, h: 0.49,
});
slide.addText('luigisbox.com', {
  x: 6.32, y: 0.52, w: 3.11, h: 0.38,
  fontSize: 11, fontFace: 'Inter', bold: true, color: 'FFFFFF', align: 'right',
});

// Light slide: pictogram top-right
slide.addImage({
  path: path.join(ASSETS_DIR, 'icon-color.png'),
  x: 9.03, y: 0.62, w: 0.32, h: 0.32,
});
```

### Client Brand Logos

28 client/partner logos in `/assets/brands/` subdirectory. Used on Logo Grid slides (Layout 26). See `/assets/brands/index.md` for full index with brand names.

**Key brands:** Notino, Alza.sk, Nespresso, Under Armour, O2, Skoda, Dr.Max, Datart, Mountfield, KiK, ZOOT, Answear, Pilulka, Muziker, and more.

**Logo size:** ~1.15"×0.86" each (transparent PNG). Grid positions match Layout 26 spec.

```javascript
// Example: add a client logo
slide.addImage({
  path: path.join(ASSETS_DIR, 'brands', 'notino.png'),
  x: 0.68, y: 1.23, w: 1.15, h: 0.86,
});
```

### Stock Photos & Product Screenshots

72 photos in `/assets/photos/` subdirectory, organized by source slide. See `/assets/photos/index.md` for full index with descriptions.

| Source | Count | Content | Use For |
|--------|-------|---------|---------|
| `slide111_*.png` | 24 | People + e-commerce overlays | Half-image content slides |
| `slide112_*.png` | 24 | UI screenshots, dashboards, mockups | Product deep-dives, feature slides |
| `slide113_*.png` | 24 | Professional portraits, people at work | Speaker slides, team pages |

**Photo size:** ~1060×1200px each (portrait orientation PNG).

```javascript
// Example: add a lifestyle photo on a half-image slide
slide.addImage({
  path: path.join(ASSETS_DIR, 'photos', 'slide113_06.png'),
  x: 5.01, y: 0.00, w: 4.99, h: 5.63,
});
```

### Accent Bar

- **Position:** Top-left corner, x=0, y=0
- **Size:** 0.63" × 0.05" (from master template)
- **Color:** LuigisBox brand teal (#35C2D6)
- **Required:** On every slide (dark and light)

### Slide Number

- **Position:** Bottom-right, (9.27", 5.10"), size 0.60"×0.43"
- **Alignment:** LEFT on master 0 layouts, RIGHT on master 1 layouts
- **Format:** "‹#›" (auto page number)

### Slide Dimensions

- **Standard:** 10.00" × 5.62" (16:9, `LAYOUT_16x9` in pptxgenjs)

## Slide Types

### 1. Title/Section Divider Slide (Dark Background)

Used for section breaks, topic introductions. Title centered.

```
Layout:
- Dark gradient background (#0D0E0F top → #0C272F bottom)
- Accent bar: top-left (0.63" × 0.05", #35C2D6)
- Full logo: logo-full-white.png at (0.65", 0.62"), 1.45"×0.49"
- "luigisbox.com": top-right (6.32", 0.52"), Inter Bold 11pt, white, right-aligned
- Title: centered, y:2.10, 45pt Inter Bold
  - Main text: white (#F4FBFC)
  - Accent text: brand teal (#35C2D6)
- Title Case for headlines
```

**Examples from master:**
- "Introduction **Slides**" (accent on "Slides")
- "Product **Deep-dive Slides**"
- "Problems & Solutions **Slides Examples**"
- "Closing **Slides**"

### 2. Intro Slide with Headline (Dark Background)

Title slide with headline + optional subheadline. Left-aligned.

```
Layout:
- Dark gradient background
- Accent bar + full logo + "luigisbox.com"
- Title: left-aligned at (0.52", 2.60"), 45pt Inter Bold
  - Line 1: white
  - Line 2: white
- Subheadline (optional): (0.52", 4.21"), 20pt Inter, accent color (#35C2D6)
```

### 3. Intro Slide with Speaker (Dark Background)

For live presentations. Shows speaker photo, name, and role.

```
Layout:
- Dark gradient background
- Accent bar + full logo + "luigisbox.com"
- Title: left-aligned at (0.52", 2.02"), 40pt Inter Bold
  - Line 1: white
  - Line 2: accent (#35C2D6) or white
- Speaker photo: circular, (0.62", 3.95"), 1.05"×1.05"
- Speaker name: Inter Bold 15pt, white, at (1.87", 4.10")
- Speaker title: Inter Regular 12pt, white, directly below name
```

**Variant — With subheadline:** Title at y:1.51, subheadline at y:3.03 (20pt, #35C2D6).

**Variant — Two speakers:** Two speaker blocks side by side, photos at (0.64", 4.08") and (4.58", 4.08"), 0.92"×0.92" each.

**Variant — Event with logo:** Same as speaker slide + event logo in top-right area, "luigisbox.com" moved to bottom-right.

### 4. AB Test Title Slide (Dark Background)

```
Layout:
- Dark gradient background (#0D0E0F top → #0C272F bottom)
- Accent bar: top-left (0.63" × 0.05", #35C2D6)
- Full logo: logo-full-white.png at (0.65", 0.62"), 1.45"×0.49"
- "luigisbox.com": top-right (6.32", 0.52"), Inter Bold 11pt, white
- Title: "A/B test report" (45pt Inter Bold, white) at x:0.6 y:1.8
- Subtitle: "[client] YYYY-MM [services]" (45pt Inter Bold, #35C2D6) at x:0.6 y:2.55
- Bottom-left: "[date]  [author], [role]" (14pt Inter, #8899AA) at x:0.6 y:4.6
```

### 5. Summary Slide (Dark Background)
```
Layout:
- Dark gradient background (#0D0E0F top → #0C272F bottom)
- Two-tone title: "AB test " (white) + "summary" (#35C2D6) at 24pt
- Bullet list (13pt Inter, 135% line spacing):
  - Level 1 bullets: ● (code 25CF), white text
  - Level 2 bullets: ○ (code 25CB), dimmed #8899AA text, 11pt
- Bullets at x:0.6 y:1.2, w:8.5
```

### 6. Result Highlights (Dark, 3-Card Layout)
```
Layout:
- Dark gradient background (#0D0E0F top → #0C272F bottom)
- Two-tone title: "Result " (white) + "highlights" (#35C2D6)
- Three white rounded rectangle cards (rectRadius: 0.12):
  - Card width: 2.8", height: 2.5", gap: 0.3" between cards
  - Starting at y:1.3
- Card content (all centered, NON-OVERLAPPING — see safe card template):
  - Big metric:  y+0.15, h:0.55 — 34pt Inter Bold, green (#50B432) for positive
  - Unit label:  y+0.70, h:0.30 — 18pt Inter Bold, teal (#2DA1BA)
  - Description: y+1.00, h:0.50 — 12pt Inter, teal (#2DA1BA)
  - Percentage:  y+1.55, h:0.35 — 16pt Inter Bold, green (#50B432) for positive

IMPORTANT: Big metric numbers use green (#50B432), NOT teal.
           Unit labels ("CZK", "AOV") use teal (#2DA1BA).
```

### 7. Results Slide (White, Metric Card + Table) — PREFERRED for data slides
```
Layout:
- White background (#FFFFFF)
- Two-tone title: main text (black #000000) + accent (#2DA1BA teal) at 24pt
- Info subtitle: "date range | N days | N users" (11pt Inter, #2DA1BA) at y:0.8
- LEFT: Metric card (rounded rect, 2.6"×1.5"):
  - Fill: #F0FFF0 (light green for positive), #FFF0F0 (light pink for negative)
  - Big metric: 36pt Inter ExtraBold, colored (#50B432 green or #ED561B orange)
  - Label: 11pt Inter, #666666, centered
- RIGHT: Data table at x:3.6, w:5.3":
  - Header row: fill with dark gradient or solid #0D0E0F, white bold text, 11pt
  - Data rows: white bg, black text, 11pt, right-aligned numbers
  - Borders: #DDDDDD solid 0.5pt
  - Difference column: color-coded bold (green #50B432 positive, orange #ED561B negative)
- Source citation: italic, 10pt, #2DA1BA teal
- Bottom notes: bold header (12pt #333333) + bulleted observations (11pt #555555, 140% line spacing)
```

### 8. Content Slide with Text + Image (Light Background)

Half text, half image. From master slides 28-38, 58-59.

```
Layout:
- White background
- Accent bar + pictogram (icon-color.png top-right)
- Two-tone title at (0.52", 0.48"): main text (black) + accent (#2DA1BA)
- Body text: left half (0.52", 1.14"), w:4.41", Inter 12pt, line spacing 1.25
  - Bold key phrases within body text
- Bullet list (optional): Inter 12pt, ● bullets, spacing before=127000
- Image: right half (~5.28", 0.41"), w:4.26"
```

### 9. Challenge/Solution Slide (Light Background)

Split layout with horizontal divider. From master slides 40-41.

```
Layout:
- White background
- Accent bar + pictogram
- TOP HALF:
  - Icon (0.63", 0.55"), 0.43"×0.43" (teal icon)
  - Title: "Challenge: ..." at (1.19", 0.47"), bold
  - Body: (1.22", 1.13"), 12pt, line spacing 1.25
- Horizontal line: (0.61", ~2.65"), full width
- BOTTOM HALF:
  - Icon (0.63", ~3.16"), 0.43"×0.43"
  - Title: "How Luigi's Box solves this" at (1.19", ~3.07"), bold
  - Body: (1.22", ~3.74"), 12pt, line spacing 1.25
```

### 10. Feature Highlight Cards (Light Background)

Variants for 2, 3, 4, 5, or 6 features. From master slides 50-55.

```
Common pattern:
- White background + accent bar + pictogram
- Title at (0.52", 0.48")
- Rounded rectangle cards with light fill (#C6F0F5 or #E5F8FA)
- Each card has: icon (0.43"×0.43") + title (Inter Bold 12pt) + description (Inter 9-11pt)
- Cards arranged in rows/columns depending on count

3-feature variant (most common):
- Three stacked horizontal cards, full width (8.72"×1.08")
- Icon left, title + description right
- Cards at y:1.32, y:2.55, y:3.78

4-feature variant:
- 2×2 grid of tall cards (2.05"×3.29")
- Icon centered, title centered below, description centered below title
```

### 11. Problem Statement Cards (Light Background)

Three stacked problem cards. From master slide 42.

```
Layout:
- White background + accent bar + pictogram
- Title at (0.52", 0.48")
- Three rounded rectangle cards, full width (8.74"×1.08")
  - Cards at y:1.32, y:2.55, y:3.78
  - Icon left (0.43"×0.43"), text right
  - Bold key phrases within each statement
```

### 12. "What You See / What It Reveals" Slide (Light Background)

Two-column insight mapping. From master slide 57.

```
Layout:
- White background + accent bar + pictogram
- Title with two-tone accent
- Left column header: "WHAT YOU SEE" (Inter Bold 11pt, ALL CAPS, teal)
- Right column header: "WHAT IT REVEALS" (Inter Bold 11pt, ALL CAPS, teal)
- Paired rows: left card (problem) → arrow → right card (insight)
- Cards with colored fills, Inter Bold 12pt white text
```

### 13. Conclusions Slide (Dark Background)
```
Layout:
- Dark gradient background (#0D0E0F top → #0C272F bottom)
- Two-tone title: "Conclusions & " (white) + "next steps" (#35C2D6)
- Hierarchical bullets at x:0.6 y:1.2, 150% line spacing:
  - Level 1: ● (25CF), 14pt Inter, white — key findings
  - Level 2: ○ (25CB), 14pt Inter, #8899AA — supporting details
```

### 14. Contact/Closing Slide (Dark Background)

Several variants from master slides 120-122.

**Variant A — Tagline + Contact (slide 120):**
```
Layout:
- Dark gradient background
- "luigisbox.com": top-right, Inter Bold 11pt, white
- Headline at (0.59", 1.89"): 25pt Inter, white + accent (#35C2D6)
  - e.g., "Trusted by over 4,000 e-commerce websites to drive better results..."
- Contact info at bottom:
  - Phone icon + number: Inter SemiBold 12pt
  - Email icon + address: Inter SemiBold 12pt
```

**Variant B — Thank You + Speaker (slide 121):**
```
Layout:
- Dark gradient background
- "luigisbox.com": top-right, Inter Bold 11pt, white
- Message at (0.52", 2.17"): 38pt Inter Bold
  - Line 1: white ("Thank you for participating.")
  - Line 2: accent #35C2D6 ("You're invited to the after party!")
- Speaker photo: (0.63", 3.95"), 1.05"×1.05"
- Speaker name: Inter Bold 15pt, white
- Speaker title: Inter Regular 12pt, white
```

**Variant C — Full Closing with Tagline (slide 122):**
```
Layout:
- Dark gradient background
- "luigisbox.com": top-right, Inter Bold 11pt, white
- Tagline box at (0.62", 1.95"), w:8.67":
  - "Don't let your customers leave frustrated." (22pt Inter Bold, white)
  - "With us, product search and discovery is lucrative" (22pt Inter Bold, #35C2D6)
  - "and intuitive." (22pt Inter Bold, #35C2D6)
- Speaker photo: (0.63", 3.95"), 1.05"×1.05"
- Speaker name + title to the right of photo
- Contact: phone + email at bottom, Inter SemiBold 12pt, #F4FBFC
```

### 15. Section Divider (Dark Background)
```
Layout:
- Dark gradient background (#0D0E0F top → #0C272F bottom)
- Accent bar + full logo + "luigisbox.com"
- Large title (45pt Inter Bold), centered:
  - Main text: white
  - Accent text: brand teal (#35C2D6)
```

### 16. Product Overview — 5-Card Row (Light or Dark Background)

Showcases LB product suite. Five cards in a row, each with icon + title + description. From master slides 15-16.

```
Layout:
- Light or dark background (both variants exist)
- Accent bar + branding (light: pictogram; dark: full logo)
- Title at (0.52", 0.48"): two-tone, 24pt Inter Bold
- Optional subtitle at (0.52", 1.12"), 12pt Inter

- Five rounded-rect cards in a row:
  - Card size: 1.65"×3.10"
  - Cards at x: 0.63, 2.39, 4.16, 5.92, 7.69; y: 1.84
  - Fill: #E5F8FA (veryLightTeal) on light BG; white on dark BG
  - Each card contains:
    - Icon: 0.43"×0.43" centered horizontally at y: card_y+0.54
    - Title: Inter Bold 12pt, centered at y: card_y+1.13
    - Description: Inter 9pt, centered at y: card_y+1.60, w: card_w-0.20"

- Optional footer: "All products powered by AI" + ai-sparkle.png icon
  - MUST be BELOW cards (cards end at 1.84+3.10 = 4.94")
  - Sparkle: 0.28"×0.28" at (~3.62", 5.00")
  - Text: 11pt Inter, at (3.92", 5.00")

Product icons (from /assets/icons/):
  Search → product-search.png
  Recommender → product-recommender.png
  Product Listing → product-listing.png
  Shopping Assistant → product-shopping-assistant.png
  Analytics → product-analytics.png
```

### 17. Benefits with Icon List + Image (Light Background)

Icon bullets on left, large image on right. From master slide 17.

```
Layout:
- White background + accent bar + pictogram
- Title at (0.52", 0.48"): two-tone, 24pt Inter Bold, w:8.33", h:1.00"
  (multi-line allowed — "The benefits of choosing\nLuigi's Box")
- Benefit rows (left half):
  - Each row: icon (0.43"×0.43") at x:0.63 + text at x:1.20, w:4.95"
  - Row spacing: 0.62" between rows (y: 1.78, 2.40, 3.02, 3.64, 4.26)
  - Text: Inter Bold 12pt, dark (#0D0D0F)
  - Typically 5 benefit items
- Image (right half): photo at (~6.56", 0.42"), w:2.98", h:4.83"
  - Rounded corners applied via image masking

Benefit icons (from /assets/icons/):
  Chart growth → benefit-chart-growth.png
  Smiley/UX → benefit-smiley-ux.png
  Cart → benefit-cart.png
  Trophy → benefit-trophy.png
  Trend up → benefit-trend-up.png
```

### 18. Four Boxes Grid — 2×2 (Light or Dark Background)

Four rounded-rect cards in a 2×2 grid, each with icon + title + description. From master slides 3, 18, 53.

```
Layout:
- Light or dark background + accent bar + branding
- Title at (0.52", 0.48"): 24pt Inter Bold
- Four cards in 2×2 grid:
  - Card size: 4.30"×1.77"
  - Positions: (0.63, 1.32), (5.05, 1.32), (0.63, 3.21), (5.05, 3.21)
  - Fill: #E5F8FA (veryLightTeal) on light BG; white on dark BG
  - Each card:
    - Icon: 0.43"×0.43" at (card_x+0.26, card_y+0.27)
    - Title: Inter Bold 12pt at (card_x+0.78, card_y+0.27), w:3.31"
    - Description: Inter 11pt at (card_x+0.78, card_y+0.66), w:3.31", h:0.95"
```

### 19. Four Stacked Rows with Checkmarks (Light Background)

Horizontal rows with checkmark/icon left and text right. From master slides 45, 47.

```
Layout:
- White background + accent bar + pictogram
- Title at (0.52", 0.48"): 24pt Inter Bold
- Four full-width rows:
  - Row size: 8.74"×0.80"
  - Rows at y: 1.30, 2.21, 3.14, 4.07; x: 0.60
  - Fill: #E5F8FA (veryLightTeal)
  - Checkmark/icon: 0.43"×0.43" at (row_x+0.23, row_y+0.18)
  - Text: at (row_x+0.75, row_y), w:7.70", h:0.80"
    - Bold key phrase + regular continuation, 12pt Inter

Use checkmark-teal.png or any feature icon from /assets/icons/.
```

### 20. Stats Cards — 3 Columns (Light Background)

Three tall cards with icon, big metric, and description. From master slides 20, 67, 68.

```
Layout:
- White background + accent bar + (optional) pictogram
- Title at (0.52", 0.47"): 24pt Inter Bold
- Three rounded-rect cards:
  - Card size: 2.72"×3.61"
  - Cards at x: 0.63, 3.62, 6.62; y: 1.38
  - Fill: #E5F8FA (veryLightTeal)
  - Each card:
    - Icon: 0.43"×0.43" centered at y: card_y+0.56
    - Big metric: Inter Bold 36pt, centered at y: card_y+1.25, h:0.96"
      - Color: #0D0D0F (nearBlack) for neutral stats; #50B432 for positive
    - Description: Inter 12pt, centered at y: card_y+2.07, h:0.87"
      - Color: #4E6172 (muted)
```

### 21. Stats Cards — 4 Columns (Light Background)

Four metric cards in a row with icons. From master slide 91.

```
Layout:
- White background + accent bar + pictogram
- Title at (0.52", 0.48"): 24pt Inter Bold, two-tone
- Four rounded-rect cards:
  - Card size: 2.05"×3.05"
  - Cards at x: 0.62, 2.84, 5.06, 7.29; y: 1.56
  - Fill: #E5F8FA (veryLightTeal)
  - Each card:
    - Icon: 0.43"×0.43" centered at y: card_y+0.55
    - Big metric: Inter Bold 36pt, centered at y: card_y+1.16, h:0.71"
      - Color: #0D0D0F (nearBlack)
    - Description: Inter 11pt, centered at y: card_y+1.92, h:0.79"
      - Color: #4E6172 (muted)
```

### 22. Text + Layered Screenshot (Light Background)

Text on left, layered product screenshot with shadow effect on right. From master slides 29, 31, 33, 35, 37, 83-86.

```
Layout:
- White background + accent bar + pictogram (pictogram may overlay on image)
- Title: (0.50", 0.65"), w:3.89", 24pt Inter Bold, two-tone
- Body text: (0.32", 1.66"), w:4.00", h:3.24", Inter 12pt, line spacing 1.25
  - Bullet points with ● bullets

- RIGHT: Layered screenshot group:
  - Mac browser frame: `browser-frame.png` at (4.26", 0.36"), w:5.74", h:5.26"
    (has red/yellow/green traffic light dots, dark title bar, light gray window body)
  - Screenshot image: (4.68", 1.02"), w:5.32", h:3.81" (inside the frame)
  - Bottom gradient bands for depth (RIGHT SIDE ONLY — don't cover left text): `gradient-band-bottom.png`
    - Band 1: (4.26", 2.88"), w:5.74", h:2.74"
    - Band 2: (4.26", 3.49"), w:5.74", h:2.14"

Assets: `browser-frame.png` and `gradient-band-bottom.png` are in /assets/.
The browser frame was extracted from master deck slide 29 (image20.png).
```

### 23. Half Image / Half Content (Light Background)

Full-bleed image on one half, text content on the other. From master slides 12-13, 66.

```
Layout:
- White background
- Image: (0.00", 0.00"), w:4.99", h:5.63" (left half, full bleed)
  OR: (5.01", 0.00"), w:4.99", h:5.63" (right half)
- Accent bar: (0.00", 0.00"), 0.63"×0.05" explicitly added on top
- Pictogram: top-right (9.03", 0.62")
- Title: on text side at (0.52", 0.48") or (5.45", 0.48"), 24pt Inter Bold
- Body: below title, 12pt Inter, line spacing 1.25

Variant — Image RIGHT (slide 14, Vision/Mission):
- Two sections side by side, each with icon + title + body
- Icon: 0.43"×0.43"
- Section label: Inter Bold 12pt, teal
```

### 24. Testimonial Cards — 3 per Slide (Light Background)

Three customer quotes in tall cards. From master slide 76.

```
Layout:
- White background + accent bar + (no pictogram)
- Title at (0.52", 0.48"): 24pt Inter Bold
- Three tall cards:
  - Card size: 2.72"×3.61"
  - Cards at x: 0.63, 3.62, 6.61; y: 1.37
  - Fill: #E5F8FA (veryLightTeal)
  - Each card:
    - Company logo: centered at y: card_y+0.38, w:1.01", h:0.20"
    - Quote: Inter 11pt, at y: card_y+0.77, w:2.34", centered
    - Speaker photo: 0.57"×0.56", centered at y: card_y+2.01
    - Speaker name: Inter Bold 11pt, centered at y: card_y+2.75
    - Speaker title: Inter 10pt, centered at y: card_y+3.05
```

### 25. Quote / Testimonial — Full Width (Light Background)

Large testimonial quote with quotation marks. From master slides 70, 78-79.

```
Layout:
- White background (no accent bar on some variants)
- Quote mark image: (0.27", 0.54"), 1.53"×1.19" (large open-quote graphic)
- Quote line 1: (0.78", 1.22"), w:8.31", Inter Bold 18pt, teal (#2DA1BA)
- Quote line 2: (0.81", 2.26"), w:8.07", Inter 13pt, line spacing 1.25
- Speaker photo: circular, (0.90", 3.95"), 0.90"×0.89"
- Speaker name: Inter Bold 11pt at (1.96", 4.07")
- Speaker title + company: Inter 11pt at (1.96", 4.29")
- Company logo: below, small (0.95"×0.19")
```

### 26. Logo Grid (Light Background)

Grid of client/partner logos. From master slide 81.

```
Layout:
- White background + accent bar
- Title at (0.52", 0.48"): 24pt Inter Bold
- 7×4 grid of logos:
  - Logo size: 1.15"×0.86" each
  - Starting at (0.68", 1.23")
  - Column spacing: 1.25" (x: 0.68, 1.93, 3.19, 4.44, 5.69, 6.95, 8.20)
  - Row spacing: 0.97" (y: 1.23, 2.20, 3.16, 4.14)
  - 28 logo slots total
```

### 27. Client Results Cards — 4 Columns (Dark Background)

Four metric cards with client logos on dark background. From master slide 80.

```
Layout:
- Dark gradient background
- Title at (0.52", 0.48"): 24pt Inter Bold, two-tone
- Four rounded-rect cards:
  - Card size: 2.05"×3.14"
  - Cards at x: 0.62, 2.84, 5.06, 7.29; y: 1.25
  - Fill: white
  - Each card:
    - Client logo: centered at y: card_y+0.40, w:1.15", h:0.30"
    - Big metric: Inter Bold 36pt, centered at y: card_y+1.06, #0D0D0F
    - Metric label: Inter 12pt, centered at y: card_y+1.74, #4E6172
    - Category label: Inter 11pt at y: card_y+2.79, #4E6172
- "More case studies" button: (0.63", 4.74"), rounded rect, teal fill
```

### 28. Before/After Screenshots (Light Background)

Full-size screenshot with badge overlay. From master slides 73-74.

```
Layout:
- White background (no accent bar)
- Screenshot: (0.65", 0.45"), w:8.70", h:4.75"
- Badge: rounded rect (1.04"×1.04") at (0.48", 0.81")
  - Fill: teal (#35C2D6) for "Before", green (#50B432) or teal for "After"
  - Text: Inter Bold 12pt, white, centered
```

### 29. Six Benefits Grid — Text Only (Light Background)

Six text items in a 3×2 grid separated by vertical lines. From master slide 60.

```
Layout:
- White background + accent bar
- Title at (0.52", 0.48"): 24pt Inter Bold
- 3×2 grid (3 columns, 2 rows):
  - Column width: ~2.40", column x: 0.55, 3.71, 6.87
  - Row y: 1.70 (row 1), 3.49 (row 2)
  - Each cell:
    - Title: Inter Bold 12pt at cell_y
    - Description: Inter 11pt at cell_y+0.39, h:0.78"
  - Vertical divider lines: at x: 3.30, 6.46
    - Line from row_y+0.12 to row_y+1.22
```

### 30. Integration Logos Grid (Light Background)

Two rows of platform logos with labels. From master slide 94.

```
Layout:
- White background + accent bar
- Title at (0.52", 0.48"): 24pt Inter Bold
- Subtitle: (0.52", 1.14"), Inter 12pt
- Section header: (0.54", 1.97"), Inter Bold 12pt, teal
- Two rows of 8 logos:
  - Logo size: 0.75"×0.75" each
  - Row 1 y: 2.57, Row 2 y: 3.81
  - x positions: 0.64, 1.77, 2.91, 4.05, 5.19, 6.32, 7.46, 8.60
  - Labels: Inter 10pt below each logo, centered, y: row_y+0.76
```

### 31. Two Features Stacked (Light Background)

Two full-width horizontal cards stacked vertically. From master slide 50.

```
Layout:
- White background + accent bar + pictogram
- Title at (0.52", 0.48"): 24pt Inter Bold
- Two cards stacked:
  - Card size: 8.72"×1.68"
  - Card 1 at y: 1.32, Card 2 at y: 3.25
  - Fill: #E5F8FA (veryLightTeal)
  - Each card:
    - Icon: 0.43"×0.43" at (card_x+0.30, card_y+0.31)
    - Title: Inter Bold 12pt at (card_x+0.83, card_y+0.29)
    - Description: Inter 11pt at (card_x+3.79, card_y+0.23), w:4.53"
```

## Chart Types (pptxgenjs)

Charts from master slides 88-92. pptxgenjs has built-in chart support.

### Grouped Bar Chart (slide 88)

Two-series comparison (e.g., Desktop vs Mobile). Bars in brand teal + near-black.

```javascript
// Grouped bar chart - Desktop vs Mobile
const categories = ['Books & Games', 'Consumer Electronics', 'Cosmetics', /* ... */];
const desktopData = [28, 14, 14, 9, 15, 12, 13, 14, 9, 17, 17, 23, 16, 14];
const mobileData = [22, 9, 15, 8, 14, 9, 12, 9, 8, 12, 11, 14, 13, 11];

const s = lightSlide();
slideTitle(s, 'Search usage ', 'by device', { dark: false });

// Chart container (light gray rounded rect background)
s.addShape(pptx.ShapeType.roundRect, {
  x: 0.65, y: 1.27, w: 8.70, h: 3.68,
  fill: { color: C.chartBg }, rectRadius: 0.08,  // #F4FBFC near-white
  line: { type: 'none' },
});

s.addChart(pptx.charts.BAR, [
  { name: 'Desktop', labels: categories, values: desktopData },
  { name: 'Mobile', labels: categories, values: mobileData },
], {
  x: 0.65, y: 1.27, w: 8.70, h: 3.68,
  barDir: 'col',
  barGrouping: 'clustered',
  barGapWidthPct: 50,
  chartColors: [C.teal, C.darkNavy],  // darker teal (#2DA1BA) + dark navy (#111A21)
  showValue: true,
  valueFontSize: 8,
  valueFontFace: F,
  catAxisLabelFontSize: 8,
  catAxisLabelFontFace: F,
  showLegend: true,
  legendPos: 't',
  legendFontSize: 9,
  legendFontFace: F,
  valAxisHidden: true,
  catGridLine: { style: 'none' },
  valGridLine: { style: 'none' },
});
```

### Single Bar Chart (slide 89)

One series, all bars in brand teal.

```javascript
const s = lightSlide();
slideTitle(s, 'Graph example: ', 'Autocomplete CTR', { dark: false });

s.addShape(pptx.ShapeType.roundRect, {
  x: 0.65, y: 1.27, w: 8.70, h: 3.68,
  fill: { color: C.chartBg }, rectRadius: 0.08,  // #F4FBFC near-white
  line: { type: 'none' },
});

s.addChart(pptx.charts.BAR, [{
  name: 'CTR',
  labels: ['Books & Games', 'Consumer Electronics', /* ... */],
  values: [39, 29, 40, 29, 38, 30, 34, 35, 25, 22, 35, 40, 28, 34],
}], {
  x: 0.65, y: 1.27, w: 8.70, h: 3.68,
  barDir: 'col',
  chartColors: [C.teal],
  showValue: true,
  valueFontSize: 9,
  valueFontFace: F,
  catAxisLabelFontSize: 8,
  catAxisLabelFontFace: F,
  valAxisHidden: true,
  catGridLine: { style: 'none' },
  valGridLine: { style: 'none' },
});
```

### Donut Charts — 3 in Row (slide 90)

Three donut/ring charts side by side with percentage in center and label below.

```javascript
const s = lightSlide();
slideTitle(s, 'Pie graph example', '', { dark: false });

// Color palette for donuts: brand teal, darker teal, deep teal
const donutColors = [C.brand, C.teal, C.deepTeal];

const donuts = [
  { value: 75, label: 'Average cart conversion\nrate increase' },
  { value: 62, label: 'Average order value\nincrease' },
  { value: 50, label: 'Search can make up half\nof your revenue' },
];

donuts.forEach((d, i) => {
  const cx = 0.28 + i * 3.14;  // x positions: 0.28, 3.42, 6.53

  // Donut chart
  s.addChart(pptx.charts.DOUGHNUT, [{
    name: 'Value',
    labels: ['Value', 'Remaining'],
    values: [d.value, 100 - d.value],
  }], {
    x: cx, y: 1.28, w: 3.16, h: 2.76,
    chartColors: [donutColors[i], 'E8E8E8'],
    showTitle: false,
    showLegend: false,
    holeSize: 65,
  });

  // Center percentage text
  s.addText(`${d.value}%`, {
    x: cx + 0.94, y: 2.18, w: 1.28, h: 0.96,
    fontSize: 32, fontFace: F, bold: true, color: C.nearBlack,
    align: 'center', valign: 'middle',
  });

  // Description below
  s.addText(d.label, {
    x: cx + 0.36, y: 4.06, w: 2.55, h: 0.87,
    fontSize: 11, fontFace: F, color: C.muted,
    align: 'center', valign: 'top',
  });
});
```

### Donut Chart with Legend (slide 92)

Single large donut with colored legend badges on the right.

```javascript
const s = lightSlide();
slideTitle(s, 'Graph example: ', 'Pagination usage', { dark: false });

// Container
s.addShape(pptx.ShapeType.roundRect, {
  x: 0.65, y: 1.27, w: 8.70, h: 3.68,
  fill: { color: C.chartBg }, rectRadius: 0.08,  // #F4FBFC near-white
  line: { type: 'none' },
});

// Donut chart (left side)
s.addChart(pptx.charts.DOUGHNUT, [{
  name: 'Pagination',
  labels: ['First page only', 'Next page', 'Jump to another'],
  values: [88, 10, 2],
}], {
  x: 0.87, y: 1.40, w: 3.46, h: 3.42,
  chartColors: [C.teal, C.darkNavy, C.magenta],
  showTitle: false,
  showLegend: false,
  holeSize: 55,
});

// Legend badges (right side) — colored rounded-rect pills with text
const legendItems = [
  { pct: '88%', label: 'First page only', color: C.brand },
  { pct: '10%', label: 'Next page', color: C.nearBlack },
  { pct: '2%', label: 'Jump to another', color: C.magenta },
];
legendItems.forEach((item, i) => {
  const y = 2.32 + i * 0.58;
  // Colored pill
  s.addShape(pptx.ShapeType.roundRect, {
    x: 5.12, y: y, w: 1.40, h: 0.40,
    fill: { color: item.color }, rectRadius: 0.06,
  });
  s.addText(item.pct, {
    x: 5.12, y: y, w: 1.40, h: 0.40,
    fontSize: 11, fontFace: F, bold: true, color: C.white,
    align: 'center', valign: 'middle',
  });
  // Label
  s.addText(item.label, {
    x: 6.55, y: y, w: 1.98, h: 0.40,
    fontSize: 12, fontFace: F, color: C.nearBlack,
    valign: 'middle',
  });
});
```

### Chart Color Rules

| Series / Element | Color | Hex |
|-----------------|-------|-----|
| Primary series (single bar) | Darker teal | `#2DA1BA` |
| Secondary series (comparison) | Dark navy | `#111A21` (dk2) |
| Donut segments (ordered) | Teal → Dark navy → Magenta → Deep teal | `#2DA1BA` → `#111A21` → `#E22077` → `#007E90` |
| Donut remaining/empty | Light gray | `#E8E8E8` |
| Chart background container | Near-white | `#F4FBFC` (lt1) |
| Value labels | Near-black | `#0D0D0F` |
| Category labels | Muted | `#4E6172` |

## AB Test Report Structure

Standard AB test report flow (dark-light-dark sandwich):

1. **Title slide** (dark) - Client, date range, services tested
2. **AB test summary** (dark) - Test parameters as bullet list
3. **Result highlights** (dark) - 3 white metric cards with key numbers
4. **Results slide(s)** (white) - Metric card + table + source + notes (one per data source)
5. **Conclusions** (dark) - Hierarchical bullet summary with recommendations
6. **Closing slide** (dark) - Tagline, contact info

Optional additions (insert between 4 and 5):
- **Phase slides** (white) - For multi-phase tests, one results slide per phase
- **Chart slide** (white/dark) - Trend visualization across phases
- **Section dividers** (dark) - Between major sections in longer presentations

## Optional Components

### Progress Tracker (for process/stages presentations)

Use when presenting multi-stage processes (e.g., AB test lifecycle, onboarding steps). Not for standard AB test reports.

```
Layout:
- Position: Bottom of slide (y ≈ 4.85")
- Background line: full width, dimmed (#2A2A3E)
- Progress fill: brand teal (#35C2D6), fills based on current stage
- Stage markers: circles centered on line
  - Active/past: brand teal fill (#35C2D6), white number
  - Future: dimmed fill (#2A2A3E), dimmed number
  - Active: larger circle with white border
- Labels: below circles, 7pt, dimmed (active = white + bold)
```

**Key implementation detail:** Center numbers by making text box match circle dimensions exactly.

## Color Consistency Rules

**CRITICAL:** Every color has ONE defined role. Never mix them.

| Color | Hex | Role | Example |
|-------|-----|------|---------|
| **brand teal** | `#35C2D6` | Accent bar, headline accents on DARK BG | Accent bar, "summary", "highlights", "next steps" |
| **teal** | `#2DA1BA` | Headline accents on LIGHT BG, labels, citations | Title accent words, source citations, info subtitles |
| **green** | `#50B432` | Positive metric VALUES only | "+3.47%", "+390,478", bold diff cells |
| **orange** | `#ED561B` | Negative metric VALUES only | "-2.1%", bold diff cells |
| **dimmed** | `#8899AA` | Secondary/muted text on DARK BG | Level 2 bullets, date/author, footnotes |

**The most common mistake:** Using `teal` (#2DA1BA) for big metric numbers on highlight cards instead of `green` (#50B432). Teal is a label/accent color, NOT a metric value color. All positive metric values — regardless of slide background — must use `green` (#50B432).

```
WRONG: s.addText('+390,478', { color: C.teal });   // teal is for labels
RIGHT: s.addText('+390,478', { color: C.green });  // green for positive metrics

WRONG: s.addText('CZK', { color: C.teal });        // this IS a label, teal OK
RIGHT: s.addText('CZK', { color: C.teal });        // correct!
```

**Quick test:** Is this text a NUMBER/VALUE? → green or orange. Is it a WORD/LABEL? → teal, dimmed, or body gray.

## Text Layout Rules (Overlap Prevention)

```
╔════════════════════════════════════════════════════════════════════════╗
║  ⛔ TEXT OVERLAP IS THE #1 VISUAL BUG IN pptxgenjs PRESENTATIONS       ║
╠════════════════════════════════════════════════════════════════════════╣
║                                                                        ║
║  THE PROBLEM:                                                          ║
║  pptxgenjs has NO layout engine. Each addText() creates an             ║
║  independently positioned text box. Unlike HTML/CSS, there is          ║
║  NO auto-flow, NO flexbox, NO margin collapse, NO overflow             ║
║  handling. If two text boxes occupy the same vertical space,           ║
║  they render ON TOP of each other — producing garbled,                 ║
║  unreadable text in the final PPTX.                                    ║
║                                                                        ║
║  WHY IT KEEPS HAPPENING:                                               ║
║  1. Font size → box height mapping is not intuitive                    ║
║     (36pt text does NOT need a 36pt/72 = 0.50" box — it               ║
║     needs 0.55" due to line metrics and padding)                       ║
║  2. Relative offsets inside cards accumulate errors                     ║
║     (card_y + 0.27 + 0.43 ≠ card_y + 0.66 — off by 0.04")           ║
║  3. Copy-paste between slides introduces drift                         ║
║  4. "Looks close enough" is never close enough — even 0.01"           ║
║     overlap produces visible garbling in PowerPoint                    ║
║                                                                        ║
║  THE FIX:                                                              ║
║  Mandatory y-coordinate audit with inline math comments.               ║
║  Every text box must document: start_y, height, end_y.                 ║
║  Next box start_y must be >= previous box end_y.                       ║
╚════════════════════════════════════════════════════════════════════════╝
```

### The Golden Rule

**Next box y must be >= previous box y + previous box h.** No exceptions.

```javascript
// WRONG — overlap! Big text at 34pt needs ~0.55" actual height
s.addText('+390,478', { y: 1.50, h: 0.7 });  // ends at 2.20
s.addText('CZK',      { y: 2.10, h: 0.4 });  // starts at 2.10 — OVERLAPS by 0.10"!

// RIGHT — no overlap
s.addText('+390,478', { y: 1.50, h: 0.55 }); // ends at 2.05
s.addText('CZK',      { y: 2.05, h: 0.35 }); // starts at 2.05 — clean
```

### Mandatory Y-Coordinate Audit Comments

**Every group of stacked text boxes MUST have inline comments documenting the vertical math.** This is not optional — it is the primary defense against overlap bugs.

Pattern: `// element: y_value h:height → ends y_value+height`

```javascript
// REQUIRED — audit comments for every card or stacked text group:
//
// Card content — y-stacked, no overlap:
//   icon:   cardY+0.12  h:0.38  → ends cardY+0.50
//   metric: cardY+0.55  h:0.55  → ends cardY+1.10
//   unit:   cardY+1.10  h:0.35  → ends cardY+1.45
//   desc:   cardY+1.45  h:0.50  → ends cardY+1.95
//   badge:  cardY+1.95  h:0.35  → ends cardY+2.30  (within card h:2.70)
//
// ✅ Each start >= previous end. Last end < card bottom.

s.addImage({ path: iconPath, y: cardY + 0.12, h: 0.38 });
s.addText('+390,478',    { y: cardY + 0.55, h: 0.55 }); // 34pt
s.addText('CZK',         { y: cardY + 1.10, h: 0.35 }); // 18pt
s.addText('Description', { y: cardY + 1.45, h: 0.50 }); // 12pt
s.addText('+3.47%',      { y: cardY + 1.95, h: 0.35 }); // 16pt
```

**Without these comments, overlaps WILL creep in over time.** The math must be visible in the code, not just in the developer's head.

### Common Overlap Patterns (from real bugs found)

| Bug Pattern | What Goes Wrong | Fix |
|-------------|-----------------|-----|
| **Oversized h** | Box h:0.96 for 36pt text extends way past where next box starts | Use minimum h from table below (36pt → 0.55") |
| **Accumulated offset drift** | `by+0.27` + `h:0.43` = `by+0.70`, but next box at `by+0.66` | Always compute `end = start + h` explicitly |
| **Multi-line title undercount** | 45pt × 2 lines needs h:1.40", given h:1.20" | Multiply single-line h by line count + 0.10" padding |
| **Line wrap not anticipated** | Box h:0.70 sized for 1 line at 45pt, but text wraps to 2 lines at the given width — text overflows the box and collides with neighbors | Always estimate line count at the box width: at W inches, a line fits ~W×4 chars at 45pt bold. If text wraps, multiply h accordingly |
| **Unit label too small** | 18pt text in h:0.28 box — needs h:0.35 | Check height guidelines table for EVERY font size |
| **Copy-paste without recalc** | Copied card layout, changed font sizes, kept old y/h values | Recalculate ALL y/h values from scratch after any change |

### Safe Card Layout Templates

**3-Card Highlight (dark BG, 2.8"×2.70" white cards starting at y:1.3):**
```
Card y:1.3, h:2.70 → ends at y:4.00
├── Icon:        y+0.12, h:0.38  → ends y+0.50  (icon image)
├── Big metric:  y+0.55, h:0.55  → ends y+1.10  (34pt bold, green)
├── Unit label:  y+1.10, h:0.35  → ends y+1.45  (18pt bold, teal)
├── Description: y+1.45, h:0.50  → ends y+1.95  (12pt, teal)
└── Percentage:  y+1.95, h:0.35  → ends y+2.30  (16pt bold, green)
```

**Metric Card on white slide (2.8"×1.8" tinted card starting at y:1.3):**
```
Card y:1.3, h:1.8 → ends at y:3.10
├── Big metric:  y:1.35, h:0.55  → ends 1.90  (36pt bold, green)
├── Label:       y:1.90, h:0.30  → ends 2.20  (12pt, teal)
├── Sub-value:   y:2.20, h:0.30  → ends 2.50  (14pt bold, #333)
└── Sub-label:   y:2.50, h:0.25  → ends 2.75  (10pt, dimmed)
```

**Small metric card (2.3"×1.0" tinted card):**
```
Card y:Y, h:1.0 → ends at y:Y+1.0
├── Big metric:  y:Y+0.05, h:0.50  → ends Y+0.55  (26pt bold, green)
└── Label:       y:Y+0.55, h:0.30  → ends Y+0.85  (11pt, teal)
```

**Feature card (8.72"×1.08" horizontal card):**
```
Card y:fy, h:1.08 → ends at fy+1.08
├── Icon:   fy+0.15, h:0.43  → ends fy+0.58  (0.43" icon)
├── Title:  fy+0.15, h:0.35  → ends fy+0.50  (12pt bold)
└── Desc:   fy+0.55, h:0.40  → ends fy+0.95  (11pt)
```

**Four Boxes Grid (4.30"×1.77" card):**
```
Card y:by, h:1.77 → ends at by+1.77
├── Icon:   by+0.27, h:0.43  → ends by+0.70  (0.43" icon)
├── Title:  by+0.27, h:0.43  → ends by+0.70  (12pt bold, beside icon)
└── Desc:   by+0.75, h:0.85  → ends by+1.60  (11pt, below title)
```

**Stats Cards 3-col (2.72"×3.61" card):**
```
Card y:cy, h:3.61 → ends at cy+3.61
├── Icon:    cy+0.56, h:0.43  → ends cy+0.99  (0.43" icon)
├── Metric:  cy+1.15, h:0.65  → ends cy+1.80  (36pt bold)
└── Desc:    cy+1.90, h:0.80  → ends cy+2.70  (12pt)
```

### Height Guidelines by Font Size

| Font Size | Minimum h (1 line) | Per Extra Line | Notes |
|-----------|-------------------|----------------|-------|
| 45pt | 0.70" | +0.65" | Main titles |
| 34-36pt | 0.55" | +0.50" | Big metrics |
| 24-26pt | 0.45" | +0.40" | Slide headers, card metrics |
| 18pt | 0.35" | +0.30" | Unit labels |
| 14-16pt | 0.30" | +0.25" | Body text, percentages |
| 11-13pt | 0.25" | +0.20" | Bullets, table text, labels |
| 9-10pt | 0.20" | +0.15" | Footnotes, source citations |

**Multi-line formula:** `h = single_line_h + (extra_lines × per_extra_line) + 0.10"` (the 0.10" is padding for line spacing)

### MANDATORY: Line Wrap Estimation Before Setting Box Height

```
╔════════════════════════════════════════════════════════════════════════╗
║  ⛔ YOU MUST ESTIMATE LINE WRAPS FOR EVERY addText() CALL              ║
║                                                                        ║
║  pptxgenjs does NOT auto-resize text boxes. If text wraps to more     ║
║  lines than h allows, it OVERFLOWS and overlaps the next element.     ║
║  This is the #1 cause of broken slides — especially on titles.        ║
║                                                                        ║
║  You MUST calculate lines BEFORE setting h. No exceptions.             ║
╚════════════════════════════════════════════════════════════════════════╝
```

**Step 1: Count characters.** Count the total characters in the text string, including spaces.

**Step 2: Calculate how many fit per line.** Use this lookup table:

| Font Size | Bold Chars/Inch | Regular Chars/Inch | Example: w:8.5" fits |
|-----------|----------------|-------------------|---------------------|
| 45pt | 2.8 | 3.0 | **23 bold** / 25 regular |
| 36pt | 3.3 | 3.6 | 28 bold / 30 regular |
| 24pt | 4.5 | 5.0 | 38 bold / 42 regular |
| 18pt | 5.5 | 6.0 | 46 bold / 51 regular |
| 14pt | 7.0 | 7.5 | 59 bold / 63 regular |
| 12pt | 8.0 | 8.5 | 68 bold / 72 regular |
| 11pt | 9.0 | 9.5 | 76 bold / 80 regular |

**Formula:** `chars_per_line = width_inches × chars_per_inch`

**Step 3: Calculate lines needed.** `lines = ceil(total_chars / chars_per_line)`

**Step 4: Calculate height needed.** Use the Height Guidelines table:
`h = single_line_h + (extra_lines × per_extra_line)`

**Step 5: Set h to at least the calculated value.** If the text might vary in length, also add `shrinkText: true`.

#### Worked Example — Title Slide

```
Text: "LuigisBox Sample Deck Template" (30 chars, 45pt bold, w:8.5")

Step 2: chars_per_line = 8.5 × 2.8 = 23.8 → 23 chars per line
Step 3: lines = ceil(30 / 23) = 2 lines  ← WRAPS!
Step 4: h = 0.70 + (1 × 0.65) = 1.35" minimum → use 1.40"

⛔ If you had used h:0.70 (1-line height), the second line overflows
   and overlaps whatever comes next. THIS IS THE BUG.
```

#### When Text WILL Wrap: Use Explicit Line Breaks

**If your calculation shows text will wrap, insert `\n` to control WHERE it wraps.** Do not let the renderer decide — word break positions vary between PowerPoint, Keynote, and Google Slides.

```javascript
// BAD — "Template" wraps to line 2, position depends on renderer
s.addText([
  { text: 'LuigisBox Sample ', options: { color: C.white } },
  { text: 'Deck Template', options: { color: C.brand } },
], { y: 1.80, h: 0.70 });  // h:0.70 = 1 line — OVERFLOW!

// GOOD — explicit 2-line layout, h sized for 2 lines
s.addText([
  { text: 'LuigisBox Sample\n', options: { color: C.white } },
  { text: 'Deck Template', options: { color: C.brand } },
], { y: 1.80, h: 1.40 });  // h:1.40 = 2 lines at 45pt ✅
```

### MANDATORY: Overlap Validation Before Saving

```
╔════════════════════════════════════════════════════════════════════════╗
║  RUN THIS CHECK FOR EVERY SLIDE BEFORE SAVING.                        ║
║  This is NOT optional. Do NOT skip it. Do NOT trust your memory.       ║
╚════════════════════════════════════════════════════════════════════════╝
```

For each slide, perform these checks IN ORDER:

**Check 1: Line wrap estimation** (see above)
- For EVERY `addText()` call, calculate `chars_per_line` and `lines_needed`
- If `lines_needed > 1`, verify h accommodates all lines
- If text wraps, insert explicit `\n` to control break position

**Check 2: Vertical overlap scan**
- List all elements (text, shapes, images) sorted by y ascending
- For each consecutive pair: verify `next.y >= current.y + current.h`
- Any violation = STOP and fix

**Check 3: Card bounds check**
- For text inside cards: verify last element's `y + h <= card_y + card_h`

**Check 4: 2D bounding box collision detection**
- Elements don't just stack vertically — footers, standalone text, and images can overlap with card groups or other content areas horizontally AND vertically
- For EVERY element that is NOT inside a known vertical stack (e.g., standalone footers, floating text, images placed near cards), check for 2D collision against all nearby elements:
  ```
  Two rectangles (x1,y1,w1,h1) and (x2,y2,w2,h2) COLLIDE if ALL four are true:
    x1 < x2 + w2   AND   x1 + w1 > x2   AND   y1 < y2 + h2   AND   y1 + h1 > y2
  ```
- Common collision scenarios to watch for:
  - Footer text/icons placed BELOW cards but WITHIN the card y-range (e.g., cards end at y:4.94 but footer at y:4.56)
  - Images or shapes that extend beyond their intended column into adjacent content
  - Gradient overlays covering content they shouldn't (e.g., full-width gradients over left-side text)
- Fix: move the colliding element so its bounding box is fully outside the other element's bounding box

**Check 5: Audit comments present**
- Every group of stacked text boxes MUST have inline y-coordinate audit comments
- If comments are missing, add them before proceeding

### Additional Safeguards

- **Use `shrinkText: true`** on any text box where content length varies (e.g., metric values that might be "+3.47%" or "+1,234,567 CZK") or where the text is close to wrapping
- **Use `valign: 'middle'`** to center text vertically within its box (prevents visual drift)
- **Bullet lists:** Set container `h` large enough for all bullets. Estimate: `N_items × fontSize_pt / 72 × lineSpacingMultiple + 0.3"`
- **After ANY change to font sizes, y-positions, or heights:** Recalculate ALL y/h values in that group from scratch and update the audit comments. Never adjust just one box in isolation.

## Code Constants (pptxgenjs)

```javascript
// Color palette (from master template themes)
const C = {
  // Dark background gradient (REQUIRED — never use flat color)
  darkBgTop: '0D0E0F',       // Gradient top (near-black)
  darkBgBottom: '0C272F',    // Gradient bottom (dark teal-tinted)
  // Core colors
  white: 'FFFFFF',
  black: '000000',
  nearBlack: '0D0D0F',       // dk1 — primary text on light BG
  nearWhite: 'F4FBFC',       // lt1 — text on dark BG, contact info
  // Brand & accent
  brand: '35C2D6',            // LuigisBox brand teal (accent bar + dark BG title accents)
  accentBar: '35C2D6',       // Alias for brand
  teal: '2DA1BA',            // Darker teal (light BG title accents, labels, citations)
  deepTeal: '007E90',        // accent3 — deep teal for special use
  // Metric colors
  green: '50B432',           // Positive metrics, positive differences
  orange: 'ED561B',          // Negative metrics, negative differences
  // Neutral
  dimmed: '8899AA',          // Muted text on dark BG
  muted: '4E6172',           // lt2 — captions, footnotes
  tableBorder: 'DDDDDD',    // Table cell borders
  bodyGray: '555555',        // Body text in notes sections
  headerGray: '333333',      // Bold headers in notes
  labelGray: '666666',       // Metric labels (neutral)
  // Card fills
  lightTeal: 'C6F0F5',      // Feature card background (accent4)
  veryLightTeal: 'E5F8FA',  // Lighter card background (accent5)
  lightGreen: 'F0FFF0',     // Positive metric card background
  lightPink: 'FFF0F0',      // Negative metric card background
  lightBlue: 'F0F8FF',      // Neutral metric card background
  // Chart
  darkNavy: '111A21',        // dk2 — dark navy for chart secondary series
  chartBg: 'F4FBFC',         // lt1 — chart container background (near-white)
  // Special
  magenta: 'E22077',         // accent6 — special accent
};

const F = 'Inter';           // Primary font (ALWAYS use Inter, per master template)
```

## Code Example (pptxgenjs) — Preferred Pattern

This is the **preferred** implementation pattern, based on the master template and polished presentations.

```javascript
import pptxgen from 'pptxgenjs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ASSETS_DIR = '/Users/martin/.claude/skills/luigisbox-presentations/assets';

const pptx = new pptxgen();
pptx.layout = 'LAYOUT_16x9';

// ─── Reusable helpers ───
function addAccentBar(s) {
  s.addShape(pptx.ShapeType.rect, {
    x: 0, y: 0, w: 0.63, h: 0.05,
    fill: { color: C.accentBar },
    line: { type: 'none' },
  });
}

function addDarkGradient(s) {
  // pptxgenjs does NOT support gradient fills on shapes — use pre-rendered PNG
  s.background = {
    path: path.join(ASSETS_DIR, 'dark-gradient-bg.png'),
  };
}

function addDarkBranding(s) {
  // Full logo top-left
  s.addImage({
    path: path.join(ASSETS_DIR, 'logo-full-white.png'),
    x: 0.65, y: 0.62, w: 1.45, h: 0.49,
  });
  // "luigisbox.com" top-right
  s.addText('luigisbox.com', {
    x: 6.32, y: 0.52, w: 3.11, h: 0.38,
    fontSize: 11, fontFace: F, bold: true, color: C.white, align: 'right',
  });
}

function addLightBranding(s) {
  // Pictogram top-right
  s.addImage({
    path: path.join(ASSETS_DIR, 'icon-color.png'),
    x: 9.03, y: 0.62, w: 0.32, h: 0.32,
  });
}

function darkSlide() {
  const s = pptx.addSlide();
  addDarkGradient(s);
  addAccentBar(s);
  addDarkBranding(s);
  return s;
}

function lightSlide() {
  const s = pptx.addSlide();
  s.background = { color: C.white };
  addAccentBar(s);
  addLightBranding(s);
  return s;
}

// Two-tone title (white+brand on dark, black+teal on light)
function slideTitle(s, mainText, accentText, opts = {}) {
  const dark = opts.dark !== false;
  const parts = [];
  if (mainText) parts.push({ text: mainText, options: { bold: true, fontSize: 24, fontFace: F, color: dark ? C.white : C.black } });
  if (accentText) parts.push({ text: accentText, options: { bold: true, fontSize: 24, fontFace: F, color: dark ? C.brand : C.teal } });
  s.addText(parts, { x: 0.6, y: 0.4, w: 8.0, h: 0.6, margin: 0 });
}

// ─── Results slide (white BG, metric card + table) ───
const s = lightSlide();
slideTitle(s, 'Detailed results ', '- GA4', { dark: false });

// Info subtitle
s.addText('12.1.2026 - 5.2.2026  |  25 days  |  72,000 users', {
  x: 0.6, y: 0.8, w: 8.0, h: 0.3,
  fontSize: 11, fontFace: F, color: C.teal, margin: 0,
});

// Metric card (left)
s.addShape(pptx.ShapeType.roundRect, {
  x: 0.6, y: 1.3, w: 2.6, h: 1.5,
  fill: { color: C.lightGreen }, rectRadius: 0.1,
});
s.addText('+3.47%', {
  x: 0.6, y: 1.4, w: 2.6, h: 0.8,
  fontSize: 36, fontFace: 'Inter ExtraBold', bold: true,
  color: C.green, align: 'center', valign: 'middle', margin: 0,
});
s.addText('User CVR', {
  x: 0.6, y: 2.15, w: 2.6, h: 0.4,
  fontSize: 11, fontFace: F, color: C.labelGray, align: 'center', margin: 0,
});

// Table (right) — header row dark, data rows white, color-coded diffs
const border = { type: 'solid', pt: 0.5, color: C.tableBorder };
const tableData = [
  // Header row
  [
    { text: 'Metric', options: { bold: true, fontSize: 11, color: C.white, fill: { color: C.darkBgTop }, border: [border,border,border,border], valign: 'middle' } },
    { text: 'Original', options: { bold: true, fontSize: 11, color: C.white, fill: { color: C.darkBgTop }, border: [border,border,border,border], valign: 'middle' } },
    // ... more columns
  ],
  // Data rows with color-coded difference column
];
s.addTable(tableData, { x: 3.6, y: 1.3, w: 5.3, colW: [1.6, 1.2, 1.2, 1.3], rowH: 0.35, fontFace: F });

// Source
s.addText('Source: Google Analytics 4', {
  x: 3.6, y: 3.3, w: 5.3, h: 0.3,
  fontSize: 10, fontFace: F, italic: true, color: C.teal, margin: 0,
});

// Bottom notes
s.addText([
  { text: 'Key observation:', options: { bold: true, fontSize: 12, fontFace: F, color: C.headerGray, breakLine: true } },
  { text: '', options: { breakLine: true, fontSize: 6 } },
  { text: 'Finding 1...', options: { bullet: { code: '25CF' }, breakLine: true, fontSize: 11, fontFace: F, color: C.bodyGray } },
  { text: 'Finding 2...', options: { bullet: { code: '25CF' }, fontSize: 11, fontFace: F, color: C.bodyGray } },
], { x: 0.6, y: 3.7, w: 8.5, h: 1.6, lineSpacingMultiple: 1.4 });
```

## Quick Reference

| Element | Font | Size | Color (Light BG) | Color (Dark BG) | Min h |
|---------|------|------|------------------|-----------------|-------|
| Title | Inter Bold | 45pt | #000000 | #FFFFFF | 0.70" |
| Title Accent | Inter Bold | 45/24pt | **#2DA1BA** (teal) | **#35C2D6** (brand, NOT #24CBE5) | — |
| Slide Header | Inter Bold | 24pt | #000000 | #FFFFFF | 0.45" |
| Info subtitle | Inter | 11pt | #2DA1BA | #8899AA | 0.25" |
| Bullet L1 | Inter | 13-14pt | #555555 | #FFFFFF | 0.25" |
| Bullet L2 | Inter | 14pt | — | #8899AA | 0.25" |
| **Metric big** | **Inter ExtraBold** | **36pt** | **#50B432 ONLY** | **#50B432 ONLY** | **0.55"** |
| Metric label | Inter | 11-12pt | #2DA1BA (teal) | #2DA1BA (teal) | 0.25" |
| Metric unit | Inter Bold | 18pt | #2DA1BA (teal) | #2DA1BA (teal) | 0.30" |
| Metric % badge | Inter Bold | 14-16pt | #50B432/#ED561B | #50B432/#ED561B | 0.30" |
| Table header | Inter Bold | 11pt | #FFFFFF on #0D0E0F | — | — |
| Table data | Inter | 11pt | #000000 | — | — |
| Table diff (+) | Inter Bold | 11pt | #50B432 | — | — |
| Table diff (-) | Inter Bold | 11pt | #ED561B | — | — |
| Source | Inter Italic | 10pt | #2DA1BA | — | 0.20" |
| Notes header | Inter Bold | 12pt | #333333 | — | 0.25" |
| Notes body | Inter | 11pt | #555555 | — | 0.25" |
| Footnotes | Inter | 11pt | #2DA1BA | #8899AA | 0.20" |
| Date/author | Inter | 14pt | — | #8899AA | 0.30" |
| Speaker name | Inter Bold | 15pt | — | #FFFFFF | 0.30" |
| Speaker title | Inter | 12pt | — | #FFFFFF | 0.25" |
| Contact info | Inter SemiBold | 12pt | — | #F4FBFC | 0.25" |
| Section label | Inter Bold | 11pt | #2DA1BA (ALL CAPS) | — | 0.25" |

**Color rule summary:** Numbers/values = green (#50B432) or orange (#ED561B). Labels/descriptions = teal (#2DA1BA). Title accents on dark BG = brand (#35C2D6). Never use teal for metric values.

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| **Flat dark background** | Dark slides MUST use gradient: #0D0E0F (top) → #0C272F (bottom). Never use flat #1A1A2E or #0D0E0F. Use a full-slide rectangle with linear gradient fill (see `addDarkGradient()` helper in Code Example section). |
| **Wrong dark BG accent color (#24CBE5)** | Use **#35C2D6** (brand teal) for title accents on dark BG. The old #24CBE5 (cyan) is from the legacy theme. |
| **Wrong light BG accent color (#158158)** | Use **#2DA1BA** for title accents on light BG. The old #158158 is from the legacy theme. |
| **Teal (#2DA1BA) for metric values** | Teal is a LABEL color. Use green (#50B432) for ALL positive metric numbers ("+390K", "+3.47%"). Teal only for labels ("CZK", "User CVR", source lines). |
| **Overlapping text boxes in cards** | Every text box y must be >= previous y + previous h. **Add mandatory y-coordinate audit comments** (see Text Layout Rules section). Use the safe card layout templates. Never skip the math. |
| **Inconsistent green shades** | Use ONLY `#50B432` for positive values. Never use teal (#2DA1BA) or any other green variant for metric numbers. One green, no exceptions. |
| **Green in slide titles** | Use brand (#35C2D6) for title accents on dark BG, teal (#2DA1BA) on light BG. Green is only for positive metrics. |
| **All-dark slides for data** | Use **white background** for results/data slides with metric card + table layout. Dark BG is for title, summary, conclusions, closing. |
| **Icon-only on dark intro slides** | Dark intro/section slides get the **full logo** (icon + "LUIGI'S BOX") in top-left + "luigisbox.com" text top-right. Pictogram (icon only) is for light content slides only. |
| **Logo on dark content slides** | Dark content slides (summary, highlights, conclusions) get NO logo and NO "luigisbox.com" text — only gradient background + accent bar. The logo is reserved for title, section dividers, and closing slides. |
| **Wrong accent bar size** | Accent bar is 0.63"×0.05" at (0,0). Not 0.75"×0.06". |
| Missing accent bar | Add 0.63"×0.05" bar at top-left, #35C2D6 (brand teal), on every slide |
| Wrong metric font | Must use Inter ExtraBold (not just bold), 36pt |
| Missing info subtitle | White data slides need "date \| N days \| N users" line at y:0.8 in teal |
| Missing source citation | Always add italic teal source line below tables |
| Flat metric display | Use rounded rect card (rectRadius: 0.1) with tinted fill, not just text |
| No notes section | Data slides need "Key observation:" header + bulleted findings below the table |
| Table without dark header | Header row must be #0D0E0F fill with white text |
| **Text box height too tall for font** | Use the height guidelines table. 34pt text only needs h:0.55", not h:0.70". Oversized boxes steal space from neighbors. |
| **Text exceeding card bounds** | Last text in a card must end (y+h) before card_y + card_h. Check both top and bottom boundaries. |
| **Skewed/deformed logo** | `logo-full-white.png` is 900×306px (aspect ratio 2.94:1). Always preserve this ratio: w:2.5 h:0.85, or w:2.0 h:0.68, or w:1.45 h:0.49. |
| **Content pushed to bottom, empty top** | Vertically center content on the slide. The usable area is ~0.5" to ~5.1" (after accent bar). Don't start content at y:1.2+ when it will overflow the bottom — shift everything up. Always verify the last element's y+h stays below 5.1" with breathing room. |
| **Z-order: arrows/connectors hidden behind cards** | pptxgenjs layers elements in insertion order (first added = bottom). When cards/shapes overlap with arrows or connectors, use **two passes**: Pass 1 adds all background shapes, Pass 2 adds all content, text, and connectors on top. Never interleave backgrounds and foreground elements in a single loop. |
| **Text overflowing its text box** | pptxgenjs does NOT auto-resize text boxes. If text wraps to more lines than the box height allows, it overflows and overlaps neighbors. **This is the #1 cause of overlap on title slides** — e.g., a subtitle at 45pt in a h:0.70" box that wraps to 2 lines needs h:1.40". **Always estimate line count**: at width W inches, a line fits ~W×4 characters at 45pt bold, ~W×5.5 at 14pt, ~W×7 at 11pt. Multiply line count by the height-per-line from the height guidelines table. Use `shrinkText: true` as a safety net, or insert explicit `\n` to control wrapping. |
| **lowercase headlines** | Per master template: use **Title Case** for all main headlines & CTAs. Sentence case for everything else. |
| **Too many bullet points** | Max 3–6 bullet points per slide. Don't overload slides with text. |
| **Too many font sizes** | Stick to 2–3 font sizes per slide maximum. |
| **Slide type label in production** | The bottom-right "Layout N: ..." label is for the **sample deck only**. Never include `slideLabel()` calls in production presentations. Delete any slide type labels before delivering to clients. |

## Animated Content

For presentations that benefit from animated visuals (e.g., embedded videos or GIFs), consider using the **video-maker** skill which creates simple 2D animations using Remotion.

**Good candidates for animation in presentations:**
- `BarRaceChart` - Rankings/market share changing over time
- `AnimatedCounter` - Stats counting up for impact
- `BeforeAfter` - Performance comparison reveals
- `StepTimeline` - Process/onboarding step visualization

**Workflow:** Render video with video-maker skill → embed in PPTX as video or convert to GIF.

Note: Only use animations where motion adds meaning. Static charts are usually sufficient for AB test reports.

## File Reference

**Primary source:** LBX Master Deck Template (`Copy of LBX – Master Deck Template.pptx`)
- 167 slides, 2 slide masters, 38 slide layouts
- Theme 1: "Luigi's Box Template" (primary, "Simple Light" color scheme)
- Theme 2: Legacy default (used in older AB test reports)
- Embedded fonts: Inter, Inter ExtraBold, Inter Medium, Inter SemiBold, Montserrat Medium
- Localized versions: Polish (slides 124-134), German (135-145), Czech (146-156), Slovak (157-167)

**AB test report reference:** `homla.com.pl/AB test report homla.com.pl 2024-06 2026-02-02.pptx`

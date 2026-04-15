/**
 * build-deck.mjs
 *
 * Input:  .agent_context/S2-slide-plan.json  (23 slides, types {2,5,9,11,12,15,31})
 * Output: presentations/working-with-claude-dev-team.pptx
 *
 * Architecture: single ES-module, ~700 lines.
 * Run: node build-deck.mjs  (do not run in S4 — S5 responsibility)
 */

import PptxGenJS from 'pptxgenjs';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import process from 'process';

// ---------------------------------------------------------------------------
// Derived constants
// ---------------------------------------------------------------------------
const __filename = fileURLToPath(import.meta.url);
const __dirname  = path.dirname(__filename);

const ASSETS_DIR  = path.resolve(__dirname, 'assets');
const PLAN_PATH   = path.resolve(__dirname, '../.agent_context/S2-slide-plan.json');
const OUTPUT_PATH = path.resolve(__dirname, 'working-with-claude-dev-team.pptx');

// ---------------------------------------------------------------------------
// Color palette — verbatim from S1 extraction §4a (21 keys, do not edit hex)
// ---------------------------------------------------------------------------
const C = {
  // Backgrounds
  darkBgTop:    '0D0E0F',   // gradient top (near-black)
  darkBgBottom: '0C272F',   // gradient bottom (dark teal-tinted)
  white:        'FFFFFF',
  nearBlack:    '0D0D0F',   // dk1 primary text on light BG
  nearWhite:    'F4FBFC',   // lt1 text on dark BG, contact info
  // Brand accents
  brand:        '35C2D6',   // accent bar + dark BG title accents
  teal:         '2DA1BA',   // light BG title accents + labels + citations
  deepTeal:     '007E90',
  // Metric colors (values ONLY, never labels)
  green:        '50B432',   // positive metric numbers
  orange:       'ED561B',   // negative metric numbers
  // Neutral
  dimmed:       '8899AA',   // L2 bullets on dark BG, dates, footnotes
  muted:        '4E6172',   // captions, small footnotes
  tableBorder:  'DDDDDD',
  bodyGray:     '555555',
  headerGray:   '333333',
  labelGray:    '666666',
  // Card fills
  lightTeal:     'C6F0F5',
  veryLightTeal: 'E5F8FA',  // DEFAULT card fill on light BG
  lightGreen:    'F0FFF0',  // positive metric card
  lightPink:     'FFF0F0',  // negative metric card
  lightBlue:     'F0F8FF',  // neutral metric card
  // Charts
  darkNavy:   '111A21',
  chartBg:    'F4FBFC',
  // Special
  magenta:    'E22077',
};

const F = 'Inter';  // ALWAYS

// Ceiling Y — content must not exceed this
const CEILING_Y = 5.10;

// ---------------------------------------------------------------------------
// HELPERS
// ---------------------------------------------------------------------------

/** Accent bar — required on every slide. Spec: x:0 y:0 w:0.63 h:0.05 fill:C.brand */
function addAccentBar(s) {
  s.addShape('rect', {
    x: 0, y: 0, w: 0.63, h: 0.05,
    fill: { color: C.brand },
    line: { type: 'none' },
  });
}

/** Dark gradient background image */
function addDarkGradient(s) {
  s.background = { path: path.join(ASSETS_DIR, 'dark-gradient-bg.png') };
}

/**
 * Full-logo branding for dark intro/section slides.
 * logo-full-white.png @ (0.65, 0.62) 1.45"×0.49"
 * "luigisbox.com" text @ top-right (6.32, 0.52) 11pt bold white right-aligned
 */
function addDarkBranding(s) {
  s.addImage({
    path: path.join(ASSETS_DIR, 'logo-full-white.png'),
    x: 0.65, y: 0.62, w: 1.45, h: 0.49,
  });
  s.addText('luigisbox.com', {
    x: 6.32, y: 0.52, w: 3.30, h: 0.30,
    fontSize: 11, fontFace: F, bold: true,
    color: C.white, align: 'right',
    valign: 'top',
  });
}

/**
 * Pictogram branding for light content slides.
 * icon-color.png @ (9.03, 0.62) 0.32"×0.32"
 */
function addLightBranding(s) {
  s.addImage({
    path: path.join(ASSETS_DIR, 'icon-color.png'),
    x: 9.03, y: 0.62, w: 0.32, h: 0.32,
  });
}

/**
 * Factory: create a dark slide with gradient + accent bar + conditional branding.
 * branding: 'full-logo' | 'none'
 */
function darkSlide(pptx, branding) {
  const s = pptx.addSlide();
  addDarkGradient(s);
  addAccentBar(s);
  if (branding === 'full-logo') addDarkBranding(s);
  return s;
}

/**
 * Factory: create a light slide with accent bar + conditional pictogram.
 * branding: 'pictogram' | 'none'
 */
function lightSlide(pptx, branding) {
  const s = pptx.addSlide();
  addAccentBar(s);
  if (branding === 'pictogram') addLightBranding(s);
  return s;
}

/** Resolve color alias → hex string. Throws on unknown alias (not a known key and not raw 6-digit hex). */
function resolveColor(name) {
  if (C[name]) return C[name];
  if (/^[0-9A-Fa-f]{6}$/.test(name)) return name;  // explicit hex pass-through
  throw new Error(`Unknown color alias: "${name}"`);
}

// ---------------------------------------------------------------------------
// Block renderers
// ---------------------------------------------------------------------------

/**
 * Generic text block renderer — handles kinds:
 *   title, subtitle, paragraph, quote_block, footer_text
 *
 * branding param (optional): when 'pictogram', title and subtitle block widths
 * are narrowed to 8.35" so their right edge (0.6 + 8.35 = 8.95) stays clear
 * of the pictogram left edge at x=9.03. Both kinds are full-width header-area
 * blocks whose JSON-declared w:8.8 pushes the bbox to x=9.4, past the pictogram.
 * This keeps the rendered bbox within the safe zone without touching the JSON plan.
 * Paragraph, quote_block, and footer_text kinds are unaffected.
 */
function addTextBlock(s, block, branding) {
  const isTitleKind = block.kind === 'title';
  const isHeaderKind = isTitleKind || block.kind === 'subtitle';
  // On pictogram slides, narrow title/subtitle width so right edge (x + w) stays
  // below pictogram left edge (9.03). Target w: 8.35 → right edge = 0.6 + 8.35 = 8.95 < 9.03.
  const titleW = (isHeaderKind && branding === 'pictogram') ? 8.35 : block.w;
  const opts = {
    x: block.x, y: block.y, w: titleW, h: block.h,
    fontSize: block.font_size,
    fontFace: F,
    color: resolveColor(block.color),
    bold: block.bold ?? false,
    italic: block.italic ?? false,
    valign: 'top',
  };
  if (block.align) opts.align = block.align;
  // shrinkText on title blocks and footer_text (per spec item 8 + sketch §8)
  if (isTitleKind || block.kind === 'footer_text') opts.shrinkText = true;
  s.addText(block.text, opts);
}

/**
 * Bullet list renderer.
 * Renders items[] as L1 bullets (● 25CF, teal).
 */
function addBulletsBlock(s, block) {
  const bulletItems = block.items.map((item) => ({
    text: item,
    options: {
      fontSize: block.font_size,
      fontFace: F,
      color: resolveColor(block.color),
      bold: false,
      bullet: { code: '25CF', color: C.teal },
      indentLevel: 0,
    },
  }));
  s.addText(bulletItems, {
    x: block.x, y: block.y, w: block.w, h: block.h,
    valign: 'top',
  });
}

/**
 * Card block renderer.
 * Renders a rounded-rect card with header text + description items inside.
 *
 * Internal layout (from S1 §4d "Feature card" template):
 *   card rect:  x, y, w, h
 *   header:     x+0.10, y+0.12, w-0.20, 0.30  (12pt bold)
 *   items[]:    x+0.10, y+0.50 + idx*0.28, w-0.20, 0.28 each (11pt)
 *
 * Card-inner-fit assertion (spec item 6, critic fold-in 2):
 *   header.y + header.h + items_total_h must be <= card.y + card.h
 */
function addCardBlock(s, block, planId) {
  const HEADER_REL_Y  = 0.12;
  const HEADER_H      = 0.30;
  const ITEMS_START_Y = 0.50;
  const ITEM_H        = 0.28;

  // Defensive card-inner-fit assertion (spec item 6)
  const headerBottom     = block.y + HEADER_REL_Y + HEADER_H;
  const itemsTotalH      = block.items.length * ITEM_H;
  const cardBottom       = block.y + block.h;
  const itemsRegionBottom = block.y + ITEMS_START_Y + itemsTotalH;
  if (itemsRegionBottom > cardBottom + 0.001) {
    throw new Error(
      `Card inner-fit violation on slide ${planId} (block kind=card header="${block.text}"): ` +
      `items region ends at ${itemsRegionBottom.toFixed(3)}" ` +
      `but card bottom is ${cardBottom.toFixed(3)}"`
    );
  }
  // Also check header fits
  if (headerBottom > cardBottom + 0.001) {
    throw new Error(
      `Card inner-fit violation on slide ${planId} (block kind=card header="${block.text}"): ` +
      `header ends at ${headerBottom.toFixed(3)}" ` +
      `but card bottom is ${cardBottom.toFixed(3)}"`
    );
  }

  // Draw card rectangle
  s.addShape('roundRect', {
    x: block.x, y: block.y, w: block.w, h: block.h,
    fill: { color: C.veryLightTeal },
    line: { type: 'none' },
    rectRadius: 0.08,
  });

  // Header text inside card
  // block: y=(block.y+HEADER_REL_Y) h=HEADER_H → ends (block.y+HEADER_REL_Y+HEADER_H)
  s.addText(block.text, {
    x: block.x + 0.10, y: block.y + HEADER_REL_Y, w: block.w - 0.20, h: HEADER_H,
    fontSize: block.font_size,
    fontFace: F,
    color: resolveColor(block.color),
    bold: true,
    valign: 'top',
    shrinkText: true,
  });

  // Item texts inside card
  block.items.forEach((item, idx) => {
    const itemY = block.y + ITEMS_START_Y + idx * ITEM_H;
    // block: y=itemY h=ITEM_H → ends (itemY+ITEM_H)
    s.addText(item, {
      x: block.x + 0.10, y: itemY, w: block.w - 0.20, h: ITEM_H,
      fontSize: Math.max(block.font_size - 1, 10),
      fontFace: F,
      color: resolveColor(block.color),
      bold: false,
      valign: 'top',
      shrinkText: true,
    });
  });
}

/**
 * Image/asset block renderer (spec item 5).
 * asset_path must be absolute and under ASSETS_DIR (checked in validator).
 */
function addImageBlock(s, block) {
  // y-audit: y=block.y h=block.h → ends (block.y+block.h)  (asset: basename)
  s.addImage({
    path: block.asset_path,
    x: block.x, y: block.y, w: block.w, h: block.h,
  });
}

/**
 * Dispatch a single content block to its renderer by kind.
 * branding: forwarded to addTextBlock for pictogram title-width narrowing.
 * planId:   forwarded to addCardBlock for diagnostic error messages.
 */
function renderBlock(s, block, branding, planId) {
  switch (block.kind) {
    case 'title':
    case 'subtitle':
    case 'paragraph':
    case 'quote_block':
    case 'footer_text':
      addTextBlock(s, block, branding);
      break;
    case 'bullets':
      addBulletsBlock(s, block);
      break;
    case 'card':
      addCardBlock(s, block, planId);
      break;
    case 'image_block':
    case 'asset_block':
      addImageBlock(s, block);
      break;
    default:
      throw new Error(`Unknown block kind: "${block.kind}"`);
  }
}

// ---------------------------------------------------------------------------
// OVERLAP VALIDATOR (pure-JSON; no pptxgenjs dependency)
// ---------------------------------------------------------------------------

/** Check if two axis-aligned rectangles overlap (strict 2D bbox check). */
function rectsOverlap(a, b) {
  // Rectangles do NOT overlap if one is entirely to the left, right, above, or below
  const eps = 0.001;
  const noOverlap =
    (a.x + a.w <= b.x + eps) ||
    (b.x + b.w <= a.x + eps) ||
    (a.y + a.h <= b.y + eps) ||
    (b.y + b.h <= a.y + eps);
  return !noOverlap;
}

/**
 * Validate a single slide plan.
 * Throws with a diagnostic message listing all violations found.
 *
 * Rules:
 *   A — per-block ceiling check (y+h <= CEILING_Y + epsilon)
 *   B — image/asset block asset_path must be absolute and under ASSETS_DIR
 *   C — pairwise 2D bounding-box overlap check (all block kinds)
 *   D — synthetic branding rectangles vs. content blocks
 *
 * Rule D / pictogram note: title and subtitle blocks on pictogram slides are
 * rendered with w:8.35 (right edge 8.95 < 9.03), so their rendered bboxes do
 * not reach the pictogram. The validator uses the RENDERED width (8.35) for
 * title/subtitle blocks when branding === 'pictogram', matching the builder's
 * narrowing in addTextBlock.
 */
function validateSlide(plan) {
  const errors = [];
  // For Rule C/D checks on title/subtitle blocks on pictogram slides, use the
  // rendered width (8.35) rather than the JSON-declared width (8.8), since
  // addTextBlock narrows these blocks to clear the pictogram's left edge.
  const effectiveBlocks = plan.content_blocks.map((b) => {
    if ((b.kind === 'title' || b.kind === 'subtitle') && plan.branding === 'pictogram') {
      return { ...b, w: 8.35 };
    }
    return b;
  });
  const blocks = effectiveBlocks;

  // Synthetic branding rectangles (for Rule D overlap check)
  const brandingRects = [];
  // Accent bar on every slide
  brandingRects.push({ kind: 'accent-bar', x: 0, y: 0, w: 0.63, h: 0.05 });
  if (plan.branding === 'full-logo') {
    // Logo top-left
    brandingRects.push({ kind: 'logo', x: 0.65, y: 0.62, w: 1.45, h: 0.49 });
    // "luigisbox.com" text top-right
    brandingRects.push({ kind: 'luigisbox-text', x: 6.32, y: 0.52, w: 3.30, h: 0.30 });
  }
  if (plan.branding === 'pictogram') {
    // Pictogram top-right
    brandingRects.push({ kind: 'pictogram', x: 9.03, y: 0.62, w: 0.32, h: 0.32 });
  }

  // Rule A — ceiling check
  for (const b of blocks) {
    const bottom = (b.y ?? 0) + (b.h ?? 0);
    if (bottom > CEILING_Y + 0.001) {
      const label = b.kind + (b.text ? ` "${b.text.slice(0, 40)}"` : '');
      errors.push(
        `slide ${plan.id} CEILING: block ${label} y+h=${bottom.toFixed(3)} > CEILING_Y ${CEILING_Y}`
      );
    }
  }

  // Rule B — image/asset block path check
  for (const b of blocks) {
    if (b.kind === 'image_block' || b.kind === 'asset_block') {
      if (!b.asset_path) {
        errors.push(`slide ${plan.id} IMAGE: block has no asset_path`);
      } else if (!path.isAbsolute(b.asset_path)) {
        errors.push(`slide ${plan.id} IMAGE: asset_path is not absolute: ${b.asset_path}`);
      } else if (!b.asset_path.startsWith(ASSETS_DIR + path.sep)) {
        errors.push(
          `slide ${plan.id} IMAGE: asset_path not under ASSETS_DIR: ${b.asset_path}`
        );
      }
    }
  }

  // Rule C — pairwise 2D overlap among content blocks (O(n^2), n<=14)
  for (let i = 0; i < blocks.length; i++) {
    for (let j = i + 1; j < blocks.length; j++) {
      const a = blocks[i];
      const b = blocks[j];
      if (rectsOverlap(a, b)) {
        errors.push(
          `slide ${plan.id} OVERLAP: block[${i}] ${a.kind}(${a.x},${a.y}) ↔ block[${j}] ${b.kind}(${b.x},${b.y})`
        );
      }
    }
  }

  // Rule D — branding rects vs. content blocks
  for (const br of brandingRects) {
    for (let i = 0; i < blocks.length; i++) {
      const b = blocks[i];
      if (rectsOverlap(br, b)) {
        errors.push(
          `slide ${plan.id} BRANDING-OVERLAP: ${br.kind}(${br.x},${br.y}) ↔ block[${i}] ${b.kind}(${b.x},${b.y})`
        );
      }
    }
  }

  // Rule E — card inner-fit check (mirrors addCardBlock assertion; catches violations in dry-run)
  {
    const ITEMS_START_Y = 0.50;
    const ITEM_H        = 0.28;
    for (const b of blocks) {
      if (b.kind === 'card') {
        const minH = ITEMS_START_Y + (b.items.length * ITEM_H);
        if (b.h < minH - 0.001) {
          errors.push(
            `slide ${plan.id} CARD-INNER-FIT: card "${b.text}" h=${b.h} < minH=${minH.toFixed(3)} ` +
            `(ITEMS_START_Y=${ITEMS_START_Y} + ${b.items.length} items × ITEM_H=${ITEM_H})`
          );
        }
      }
    }
  }

  if (errors.length > 0) {
    throw new Error(`Validation failed:\n${errors.join('\n')}`);
  }
}

/** Global validation pass — re-runs validateSlide on every plan. */
function validateDeck(plans) {
  for (const plan of plans) {
    validateSlide(plan);
  }
}

// ---------------------------------------------------------------------------
// Asset pre-flight (spec item 5 + sketch §4)
// ---------------------------------------------------------------------------

function preflightAssets(plans) {
  const required = new Set();
  // Always-needed branding assets (unconditional)
  required.add(path.join(ASSETS_DIR, 'dark-gradient-bg.png'));
  required.add(path.join(ASSETS_DIR, 'logo-full-white.png'));
  required.add(path.join(ASSETS_DIR, 'icon-color.png'));
  // Every image_block / asset_block asset referenced in the plan
  for (const plan of plans) {
    for (const b of plan.content_blocks) {
      if ((b.kind === 'image_block' || b.kind === 'asset_block') && b.asset_path) {
        required.add(b.asset_path);
      }
    }
  }
  const missing = [];
  for (const p of required) {
    try {
      fs.accessSync(p, fs.constants.R_OK);
    } catch {
      // Find which slide references this path for diagnostics
      const refSlide = plans.find((pl) =>
        pl.content_blocks.some((b) => b.asset_path === p)
      );
      const slideRef = refSlide ? ` (referenced in slide ${refSlide.id})` : ' (branding asset)';
      missing.push(`  - ${p}${slideRef}`);
    }
  }
  if (missing.length > 0) {
    throw new Error(
      `Missing assets (hard fail before any slide render):\n${missing.join('\n')}`
    );
  }
}

// ---------------------------------------------------------------------------
// SLIDE BUILDERS
// ---------------------------------------------------------------------------

/**
 * Type 2 — Intro with Headline (dark, full-logo)
 * Slides: 1-1, 1-3, 7-1
 * Stacked blocks vary by slide; builders dispatch via renderBlock.
 *
 * Slide 1-1 y-audit:
 *   title:       y=1.60 h=0.70 → ends 2.30
 *   quote_block: y=2.50 h=2.50 → ends 5.00
 *
 * Slide 1-3 y-audit:
 *   title:       y=1.80 h=0.80 → ends 2.60
 *   subtitle:    y=2.80 h=0.80 → ends 3.60
 *   quote_block: y=3.80 h=0.50 → ends 4.30
 *
 * Slide 7-1 y-audit:
 *   title:       y=1.30 h=0.70 → ends 2.00
 *   quote_block: y=2.20 h=2.70 → ends 4.90
 */
function buildSlide_Type2(pptx, plan) {
  const s = darkSlide(pptx, plan.branding);
  // Stacked blocks — rendered in insertion order (all under CEILING_Y=5.10)
  for (const block of plan.content_blocks) {
    renderBlock(s, block, plan.branding, plan.id);
  }
  s.addNotes(plan.speaker_notes);
  return s;
}

/**
 * Type 5 — Summary Slide (bullets, dark, none-branding)
 * Slides: 1-2, 2-1, 2-2, 2-3, 2-5, 4-1, 4-4, 4-6, 4-7, 5-1, 5-2
 *
 * Slide 1-2 y-audit:
 *   title:     y=0.40 h=0.50 → ends 0.90
 *   paragraph: y=1.00 h=0.90 → ends 1.90
 *   bullets:   y=2.05 h=2.85 → ends 4.90
 *
 * Slide 2-1 y-audit:
 *   title:     y=0.40 h=0.50 → ends 0.90
 *   paragraph: y=1.00 h=0.90 → ends 1.90
 *   paragraph: y=2.00 h=1.05 → ends 3.05
 *   bullets:   y=3.15 h=1.85 → ends 5.00
 *
 * Slide 2-2 y-audit:
 *   title:       y=0.40 h=0.50 → ends 0.90
 *   paragraph:   y=1.00 h=0.75 → ends 1.75
 *   bullets:     y=1.90 h=1.20 → ends 3.10
 *   paragraph:   y=3.25 h=1.25 → ends 4.50
 *   image_block: y=3.50 h=0.50 → ends 4.00
 *
 * Slide 2-3 y-audit:
 *   title:       y=0.40 h=0.50 → ends 0.90
 *   image_block: y=0.40 h=0.50 → ends 0.90
 *   paragraph:   y=1.10 h=1.10 → ends 2.20
 *   bullets:     y=2.30 h=1.70 → ends 4.00
 *   quote_block: y=4.10 h=0.80 → ends 4.90
 *
 * Slide 2-5 y-audit:
 *   title:       y=0.40 h=0.50 → ends 0.90
 *   paragraph:   y=1.00 h=0.55 → ends 1.55
 *   bullets:     y=1.65 h=1.20 → ends 2.85
 *   paragraph:   y=2.95 h=0.85 → ends 3.80
 *   quote_block: y=3.95 h=0.95 → ends 4.90
 *
 * Slide 4-1 y-audit:
 *   title:       y=0.40 h=0.50 → ends 0.90
 *   paragraph:   y=1.00 h=0.80 → ends 1.80
 *   bullets:     y=1.95 h=1.70 → ends 3.65
 *   quote_block: y=3.80 h=0.55 → ends 4.35
 *   quote_block: y=4.50 h=0.40 → ends 4.90
 *
 * Slide 4-4 y-audit:
 *   title:       y=0.35 h=0.45 → ends 0.80
 *   bullets:     y=0.90 h=1.55 → ends 2.45
 *   paragraph:   y=2.55 h=0.35 → ends 2.90
 *   bullets:     y=3.00 h=2.00 → ends 5.00
 *
 * Slide 4-6 y-audit:
 *   title:       y=0.40 h=0.50 → ends 0.90
 *   paragraph:   y=1.00 h=0.55 → ends 1.55
 *   bullets:     y=1.65 h=2.70 → ends 4.35
 *   quote_block: y=4.45 h=0.55 → ends 5.00
 *
 * Slide 4-7 y-audit:
 *   title:       y=0.35 h=0.45 → ends 0.80
 *   bullets:     y=0.90 h=1.70 → ends 2.60
 *   paragraph:   y=2.70 h=0.35 → ends 3.05
 *   bullets:     y=3.10 h=1.90 → ends 5.00
 *
 * Slide 5-1 y-audit:
 *   title:       y=0.40 h=0.50 → ends 0.90
 *   image_block: y=0.40 h=0.50 → ends 0.90
 *   quote_block: y=1.05 h=0.50 → ends 1.55
 *   paragraph:   y=1.65 h=0.40 → ends 2.05
 *   bullets:     y=2.15 h=1.05 → ends 3.20
 *   paragraph:   y=3.25 h=0.45 → ends 3.70
 *   paragraph:   y=3.80 h=0.35 → ends 4.15
 *   bullets:     y=4.20 h=0.85 → ends 5.05
 *
 * Slide 5-2 y-audit:
 *   title:       y=0.40 h=0.50 → ends 0.90
 *   quote_block: y=1.05 h=0.55 → ends 1.60
 *   paragraph:   y=1.75 h=0.55 → ends 2.30
 *   paragraph:   y=2.45 h=1.00 → ends 3.45
 *   quote_block: y=3.60 h=0.60 → ends 4.20
 *   footer_text: y=4.40 h=0.30 → ends 4.70
 */
function buildSlide_Type5(pptx, plan) {
  const s = darkSlide(pptx, plan.branding);
  // Stacked blocks — all under CEILING_Y=5.10
  for (const block of plan.content_blocks) {
    renderBlock(s, block, plan.branding, plan.id);
  }
  s.addNotes(plan.speaker_notes);
  return s;
}

/**
 * Type 9 — Challenge / Solution (split, light, pictogram)
 * Slides: 4-3, 5-3
 *
 * Slide 4-3 y-audit:
 *   title:        y=0.40 h=0.40 → ends 0.80
 *   image_block:  y=0.85 h=0.43 → ends 1.28
 *   paragraph:    y=0.87 h=0.40 → ends 1.27
 *   paragraph:    y=1.35 h=1.20 → ends 2.55
 *   image_block:  y=2.80 h=0.43 → ends 3.23
 *   paragraph:    y=2.82 h=0.40 → ends 3.22
 *   bullets:      y=3.30 h=1.70 → ends 5.00
 *
 * Slide 5-3 y-audit:
 *   title:        y=0.35 h=0.40 → ends 0.75
 *   image_block:  y=0.85 h=0.43 → ends 1.28
 *   paragraph:    y=0.87 h=0.40 → ends 1.27
 *   quote_block:  y=1.35 h=0.95 → ends 2.30
 *   image_block:  y=2.55 h=0.43 → ends 2.98
 *   paragraph:    y=2.57 h=0.40 → ends 2.97
 *   quote_block:  y=3.05 h=1.00 → ends 4.05
 *   quote_block:  y=4.25 h=0.75 → ends 5.00
 */
function buildSlide_Type9(pptx, plan) {
  const s = lightSlide(pptx, plan.branding);
  // Stacked blocks — all under CEILING_Y=5.10
  for (const block of plan.content_blocks) {
    renderBlock(s, block, plan.branding, plan.id);
  }
  s.addNotes(plan.speaker_notes);
  return s;
}

/**
 * Type 11 — Problem Statement Cards (3, light, pictogram)
 * Slides: 3-2
 *
 * Slide 3-2 y-audit:
 *   title:       y=0.40 h=0.50 → ends 0.90
 *   card:        y=1.05 h=1.08 → ends 2.13
 *   card:        y=2.25 h=1.08 → ends 3.33
 *   card:        y=3.45 h=1.08 → ends 4.53
 *   quote_block: y=4.65 h=0.40 → ends 5.05
 */
function buildSlide_Type11(pptx, plan) {
  const s = lightSlide(pptx, plan.branding);
  // Stacked blocks — all under CEILING_Y=5.10
  for (const block of plan.content_blocks) {
    renderBlock(s, block, plan.branding, plan.id);
  }
  s.addNotes(plan.speaker_notes);
  return s;
}

/**
 * Type 12 — "What You See / What It Reveals" (light, pictogram)
 * Slides: 3-1, 4-2, 4-5
 *
 * Two-pass z-order: cards (background) rendered first, then text labels.
 * Per IC-8.9 and S1 §5a: arrows/connectors + column labels rendered on top of cards.
 *
 * Slide 3-1 y-audit:
 *   title:    y=0.40 h=0.50 → ends 0.90
 *   subtitle: y=1.00 h=0.60 → ends 1.60
 *   para(WC): y=1.70 h=0.30 → ends 2.00
 *   para(WCC):y=1.70 h=0.30 → ends 2.00
 *   card:     y=2.10 h=0.85 → ends 2.95
 *   card:     y=2.10 h=0.85 → ends 2.95
 *   card:     y=3.05 h=0.85 → ends 3.90
 *   card:     y=3.05 h=0.85 → ends 3.90
 *   card:     y=4.00 h=0.85 → ends 4.85
 *   card:     y=4.00 h=0.85 → ends 4.85
 *
 * Slide 4-2 y-audit:
 *   title:     y=0.40 h=0.50 → ends 0.90
 *   paragraph: y=0.95 h=1.00 → ends 1.95
 *   para(WYS): y=2.05 h=0.30 → ends 2.35
 *   para(WIAM):y=2.05 h=0.30 → ends 2.35
 *   card:      y=2.45 h=0.78 → ends 3.23
 *   card:      y=2.45 h=0.78 → ends 3.23
 *   card:      y=3.25 h=0.78 → ends 4.03
 *   card:      y=3.25 h=0.78 → ends 4.03
 *   card:      y=4.05 h=0.78 → ends 4.83
 *   card:      y=4.05 h=0.78 → ends 4.83
 *   footer:    y=4.85 h=0.25 → ends 5.10
 *
 * Slide 4-5 y-audit (R2: footer relocated above cards; 4×h=0.78 fills y=1.98→5.10):
 *   title:     y=0.40 h=0.40 → ends 0.80
 *   subtitle:  y=0.85 h=0.75 → ends 1.60
 *   footer:    y=1.60 h=0.10 → ends 1.70  (relocated; fits subtitle→col-label gap)
 *   para(WYS): y=1.70 h=0.25 → ends 1.95
 *   para(WIH): y=1.70 h=0.25 → ends 1.95
 *   card:      y=1.98 h=0.78 → ends 2.76
 *   card:      y=1.98 h=0.78 → ends 2.76
 *   card:      y=2.76 h=0.78 → ends 3.54
 *   card:      y=2.76 h=0.78 → ends 3.54
 *   card:      y=3.54 h=0.78 → ends 4.32
 *   card:      y=3.54 h=0.78 → ends 4.32
 *   card:      y=4.32 h=0.78 → ends 5.10
 *   card:      y=4.32 h=0.78 → ends 5.10
 */
function buildSlide_Type12(pptx, plan) {
  const s = lightSlide(pptx, plan.branding);
  // Two-pass z-order: render card blocks first, then all other blocks on top.
  const cardBlocks   = plan.content_blocks.filter((b) => b.kind === 'card');
  const otherBlocks  = plan.content_blocks.filter((b) => b.kind !== 'card');
  // Pass 1 — background cards
  for (const block of cardBlocks) {
    renderBlock(s, block, plan.branding, plan.id);
  }
  // Pass 2 — labels, title, footer, images on top
  for (const block of otherBlocks) {
    renderBlock(s, block, plan.branding, plan.id);
  }
  s.addNotes(plan.speaker_notes);
  return s;
}

/**
 * Type 15 — Section Divider (dark, full-logo)
 * Slides: 8-1
 *
 * Slide 8-1 y-audit:
 *   title:       y=1.20 h=0.80 → ends 2.00
 *   image_block: y=2.10 h=0.60 → ends 2.70
 *   paragraph:   y=2.90 h=0.30 → ends 3.20
 *   bullets:     y=3.25 h=1.70 → ends 4.95
 */
function buildSlide_Type15(pptx, plan) {
  const s = darkSlide(pptx, plan.branding);
  // Stacked blocks — all under CEILING_Y=5.10
  for (const block of plan.content_blocks) {
    renderBlock(s, block, plan.branding, plan.id);
  }
  s.addNotes(plan.speaker_notes);
  return s;
}

/**
 * Type 31 — Two Features Stacked (light, pictogram)
 * Slides: 2-4, 6-1
 *
 * Type assertion: must have exactly 2 'card' blocks.
 *
 * Slide 2-4 y-audit:
 *   title:       y=0.40 h=0.50 → ends 0.90
 *   card:        y=1.32 h=1.68 → ends 3.00
 *   card:        y=3.25 h=1.68 → ends 4.93
 *   footer_text: y=5.00 h=0.10 → ends 5.10  ← shrinkText:true applied
 *
 * Slide 6-1 y-audit:
 *   title:    y=0.40 h=0.40 → ends 0.80
 *   subtitle: y=0.90 h=0.35 → ends 1.25
 *   card:     y=1.32 h=1.68 → ends 3.00
 *   card:     y=3.25 h=1.68 → ends 4.93
 */
function buildSlide_Type31(pptx, plan) {
  // Type-specific pre-assertion
  const cardCount = plan.content_blocks.filter((b) => b.kind === 'card').length;
  if (cardCount !== 2) {
    throw new Error(
      `buildSlide_Type31 expects exactly 2 card blocks on slide ${plan.id}, found ${cardCount}`
    );
  }
  const s = lightSlide(pptx, plan.branding);
  // Stacked blocks — all under CEILING_Y=5.10
  for (const block of plan.content_blocks) {
    renderBlock(s, block, plan.branding, plan.id);
  }
  s.addNotes(plan.speaker_notes);
  return s;
}

// ---------------------------------------------------------------------------
// Dispatch table (keyed on integer slide_type)
// ---------------------------------------------------------------------------
const SLIDE_BUILDERS = {
  2:  buildSlide_Type2,
  5:  buildSlide_Type5,
  9:  buildSlide_Type9,
  11: buildSlide_Type11,
  12: buildSlide_Type12,
  15: buildSlide_Type15,
  31: buildSlide_Type31,
};

// ---------------------------------------------------------------------------
// MAIN
// ---------------------------------------------------------------------------
async function main() {
  // Load slide plan JSON
  const raw = fs.readFileSync(PLAN_PATH, 'utf-8');
  const planData = JSON.parse(raw);
  const plans = planData.slides;

  // Spec item 10: set layout once at top of main()
  const pptx = new PptxGenJS();
  pptx.layout = 'LAYOUT_16x9';

  // Asset pre-flight — hard fail before any slide render
  preflightAssets(plans);

  let imageBlockCount = 0;

  // Per-slide: validate → build → note
  for (const plan of plans) {
    // Per-slide validation BEFORE building (spec item 6)
    validateSlide(plan);

    // Dispatch to builder
    const builder = SLIDE_BUILDERS[plan.slide_type];
    if (!builder) {
      throw new Error(`Unsupported slide_type ${plan.slide_type} on slide ${plan.id}`);
    }
    builder(pptx, plan);

    // Count image blocks for summary
    imageBlockCount += plan.content_blocks.filter(
      (b) => b.kind === 'image_block' || b.kind === 'asset_block'
    ).length;
  }

  // Global validation pass (after loop, before writeFile)
  validateDeck(plans);

  // Write output
  await pptx.writeFile({ fileName: OUTPUT_PATH });
  console.log(`Wrote ${plans.length} slides to ${OUTPUT_PATH}`);
  console.log(`  Image blocks rendered: ${imageBlockCount}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});

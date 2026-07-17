# Urban Thread — Fashion House Website

Submission for **CSC 106 / IFT 203 — Introduction to Web Technologies** (Tutor-Marked Assessment, MIVA Open University).

A 5-page website for the fictional streetwear fashion house **Urban Thread**, built with vanilla HTML5, CSS3 and JavaScript — no frameworks. Open `index.html` in any browser; all links are relative, so the folder works from disk or any web server. Board avatars and product artwork are generated at view time by the free, MIT-licensed [DiceBear](https://www.dicebear.com/) API, so an internet connection is needed for images to appear.

## Files

| File | Purpose |
|---|---|
| `index.html` | Home — brand intro + DOM structure visualiser |
| `products.html` | Product showcase with filter and search |
| `trustees.html` | Board of Trustees with expandable bios |
| `inquiries.html` | Inquiries & appointment booking form |
| `events.html` | Upcoming events with live countdown |
| `style.css` | Single shared external stylesheet |
| `script.js` | Single shared script (per-page features keyed by `<body data-page>`) |

## Requirement checklist

| Brief requirement | Where it is satisfied |
|---|---|
| At least 5 pages | `index`, `products`, `trustees`, `inquiries`, `events` |
| Naming | "Urban Thread" — used consistently in brand bar, copy and footer on every page |
| Product showcase | `products.html` — 9 products with names, prices (₦), descriptions and images |
| Consistent link structure | Identical `<nav id="site-nav">` markup (same menu, order and targets) on all 5 pages |
| Board of Trustees | `trustees.html` — 4 members with titles, bios and DiceBear illustrated portraits |
| Inquiries & appointments | `inquiries.html` — name/email/phone/reason/date/message form, front-end only |
| Upcoming events | `events.html` — 3 events with names, dates and descriptions |
| Marquee on all pages | `<marquee class="ticker">` scrolling upcoming events in every page header |
| JavaScript feature per page | Home: DOM tree visualiser · Products: category filter + search · Trustees: expandable bios · Inquiries: validation + confirmation message · Events: live countdown (plus shared mobile nav toggle, active-link highlight, auto year) |
| External CSS | `style.css` only — no inline styles, no `<style>` blocks |
| DOM structure | "Under the Hood" section on `index.html`: `script.js` recursively walks the live DOM and renders it as an indented tree |

## Design notes

- **Palette — "Roadwork":** Bone `#EFECE3`, Chalk `#FFFFFF`, Ink `#17171C`, Cobalt `#2438E8`, Signal `#FF4D1F`, Concrete `#B9B6AB`.
- **Type:** condensed poster display stack (Haettenschweiler / Arial Narrow Bold / Impact) over a Helvetica/Arial body, with Courier New for garment-tag style labels.
- **Signature element:** "sticker" cards — hard 2px ink borders with offset solid shadows — plus cobalt hazard-stripe divider bars.
- Responsive to mobile (collapsible menu under 720px), visible keyboard focus states, and `prefers-reduced-motion` support (animations disabled; the marquee is stopped via JS).

# Accessibility Audit Fixes — Design Spec

**Standard:** WCAG 2.1 AA | **Date:** 2026-03-31
**Scope:** Standalone HTML visualization (`visualization.py`), VS Code webview (`graph.ts`, `graphWebview.ts`)

---

## Overview

Fix all 23 accessibility issues identified in the WCAG 2.1 AA audit across the two frontend surfaces. The VS Code tree views are already accessible via native APIs — no changes needed there.

All changes are direct in-place edits. The two surfaces (Python string template vs TypeScript) cannot share code, so fixes are applied independently to each.

---

## 1. Distinct Node Shapes (Critical — Issues #1, #19)

**Problem:** All nodes are circles, differentiated only by color. Colorblind users (~8% of males) cannot distinguish node types.

**Fix:** Use `d3.symbol()` to render distinct shapes per node kind:

| Kind     | Shape           | D3 Symbol Type         |
|----------|-----------------|------------------------|
| File     | Circle          | `d3.symbolCircle`      |
| Class    | Square          | `d3.symbolSquare`      |
| Function | Triangle-up     | `d3.symbolTriangle`    |
| Test     | Diamond         | `d3.symbolDiamond`     |
| Type     | Cross/plus      | `d3.symbolCross`       |

### Standalone (`visualization.py`)

- Replace the `<circle>` append in `updateNodes` (lines 536-547) with `<path>` using `d3.symbol().type(KIND_SHAPE[d.kind]).size(area)`
- Size the symbol area proportional to `KIND_RADIUS` squared (to match current visual sizes)
- Keep the File glow-ring as a `<circle>` underneath the shape path
- Update `highlightConnected()` and any other code that selects `circle` elements to select `path.node-shape` instead
- Add a `KIND_SHAPE` mapping alongside `KIND_COLOR`
- Update legend dots to use matching mini SVG shapes instead of colored circles

### VS Code (`graph.ts`)

- Same approach: replace `<circle>` in node rendering (lines 371-380) with `<path>` using `d3.symbol()`
- Update `NODE_RADIUS` usage to symbol area calculations
- Update any `circle` CSS selectors or D3 selections to `path.node-shape`

---

## 2. Color Contrast Fixes (Major — Issues #4, #5, #6, #20)

**Problem:** Text using `#8b949e` on dark backgrounds fails the 4.5:1 minimum contrast ratio.

### Standalone (`visualization.py`) — Contrast fixes

| Element | Current | New | Ratio |
|---------|---------|-----|-------|
| `.tt-label` (tooltip) | `#8b949e` | `#9eaab6` | ~4.8:1 |
| Stats bar label | `#8b949e` | `#9eaab6` | ~4.8:1 |
| `.dp-meta` (detail panel) | `#8b949e` | `#9eaab6` | ~4.8:1 |
| `.dp-close` | `#8b949e` | `#9eaab6` | ~4.8:1 |
| Search placeholder | `#484f58` | `#6e7681` | ~3.2:1 (placeholder exempt from 4.5:1 per WCAG, but improved) |

Implementation: Find-and-replace `#8b949e` with `#9eaab6` in CSS sections where it's used as text color on dark backgrounds. Replace `#484f58` with `#6e7681` for placeholder text.

### VS Code (`graphWebview.ts`) — Theme-compatible colors

Replace hardcoded tooltip colors with VS Code CSS variables:
- `.tooltip-params` `#a6e3a1` → `var(--vscode-debugTokenExpression-string, #a6e3a1)` (fallback to current)
- `.tooltip-return` `#89b4fa` → `var(--vscode-debugTokenExpression-number, #89b4fa)` (fallback to current)

This ensures contrast in all themes (light, dark, high contrast).

---

## 3. Keyboard Navigation (Critical — Issues #7, #8, #12, #21)

### Graph Nodes — Both surfaces

Add keyboard interaction to the SVG graph nodes:

1. **Tab into graph:** Add `tabindex="0"` to each node's `<g>` element
2. **Enter/Space:** Trigger same action as click (select node, show detail panel)
3. **Arrow keys:** Move focus to the nearest neighbor node in that direction (use node x/y positions to determine "nearest up/down/left/right")
4. **Escape:** Deselect current node, close detail panel
5. **Focus indicator:** Add a visible focus ring (2px white outline) on `:focus-visible` for node shapes

Implementation pattern (both surfaces):
```javascript
nodeG.attr("tabindex", 0)
  .on("keydown", function(event, d) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      // same as click handler
    } else if (event.key === "Escape") {
      // deselect, close detail panel
    } else if (["ArrowUp","ArrowDown","ArrowLeft","ArrowRight"].includes(event.key)) {
      event.preventDefault();
      // find nearest node in direction, focus it
    }
  });
```

### Legend Edge Toggles — Standalone (Issue #8)

Change edge toggle items from `<div>` to `<button>`:
- Native `<button>` provides keyboard and ARIA semantics automatically
- These are in the legend HTML template (lines 340-344)

### Search Results — Standalone (Issue #12)

- Add `role="listbox"` to `.search-results` container
- Add `role="option"` and `tabindex="-1"` to each `.sr-item`
- Arrow keys navigate items, Enter selects
- Manage `aria-activedescendant` on the search input

### Edge Pills — VS Code (Issue #22)

- Add `tabindex="0"` to each `<span class="edge-pill">`
- Add keyboard handler for Enter/Space to toggle
- Already covered by ARIA section below

---

## 4. ARIA Roles & Announcements (Major — Issues #14, #15, #16, #22, #23)

### Tooltip — Both surfaces (Issue #15)

- Add `role="tooltip"` and `aria-live="polite"` to the tooltip `<div>`
- Add `id="graph-tooltip"` (standalone already has it via class, VS Code needs id)
- On node focus/hover, set `aria-describedby="graph-tooltip"` on the focused node

### Detail Panel — Standalone (Issue #16)

- Add `role="dialog"`, `aria-label="Node details"`, `aria-modal="false"` to detail panel
- On open: move focus into the panel (to heading or first interactive element)
- On close (click X or Escape): return focus to the node that opened it
- Store reference to trigger element before opening

### Communities Button — Standalone (Issue #14)

- Add `aria-pressed="false"` to the Communities button (line 358)
- Toggle `aria-pressed` in the click handler (lines 675-682), matching the Labels button pattern

### Edge Pills — VS Code (Issue #22)

- Add `role="button"` to each `<span class="edge-pill">`
- Add `aria-pressed="true"` (since they start active)
- Add `aria-label="Toggle [kind] edges"` (e.g., `aria-label="Toggle Calls edges"`)
- Toggle `aria-pressed` in click handler

### Search Input — VS Code (Issue #23)

- Add `aria-label="Search nodes"` to the search `<input>`

---

## 5. Edge Differentiation (Major — Issue #2)

**Problem:** Edge types are hard to distinguish, especially for colorblind users.

### Standalone (`visualization.py`)

Widen dash pattern differences in `EDGE_CFG`:

| Edge Type    | Current Dash | New Dash   | Current Width | New Width |
|-------------|-------------|------------|---------------|-----------|
| CONTAINS    | none        | none       | 1             | 1         |
| CALLS       | none        | none       | 1.5           | 2         |
| IMPORTS_FROM| `6,3`       | `8,4`      | 1.5           | 2         |
| INHERITS    | `3,4`       | `2,6`      | 2             | 2.5       |

Add edge-type label on hover: when hovering a node and connected edges are highlighted, show the edge type as a small text label at the midpoint of each highlighted edge.

---

## 6. Minor Polish (Issues #3, #9, #10, #11, #13)

### Legend Dots — Standalone (Issue #3)

- Increase `.legend-circle` from 10x10px to 16x16px
- Add 1px border (`border: 1px solid rgba(255,255,255,0.3)`)
- Replace circle divs with mini inline SVGs using the matching shape from section 1

### Focus Indicators (Issues #9, #10)

Add `:focus-visible` outline styles:
```css
.filter-item input:focus-visible { outline: 2px solid #58a6ff; outline-offset: 2px; }
.dp-close:focus-visible { outline: 2px solid #58a6ff; outline-offset: 2px; }
.legend-edge:focus-visible { outline: 2px solid #58a6ff; outline-offset: 2px; }
```

### Skip Navigation & Landmarks — Standalone (Issue #11)

- Add a visually-hidden skip link as first body element: `<a href="#graph-svg" class="skip-link">Skip to graph</a>`
- Add `role="navigation"` to the legend
- Add `role="main"` to a wrapper around the SVG
- Add `id="graph-svg"` to the SVG element

### Help Overlay — Standalone (Issue #13)

- Add a `?` button in the controls section
- On click, toggle a small overlay panel listing keyboard/mouse controls:
  - Click node: select and show details
  - Shift+click: expand file
  - Drag: move nodes
  - Scroll: zoom
  - Arrow keys: navigate between nodes
  - Enter/Space: select focused node
  - Escape: deselect
- The overlay gets `role="dialog"` and `aria-label="Keyboard shortcuts"`

---

## Files Modified

| File | Changes |
|------|---------|
| `code_review_graph/visualization.py` | Shapes, contrast, keyboard nav, ARIA, edge styling, legend, focus styles, landmarks, help overlay |
| `code-review-graph-vscode/src/webview/graph.ts` | Shapes, keyboard nav, tooltip ARIA |
| `code-review-graph-vscode/src/views/graphWebview.ts` | Contrast (CSS vars), edge pill ARIA, search label, tooltip role |

---

## Testing

- Run `uv run pytest tests/` — visualization tests check HTML output structure, will need updates for new elements/attributes
- Manual verification: open standalone HTML, tab through all interactive elements, verify screen reader announcements
- VS Code: test in high contrast theme to verify CSS variable fallbacks

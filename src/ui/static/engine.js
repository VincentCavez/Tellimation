// Tellimations Pixel Art Engine
// Canvas: 280x180, rendered at 3x scale (840x540) with image-rendering: pixelated

const PW = 280;
const PH = 180;
const DEFAULT_SCALE = 3;

// ---------------------------------------------------------------------------
// PixelBuffer
// ---------------------------------------------------------------------------

class PixelBuffer {
  constructor(width = PW, height = PH) {
    this.width = width;
    this.height = height;
    this.data = new Array(width * height);
    this.clear();
  }

  clear() {
    for (let i = 0; i < this.data.length; i++) {
      this.data[i] = { r: 0, g: 0, b: 0, e: '', _br: 0, _bg: 0, _bb: 0 };
    }
  }

  // -- internal helpers -----------------------------------------------------

  _inBounds(x, y) {
    return x >= 0 && x < this.width && y >= 0 && y < this.height;
  }

  _set(x, y, r, g, b, entityId) {
    x = Math.round(x);
    y = Math.round(y);
    if (!this._inBounds(x, y)) return;
    const idx = y * this.width + x;
    this.data[idx].r = r;
    this.data[idx].g = g;
    this.data[idx].b = b;
    this.data[idx].e = entityId;
  }

  // -- primitive API (matches CLAUDE.md spec) --------------------------------

  px(x, y, r, g, b, entityId) {
    this._set(x, y, r, g, b, entityId);
  }

  rect(x, y, width, height, r, g, b, entityId) {
    x = Math.round(x);
    y = Math.round(y);
    for (let dy = 0; dy < height; dy++) {
      for (let dx = 0; dx < width; dx++) {
        this._set(x + dx, y + dy, r, g, b, entityId);
      }
    }
  }

  circ(cx, cy, radius, r, g, b, entityId) {
    cx = Math.round(cx);
    cy = Math.round(cy);
    radius = Math.round(radius);
    const r2 = radius * radius;
    for (let dy = -radius; dy <= radius; dy++) {
      for (let dx = -radius; dx <= radius; dx++) {
        if (dx * dx + dy * dy <= r2) {
          this._set(cx + dx, cy + dy, r, g, b, entityId);
        }
      }
    }
  }

  ellip(cx, cy, rx, ry, r, g, b, entityId) {
    cx = Math.round(cx);
    cy = Math.round(cy);
    rx = Math.round(rx);
    ry = Math.round(ry);
    if (rx === 0 || ry === 0) return;
    for (let dy = -ry; dy <= ry; dy++) {
      for (let dx = -rx; dx <= rx; dx++) {
        if ((dx * dx) / (rx * rx) + (dy * dy) / (ry * ry) <= 1) {
          this._set(cx + dx, cy + dy, r, g, b, entityId);
        }
      }
    }
  }

  tri(x1, y1, x2, y2, x3, y3, r, g, b, entityId) {
    // Scanline fill via bounding box + barycentric test
    const minX = Math.floor(Math.min(x1, x2, x3));
    const maxX = Math.ceil(Math.max(x1, x2, x3));
    const minY = Math.floor(Math.min(y1, y2, y3));
    const maxY = Math.ceil(Math.max(y1, y2, y3));

    const denom = (y2 - y3) * (x1 - x3) + (x3 - x2) * (y1 - y3);
    if (denom === 0) return; // degenerate

    for (let py = minY; py <= maxY; py++) {
      for (let px = minX; px <= maxX; px++) {
        const w1 = ((y2 - y3) * (px - x3) + (x3 - x2) * (py - y3)) / denom;
        const w2 = ((y3 - y1) * (px - x3) + (x1 - x3) * (py - y3)) / denom;
        const w3 = 1 - w1 - w2;
        if (w1 >= 0 && w2 >= 0 && w3 >= 0) {
          this._set(px, py, r, g, b, entityId);
        }
      }
    }
  }

  line(x1, y1, x2, y2, r, g, b, entityId) {
    // Bresenham's line algorithm
    x1 = Math.round(x1);
    y1 = Math.round(y1);
    x2 = Math.round(x2);
    y2 = Math.round(y2);
    let dx = Math.abs(x2 - x1);
    let dy = -Math.abs(y2 - y1);
    const sx = x1 < x2 ? 1 : -1;
    const sy = y1 < y2 ? 1 : -1;
    let err = dx + dy;

    while (true) {
      this._set(x1, y1, r, g, b, entityId);
      if (x1 === x2 && y1 === y2) break;
      const e2 = 2 * err;
      if (e2 >= dy) { err += dy; x1 += sx; }
      if (e2 <= dx) { err += dx; y1 += sy; }
    }
  }

  thickLine(x1, y1, x2, y2, width, r, g, b, entityId) {
    const half = width / 2;
    // Direction perpendicular to the line
    const dx = x2 - x1;
    const dy = y2 - y1;
    const len = Math.sqrt(dx * dx + dy * dy);
    if (len === 0) {
      this.circ(Math.round(x1), Math.round(y1), Math.round(half), r, g, b, entityId);
      return;
    }
    // Normal vector
    const nx = -dy / len;
    const ny = dx / len;

    // Rasterize the quad formed by offsetting the line endpoints
    const corners = [
      { x: x1 + nx * half, y: y1 + ny * half },
      { x: x1 - nx * half, y: y1 - ny * half },
      { x: x2 - nx * half, y: y2 - ny * half },
      { x: x2 + nx * half, y: y2 + ny * half },
    ];
    // Fill as two triangles
    this.tri(
      corners[0].x, corners[0].y,
      corners[1].x, corners[1].y,
      corners[2].x, corners[2].y,
      r, g, b, entityId
    );
    this.tri(
      corners[0].x, corners[0].y,
      corners[2].x, corners[2].y,
      corners[3].x, corners[3].y,
      r, g, b, entityId
    );
  }

  arc(cx, cy, radius, startAngle, endAngle, r, g, b, entityId) {
    cx = Math.round(cx);
    cy = Math.round(cy);
    radius = Math.round(radius);
    // Normalize angles to [0, 2PI)
    const TWO_PI = Math.PI * 2;
    startAngle = ((startAngle % TWO_PI) + TWO_PI) % TWO_PI;
    endAngle = ((endAngle % TWO_PI) + TWO_PI) % TWO_PI;

    // Step in small angle increments for pixel coverage
    const circumference = TWO_PI * radius;
    const steps = Math.max(Math.ceil(circumference * 2), 64);
    const totalArc = endAngle > startAngle
      ? endAngle - startAngle
      : TWO_PI - startAngle + endAngle;

    for (let i = 0; i <= steps; i++) {
      const angle = startAngle + (totalArc * i) / steps;
      const px = Math.round(cx + radius * Math.cos(angle));
      const py = Math.round(cy + radius * Math.sin(angle));
      this._set(px, py, r, g, b, entityId);
    }
  }

  // -- query API ------------------------------------------------------------

  getPixel(x, y) {
    x = Math.round(x);
    y = Math.round(y);
    if (!this._inBounds(x, y)) return { r: 0, g: 0, b: 0, e: '' };
    const p = this.data[y * this.width + x];
    return { r: p.r, g: p.g, b: p.b, e: p.e };
  }

  getPixelsForPrefix(prefix) {
    const indices = [];
    for (let i = 0; i < this.data.length; i++) {
      const e = this.data[i].e;
      if (e === prefix || e.startsWith(prefix + '.')) {
        indices.push(i);
      }
    }
    return indices;
  }

  getEntityBounds(prefix) {
    let x1 = this.width, y1 = this.height, x2 = -1, y2 = -1;
    for (let i = 0; i < this.data.length; i++) {
      const e = this.data[i].e;
      if (e === prefix || e.startsWith(prefix + '.')) {
        const x = i % this.width;
        const y = Math.floor(i / this.width);
        if (x < x1) x1 = x;
        if (x > x2) x2 = x;
        if (y < y1) y1 = y;
        if (y > y2) y2 = y;
      }
    }
    if (x2 === -1) return null;
    return { x1, y1, x2, y2 };
  }

  snapshotBackground() {
    for (let i = 0; i < this.data.length; i++) {
      const p = this.data[i];
      p._br = p.r;
      p._bg = p.g;
      p._bb = p.b;
    }
  }

  snapshot() {
    for (let i = 0; i < this.data.length; i++) {
      const p = this.data[i];
      p._r = p.r;
      p._g = p.g;
      p._b = p.b;
    }
  }

  restore() {
    for (let i = 0; i < this.data.length; i++) {
      const p = this.data[i];
      if (p._r !== undefined) {
        p.r = p._r;
        p.g = p._g;
        p.b = p._b;
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Renderer
// ---------------------------------------------------------------------------

class Renderer {
  constructor(canvas, pixelBuffer, scale = DEFAULT_SCALE) {
    this.canvas = canvas;
    this.buf = pixelBuffer;
    this.scale = scale;

    canvas.width = pixelBuffer.width * scale;
    canvas.height = pixelBuffer.height * scale;
    canvas.style.imageRendering = 'pixelated';
    canvas.style.imageRendering = 'crisp-edges';

    this.ctx = canvas.getContext('2d');
    this.ctx.imageSmoothingEnabled = false;

    // Off-screen 1:1 canvas for building ImageData
    this._offCanvas = document.createElement('canvas');
    this._offCanvas.width = pixelBuffer.width;
    this._offCanvas.height = pixelBuffer.height;
    this._offCtx = this._offCanvas.getContext('2d');
  }

  render() {
    const w = this.buf.width;
    const h = this.buf.height;
    const imgData = this._offCtx.createImageData(w, h);
    const pixels = imgData.data;

    for (let i = 0; i < this.buf.data.length; i++) {
      const p = this.buf.data[i];
      const off = i * 4;
      pixels[off] = p.r;
      pixels[off + 1] = p.g;
      pixels[off + 2] = p.b;
      pixels[off + 3] = 255;
    }

    this._offCtx.putImageData(imgData, 0, 0);
    this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    this.ctx.drawImage(
      this._offCanvas,
      0, 0, w, h,
      0, 0, this.canvas.width, this.canvas.height
    );
  }

}

// ---------------------------------------------------------------------------
// EntityRegistry
// ---------------------------------------------------------------------------

class EntityRegistry {
  constructor(pixelBuffer) {
    this.buf = pixelBuffer;
  }

  getAllEntities() {
    const ids = new Set();
    for (let i = 0; i < this.buf.data.length; i++) {
      const e = this.buf.data[i].e;
      if (e) ids.add(e);
    }
    return Array.from(ids).sort();
  }

  getTree() {
    const entities = this.getAllEntities();
    const tree = {};

    for (const id of entities) {
      const parts = id.split('.');
      let node = tree;
      for (const part of parts) {
        if (!node[part]) node[part] = {};
        node = node[part];
      }
    }
    return tree;
  }

  getChildren(prefix) {
    const entities = this.getAllEntities();
    const depth = prefix ? prefix.split('.').length : 0;
    const children = new Set();

    for (const id of entities) {
      if (prefix && id !== prefix && !id.startsWith(prefix + '.')) continue;
      if (!prefix && id.includes('.')) {
        children.add(id.split('.')[0]);
        continue;
      }
      if (!prefix) {
        children.add(id);
        continue;
      }
      const parts = id.split('.');
      if (parts.length > depth) {
        children.add(parts.slice(0, depth + 1).join('.'));
      }
    }
    return Array.from(children).sort();
  }
}

// ---------------------------------------------------------------------------
// executeSpriteCode
// ---------------------------------------------------------------------------

function executeSpriteCode(code, pixelBuffer) {
  try {
    const fn = new Function(
      'px', 'rect', 'circ', 'ellip', 'tri', 'line', 'thickLine', 'arc',
      'PW', 'PH', 'buf',
      code
    );
    fn(
      pixelBuffer.px.bind(pixelBuffer),
      pixelBuffer.rect.bind(pixelBuffer),
      pixelBuffer.circ.bind(pixelBuffer),
      pixelBuffer.ellip.bind(pixelBuffer),
      pixelBuffer.tri.bind(pixelBuffer),
      pixelBuffer.line.bind(pixelBuffer),
      pixelBuffer.thickLine.bind(pixelBuffer),
      pixelBuffer.arc.bind(pixelBuffer),
      pixelBuffer.width,
      pixelBuffer.height,
      pixelBuffer.data
    );
  } catch (err) {
    console.error('[executeSpriteCode] Error executing sprite code:', err);
    console.error('[executeSpriteCode] Code was:', code);
  }
}

// ---------------------------------------------------------------------------
// executePaletteSprite — render a palette-indexed pixel grid with entity masks
// ---------------------------------------------------------------------------

function _normalizeGrid(val) {
  if (Array.isArray(val)) return val.join('');
  return (typeof val === 'string') ? val : '';
}

function executePaletteSprite(spriteData, pixelBuffer) {
  const { x, y, width, height, palette, sub_entities } = spriteData;
  const pixels = _normalizeGrid(spriteData.pixels);
  const mask = _normalizeGrid(spriteData.mask);
  const rootId = spriteData.rootId || '';
  if (!pixels || !palette) return;

  for (let row = 0; row < height; row++) {
    for (let col = 0; col < width; col++) {
      const idx = row * width + col;
      if (idx >= pixels.length) continue;
      const palIdx = parseInt(pixels[idx], 16);
      if (palIdx === 0) continue; // transparent
      const colorIdx = palIdx - 1; // palette is 1-indexed (0=transparent)
      if (colorIdx < 0 || colorIdx >= palette.length) continue;
      const color = palette[colorIdx];
      let entityId = rootId;
      if (mask && idx < mask.length) {
        const maskIdx = parseInt(mask[idx], 16);
        if (maskIdx > 0 && sub_entities && maskIdx - 1 < sub_entities.length) {
          entityId = sub_entities[maskIdx - 1];
        }
      }
      pixelBuffer._set(x + col, y + row, color[0], color[1], color[2], entityId);
    }
  }
}

// ---------------------------------------------------------------------------
// executeRawSprite — render a raw_sprite with direct RGB pixels + entity masks
// ---------------------------------------------------------------------------

function executeRawSprite(spriteData, pixelBuffer) {
  const { x, y, w, h, pixels, mask } = spriteData;
  if (!pixels || !w || !h) return;

  for (let row = 0; row < h; row++) {
    for (let col = 0; col < w; col++) {
      const idx = row * w + col;
      if (idx >= pixels.length) continue;
      const px = pixels[idx];
      if (px === null || px === undefined) continue; // transparent
      const entityId = (mask && idx < mask.length && mask[idx]) || '';
      pixelBuffer._set(x + col, y + row, px[0], px[1], px[2], entityId);
    }
  }
}

// ---------------------------------------------------------------------------
// executeImageBackground — render a base64 PNG directly into the pixel buffer
// ---------------------------------------------------------------------------

function executeImageBackground(spriteData, pixelBuffer) {
  // This is async because Image loading is async in browsers.
  // For synchronous rendering (e.g. thumbnails), we use a fallback.
  // The caller should await the returned Promise if possible.
  var b64 = spriteData.image_base64;
  var bx = spriteData.x || 0;
  var by = spriteData.y || 0;
  var bw = spriteData.width || pixelBuffer.width;
  var bh = spriteData.height || pixelBuffer.height;

  if (!b64) return Promise.resolve();

  return new Promise(function(resolve) {
    var img = new Image();
    img.onload = function() {
      // Draw to an offscreen canvas to read pixel data
      var offCanvas = document.createElement('canvas');
      offCanvas.width = bw;
      offCanvas.height = bh;
      var offCtx = offCanvas.getContext('2d');
      offCtx.imageSmoothingEnabled = false;
      offCtx.drawImage(img, 0, 0, bw, bh);
      var imgData = offCtx.getImageData(0, 0, bw, bh);
      var px = imgData.data;

      for (var row = 0; row < bh; row++) {
        for (var col = 0; col < bw; col++) {
          var srcIdx = (row * bw + col) * 4;
          pixelBuffer._set(
            bx + col, by + row,
            px[srcIdx], px[srcIdx + 1], px[srcIdx + 2],
            'bg'
          );
        }
      }
      resolve();
    };
    img.onerror = function() {
      console.warn('[executeImageBackground] Failed to load background image');
      resolve();
    };
    img.src = 'data:image/png;base64,' + b64;
  });
}

// ---------------------------------------------------------------------------
// renderSpriteEntry — detect format and dispatch to the right renderer
// ---------------------------------------------------------------------------

function renderSpriteEntry(eid, entry, pixelBuffer) {
  if (typeof entry === 'string') {
    executeSpriteCode(entry, pixelBuffer);
  } else if (entry && entry.format === 'raw_sprite') {
    executeRawSprite(entry, pixelBuffer);
  } else if (entry && entry.format === 'palette_grid') {
    entry.rootId = eid;
    executePaletteSprite(entry, pixelBuffer);
  } else if (entry && entry.format === 'image_background') {
    // Returns a Promise — caller should handle async if needed
    return executeImageBackground(entry, pixelBuffer);
  }
}

// ---------------------------------------------------------------------------
// Exports
// ---------------------------------------------------------------------------

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    PixelBuffer, Renderer, EntityRegistry,
    executeSpriteCode, executePaletteSprite, executeRawSprite,
    executeImageBackground, renderSpriteEntry,
    PW, PH
  };
}

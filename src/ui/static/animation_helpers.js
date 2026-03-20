// Tellimations Animation Templates & Particle System
// 20 animation factories (8 families: I, P, A, S, T, R, Q, D) + particle presets
// Used by the Tellimation module: Gemini selects template + params,
// client resolves instantly instead of compiling raw JS code.

'use strict';

// ═══════════════════════════════════════════════════════════════════
// Section 1: Shared Helpers
// ═══════════════════════════════════════════════════════════════════

function _collectEntityPixels(buf, PW, prefix) {
  var pixels = [];
  for (var i = 0; i < buf.length; i++) {
    if (buf[i].e === prefix || buf[i].e.startsWith(prefix + '.')) {
      pixels.push({
        i: i,
        x: i % PW,
        y: Math.floor(i / PW),
        r: buf[i]._r, g: buf[i]._g, b: buf[i]._b,
        e: buf[i].e
      });
    }
  }
  return pixels;
}

function _blankEntityPixels(buf, pixels) {
  for (var j = 0; j < pixels.length; j++) {
    var idx = pixels[j].i;
    buf[idx].r = buf[idx]._br;
    buf[idx].g = buf[idx]._bg;
    buf[idx].b = buf[idx]._bb;
  }
}

function _redrawEntityPixels(buf, PW, PH, pixels, dx, dy) {
  for (var j = 0; j < pixels.length; j++) {
    var p = pixels[j];
    var nx = p.x + dx, ny = p.y + dy;
    if (nx >= 0 && nx < PW && ny >= 0 && ny < PH) {
      var ni = ny * PW + nx;
      buf[ni].r = p.r;
      buf[ni].g = p.g;
      buf[ni].b = p.b;
    }
  }
}

function _computeEntityBounds(buf, PW, prefix) {
  var x1 = Infinity, y1 = Infinity, x2 = -1, y2 = -1;
  for (var i = 0; i < buf.length; i++) {
    if (buf[i].e === prefix || buf[i].e.startsWith(prefix + '.')) {
      var x = i % PW, y = Math.floor(i / PW);
      if (x < x1) x1 = x;
      if (x > x2) x2 = x;
      if (y < y1) y1 = y;
      if (y > y2) y2 = y;
    }
  }
  if (x2 < 0) return { x1: 0, y1: 0, x2: 0, y2: 0, cx: 0, cy: 0 };
  return {
    x1: x1, y1: y1, x2: x2, y2: y2,
    cx: Math.round((x1 + x2) / 2),
    cy: Math.round((y1 + y2) / 2)
  };
}

function _easeEnvelope(t, easeIn, easeOut) {
  if (t < easeIn) return t / easeIn;
  if (t > 1 - easeOut) return (1 - t) / easeOut;
  return 1;
}

function _clamp(v, lo, hi) { return v < lo ? lo : v > hi ? hi : v; }

function _setPixel(buf, PW, PH, x, y, r, g, b) {
  x = Math.round(x); y = Math.round(y);
  if (x >= 0 && x < PW && y >= 0 && y < PH) {
    var idx = y * PW + x;
    buf[idx].r = r; buf[idx].g = g; buf[idx].b = b;
  }
}

function _hueChannel(p, q, t) {
  if (t < 0) t += 1; if (t > 1) t -= 1;
  if (t < 1/6) return p + (q - p) * 6 * t;
  if (t < 1/2) return q;
  if (t < 2/3) return p + (q - p) * (2/3 - t) * 6;
  return p;
}
function _hslToRgb(h, s, l) {
  if (s === 0) { var v = Math.round(l * 255); return [v, v, v]; }
  var q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  var pp = 2 * l - q;
  return [Math.round(_hueChannel(pp, q, h + 1/3) * 255),
          Math.round(_hueChannel(pp, q, h) * 255),
          Math.round(_hueChannel(pp, q, h - 1/3) * 255)];
}
function _rgbToHue(r, g, b) {
  var max = Math.max(r, g, b), min = Math.min(r, g, b);
  if (max === min) return 0;
  var d = max - min, h;
  if (max === r) h = ((g - b) / d + 6) % 6;
  else if (max === g) h = (b - r) / d + 2;
  else h = (r - g) / d + 4;
  return h / 6;
}

/**
 * Compute the true contour gap between two entities along the axis connecting
 * their centers. Returns { gap, ndx, ndy, impactX, impactY }.
 *
 * Works by projecting all pixels into a rotated coordinate frame aligned with
 * the A→B axis, binning by perpendicular coordinate, and finding the minimum
 * gap across bins where both entities have pixels (true silhouette collision).
 *
 * @param {Array} pixelsA  - pixels of entity A ({x, y, ...})
 * @param {Array} pixelsB  - pixels of entity B
 * @param {Object} boundsA - bounds of A (needs .cx, .cy)
 * @param {Object} boundsB - bounds of B (needs .cx, .cy)
 * @returns {Object} { gap, ndx, ndy, impactX, impactY }
 */
function _computeContourGap(pixelsA, pixelsB, boundsA, boundsB) {
  var aCx = boundsA.cx, aCy = boundsA.cy;
  var bCx = boundsB.cx, bCy = boundsB.cy;
  var ddx = bCx - aCx, ddy = bCy - aCy;
  var centerDist = Math.sqrt(ddx * ddx + ddy * ddy);
  if (centerDist < 1) centerDist = 1;
  var ndx = ddx / centerDist, ndy = ddy / centerDist;
  // Perpendicular unit vector
  var pnx = -ndy, pny = ndx;

  // Project all pixels into rotated frame (origin = A center, along = A→B axis)
  // For A: bin by perp coord, keep max along-axis value (furthest toward B)
  var binsA = {};
  for (var j = 0; j < pixelsA.length; j++) {
    var rx = pixelsA[j].x - aCx, ry = pixelsA[j].y - aCy;
    var along = rx * ndx + ry * ndy;
    var perp = Math.round(rx * pnx + ry * pny);
    if (binsA[perp] === undefined || along > binsA[perp]) binsA[perp] = along;
  }
  // For B: bin by perp coord (same frame), keep min along-axis value (closest to A)
  var binsB = {}, binsB_px = {};
  for (var j = 0; j < pixelsB.length; j++) {
    var rx = pixelsB[j].x - aCx, ry = pixelsB[j].y - aCy;
    var along = rx * ndx + ry * ndy;
    var perp = Math.round(rx * pnx + ry * pny);
    if (binsB[perp] === undefined || along < binsB[perp]) {
      binsB[perp] = along;
      binsB_px[perp] = { x: pixelsB[j].x, y: pixelsB[j].y };
    }
  }

  // Find min gap across shared perpendicular bins
  var minGap = Infinity;
  var impactPerp = 0;
  for (var perp in binsA) {
    if (binsB[perp] !== undefined) {
      var g = binsB[perp] - binsA[perp];
      if (g < minGap) { minGap = g; impactPerp = perp; }
    }
  }

  // Fallback if no shared perpendicular bins (entities don't overlap in perp)
  if (minGap === Infinity) {
    var maxA = 0, minB = Infinity;
    for (var j = 0; j < pixelsA.length; j++) {
      var p = (pixelsA[j].x - aCx) * ndx + (pixelsA[j].y - aCy) * ndy;
      if (p > maxA) maxA = p;
    }
    for (var j = 0; j < pixelsB.length; j++) {
      var p = (pixelsB[j].x - aCx) * ndx + (pixelsB[j].y - aCy) * ndy;
      if (p < minB) minB = p;
    }
    minGap = minB - maxA;
  }

  var gap = Math.max(0, minGap);

  // Impact point: B's contour pixel at the closest perpendicular bin
  var impactX, impactY;
  if (binsB_px[impactPerp]) {
    impactX = binsB_px[impactPerp].x;
    impactY = binsB_px[impactPerp].y;
  } else {
    // Fallback: midpoint
    impactX = Math.round((aCx + bCx) / 2);
    impactY = Math.round((aCy + bCy) / 2);
  }

  return { gap: gap, ndx: ndx, ndy: ndy, impactX: impactX, impactY: impactY };
}

// ═══════════════════════════════════════════════════════════════════
// Section 1b: Bitmap Pixel Font & drawText
// ═══════════════════════════════════════════════════════════════════

var _FONT_W = 5, _FONT_H = 7, _FONT_SPACING = 1;

// 5×7 bitmap font — each glyph is 35 bits (row-major, top to bottom)
var _PIXEL_FONT = {
  'A': [0,1,1,1,0, 1,0,0,0,1, 1,0,0,0,1, 1,1,1,1,1, 1,0,0,0,1, 1,0,0,0,1, 1,0,0,0,1],
  'B': [1,1,1,1,0, 1,0,0,0,1, 1,0,0,0,1, 1,1,1,1,0, 1,0,0,0,1, 1,0,0,0,1, 1,1,1,1,0],
  'C': [0,1,1,1,0, 1,0,0,0,1, 1,0,0,0,0, 1,0,0,0,0, 1,0,0,0,0, 1,0,0,0,1, 0,1,1,1,0],
  'D': [1,1,1,1,0, 1,0,0,0,1, 1,0,0,0,1, 1,0,0,0,1, 1,0,0,0,1, 1,0,0,0,1, 1,1,1,1,0],
  'E': [1,1,1,1,1, 1,0,0,0,0, 1,0,0,0,0, 1,1,1,1,0, 1,0,0,0,0, 1,0,0,0,0, 1,1,1,1,1],
  'F': [1,1,1,1,1, 1,0,0,0,0, 1,0,0,0,0, 1,1,1,1,0, 1,0,0,0,0, 1,0,0,0,0, 1,0,0,0,0],
  'G': [0,1,1,1,0, 1,0,0,0,1, 1,0,0,0,0, 1,0,1,1,1, 1,0,0,0,1, 1,0,0,0,1, 0,1,1,1,0],
  'H': [1,0,0,0,1, 1,0,0,0,1, 1,0,0,0,1, 1,1,1,1,1, 1,0,0,0,1, 1,0,0,0,1, 1,0,0,0,1],
  'I': [1,1,1,1,1, 0,0,1,0,0, 0,0,1,0,0, 0,0,1,0,0, 0,0,1,0,0, 0,0,1,0,0, 1,1,1,1,1],
  'J': [0,0,1,1,1, 0,0,0,1,0, 0,0,0,1,0, 0,0,0,1,0, 0,0,0,1,0, 1,0,0,1,0, 0,1,1,0,0],
  'K': [1,0,0,0,1, 1,0,0,1,0, 1,0,1,0,0, 1,1,0,0,0, 1,0,1,0,0, 1,0,0,1,0, 1,0,0,0,1],
  'L': [1,0,0,0,0, 1,0,0,0,0, 1,0,0,0,0, 1,0,0,0,0, 1,0,0,0,0, 1,0,0,0,0, 1,1,1,1,1],
  'M': [1,0,0,0,1, 1,1,0,1,1, 1,0,1,0,1, 1,0,0,0,1, 1,0,0,0,1, 1,0,0,0,1, 1,0,0,0,1],
  'N': [1,0,0,0,1, 1,1,0,0,1, 1,0,1,0,1, 1,0,0,1,1, 1,0,0,0,1, 1,0,0,0,1, 1,0,0,0,1],
  'O': [0,1,1,1,0, 1,0,0,0,1, 1,0,0,0,1, 1,0,0,0,1, 1,0,0,0,1, 1,0,0,0,1, 0,1,1,1,0],
  'P': [1,1,1,1,0, 1,0,0,0,1, 1,0,0,0,1, 1,1,1,1,0, 1,0,0,0,0, 1,0,0,0,0, 1,0,0,0,0],
  'Q': [0,1,1,1,0, 1,0,0,0,1, 1,0,0,0,1, 1,0,0,0,1, 1,0,1,0,1, 1,0,0,1,0, 0,1,1,0,1],
  'R': [1,1,1,1,0, 1,0,0,0,1, 1,0,0,0,1, 1,1,1,1,0, 1,0,1,0,0, 1,0,0,1,0, 1,0,0,0,1],
  'S': [0,1,1,1,0, 1,0,0,0,1, 1,0,0,0,0, 0,1,1,1,0, 0,0,0,0,1, 1,0,0,0,1, 0,1,1,1,0],
  'T': [1,1,1,1,1, 0,0,1,0,0, 0,0,1,0,0, 0,0,1,0,0, 0,0,1,0,0, 0,0,1,0,0, 0,0,1,0,0],
  'U': [1,0,0,0,1, 1,0,0,0,1, 1,0,0,0,1, 1,0,0,0,1, 1,0,0,0,1, 1,0,0,0,1, 0,1,1,1,0],
  'V': [1,0,0,0,1, 1,0,0,0,1, 1,0,0,0,1, 1,0,0,0,1, 0,1,0,1,0, 0,1,0,1,0, 0,0,1,0,0],
  'W': [1,0,0,0,1, 1,0,0,0,1, 1,0,0,0,1, 1,0,0,0,1, 1,0,1,0,1, 1,1,0,1,1, 1,0,0,0,1],
  'X': [1,0,0,0,1, 1,0,0,0,1, 0,1,0,1,0, 0,0,1,0,0, 0,1,0,1,0, 1,0,0,0,1, 1,0,0,0,1],
  'Y': [1,0,0,0,1, 1,0,0,0,1, 0,1,0,1,0, 0,0,1,0,0, 0,0,1,0,0, 0,0,1,0,0, 0,0,1,0,0],
  'Z': [1,1,1,1,1, 0,0,0,0,1, 0,0,0,1,0, 0,0,1,0,0, 0,1,0,0,0, 1,0,0,0,0, 1,1,1,1,1],
  '0': [0,1,1,1,0, 1,0,0,0,1, 1,0,0,1,1, 1,0,1,0,1, 1,1,0,0,1, 1,0,0,0,1, 0,1,1,1,0],
  '1': [0,0,1,0,0, 0,1,1,0,0, 0,0,1,0,0, 0,0,1,0,0, 0,0,1,0,0, 0,0,1,0,0, 0,1,1,1,0],
  '2': [0,1,1,1,0, 1,0,0,0,1, 0,0,0,0,1, 0,0,0,1,0, 0,0,1,0,0, 0,1,0,0,0, 1,1,1,1,1],
  '3': [0,1,1,1,0, 1,0,0,0,1, 0,0,0,0,1, 0,0,1,1,0, 0,0,0,0,1, 1,0,0,0,1, 0,1,1,1,0],
  '4': [1,0,0,0,1, 1,0,0,0,1, 1,0,0,0,1, 1,1,1,1,1, 0,0,0,0,1, 0,0,0,0,1, 0,0,0,0,1],
  '5': [1,1,1,1,1, 1,0,0,0,0, 1,0,0,0,0, 1,1,1,1,0, 0,0,0,0,1, 1,0,0,0,1, 0,1,1,1,0],
  '6': [0,1,1,1,0, 1,0,0,0,0, 1,0,0,0,0, 1,1,1,1,0, 1,0,0,0,1, 1,0,0,0,1, 0,1,1,1,0],
  '7': [1,1,1,1,1, 0,0,0,0,1, 0,0,0,1,0, 0,0,1,0,0, 0,0,1,0,0, 0,0,1,0,0, 0,0,1,0,0],
  '8': [0,1,1,1,0, 1,0,0,0,1, 1,0,0,0,1, 0,1,1,1,0, 1,0,0,0,1, 1,0,0,0,1, 0,1,1,1,0],
  '9': [0,1,1,1,0, 1,0,0,0,1, 1,0,0,0,1, 0,1,1,1,1, 0,0,0,0,1, 0,0,0,0,1, 0,1,1,1,0],
  ' ': [0,0,0,0,0, 0,0,0,0,0, 0,0,0,0,0, 0,0,0,0,0, 0,0,0,0,0, 0,0,0,0,0, 0,0,0,0,0],
  '.': [0,0,0,0,0, 0,0,0,0,0, 0,0,0,0,0, 0,0,0,0,0, 0,0,0,0,0, 0,0,0,0,0, 0,0,1,0,0],
  ',': [0,0,0,0,0, 0,0,0,0,0, 0,0,0,0,0, 0,0,0,0,0, 0,0,0,0,0, 0,0,1,0,0, 0,1,0,0,0],
  '!': [0,0,1,0,0, 0,0,1,0,0, 0,0,1,0,0, 0,0,1,0,0, 0,0,1,0,0, 0,0,0,0,0, 0,0,1,0,0],
  '?': [0,1,1,1,0, 1,0,0,0,1, 0,0,0,0,1, 0,0,0,1,0, 0,0,1,0,0, 0,0,0,0,0, 0,0,1,0,0],
  '-': [0,0,0,0,0, 0,0,0,0,0, 0,0,0,0,0, 1,1,1,1,1, 0,0,0,0,0, 0,0,0,0,0, 0,0,0,0,0],
  "'": [0,0,1,0,0, 0,0,1,0,0, 0,0,0,0,0, 0,0,0,0,0, 0,0,0,0,0, 0,0,0,0,0, 0,0,0,0,0],
};

/**
 * Render pixel-art text into the buffer.
 * @param {Array} buf   - pixel buffer (flat array of {r,g,b,e,...})
 * @param {number} PW   - buffer width
 * @param {number} PH   - buffer height
 * @param {string} text - string to render
 * @param {number} x    - top-left x position
 * @param {number} y    - top-left y position
 * @param {number} r    - red 0-255
 * @param {number} g    - green 0-255
 * @param {number} b    - blue 0-255
 * @param {string} entityId - entity ID assigned to every text pixel
 * @param {number} [scale=1] - pixel scale (each font pixel → scale×scale block)
 */
function drawText(buf, PW, PH, text, x, y, r, g, b, entityId, scale) {
  scale = scale || 1;
  var cx = x;
  var upper = text.toUpperCase();
  for (var ci = 0; ci < upper.length; ci++) {
    var ch = upper[ci];
    var glyph = _PIXEL_FONT[ch];
    if (!glyph) {
      // Unknown character — skip with space width
      cx += (_FONT_W + _FONT_SPACING) * scale;
      continue;
    }
    for (var gy = 0; gy < _FONT_H; gy++) {
      for (var gx = 0; gx < _FONT_W; gx++) {
        if (glyph[gy * _FONT_W + gx]) {
          // Draw scale×scale block
          for (var sy = 0; sy < scale; sy++) {
            for (var sx = 0; sx < scale; sx++) {
              var px = cx + gx * scale + sx;
              var py = y + gy * scale + sy;
              if (px >= 0 && px < PW && py >= 0 && py < PH) {
                var idx = py * PW + px;
                buf[idx].r = r;
                buf[idx].g = g;
                buf[idx].b = b;
                buf[idx].e = entityId;
              }
            }
          }
        }
      }
    }
    cx += (_FONT_W + _FONT_SPACING) * scale;
  }
}

// ═══════════════════════════════════════════════════════════════════
// Section 2: Particle System
// ═══════════════════════════════════════════════════════════════════

function ParticleSystem(cfg) {
  this.particles = [];
  this.maxParticles = cfg.maxParticles || 50;
  // Base config (cloned per particle with jitter)
  this._cfg = {
    color: cfg.color || [255, 255, 255],
    colorEnd: cfg.colorEnd || null,
    size: cfg.size || 1,
    maxAge: cfg.maxAge || 0.3,         // in normalized-t units
    gravity: cfg.gravity || 0,          // px per t-unit squared
    drag: cfg.drag || 0,
    spreadX: cfg.spreadX || 3,
    spreadY: cfg.spreadY || 3,
    vx: cfg.vx || 0,
    vy: cfg.vy || 0,
    vxJitter: cfg.vxJitter || 0,
    vyJitter: cfg.vyJitter || 0,
    fadeIn: cfg.fadeIn || 0.1,
    fadeOut: cfg.fadeOut || 0.3,
    flicker: cfg.flicker || false,
  };
}

ParticleSystem.prototype.burst = function(cx, cy, count) {
  for (var i = 0; i < count && this.particles.length < this.maxParticles; i++) {
    this._spawn(cx, cy);
  }
};

ParticleSystem.prototype.spawn = function(cx, cy) {
  if (this.particles.length < this.maxParticles) {
    this._spawn(cx, cy);
  }
};

ParticleSystem.prototype._spawn = function(cx, cy) {
  var c = this._cfg;
  var jx = (Math.random() - 0.5) * 2 * c.spreadX;
  var jy = (Math.random() - 0.5) * 2 * c.spreadY;
  this.particles.push({
    x: cx + jx,
    y: cy + jy,
    vx: c.vx + (Math.random() - 0.5) * 2 * c.vxJitter,
    vy: c.vy + (Math.random() - 0.5) * 2 * c.vyJitter,
    age: 0,
    maxAge: c.maxAge * (0.8 + Math.random() * 0.4),
    alive: true,
  });
};

ParticleSystem.prototype.update = function(dt) {
  var c = this._cfg;
  for (var i = this.particles.length - 1; i >= 0; i--) {
    var p = this.particles[i];
    p.age += dt;
    if (p.age >= p.maxAge) {
      this.particles.splice(i, 1);
      continue;
    }
    p.vy += c.gravity * dt;
    p.vx *= (1 - c.drag * dt);
    p.vy *= (1 - c.drag * dt);
    p.x += p.vx * dt;
    p.y += p.vy * dt;
  }
};

ParticleSystem.prototype.draw = function(buf, PW, PH) {
  var c = this._cfg;
  for (var i = 0; i < this.particles.length; i++) {
    var p = this.particles[i];
    if (!p.alive) continue;

    // Flicker: randomly skip ~30% of frames
    if (c.flicker && Math.random() < 0.3) continue;

    var lifeRatio = p.age / p.maxAge;

    // Alpha envelope
    var alpha = 1;
    if (lifeRatio < c.fadeIn) alpha = lifeRatio / c.fadeIn;
    else if (lifeRatio > 1 - c.fadeOut) alpha = (1 - lifeRatio) / c.fadeOut;

    // Color interpolation
    var r, g, b;
    if (c.colorEnd) {
      r = Math.round(c.color[0] + (c.colorEnd[0] - c.color[0]) * lifeRatio);
      g = Math.round(c.color[1] + (c.colorEnd[1] - c.color[1]) * lifeRatio);
      b = Math.round(c.color[2] + (c.colorEnd[2] - c.color[2]) * lifeRatio);
    } else {
      r = c.color[0]; g = c.color[1]; b = c.color[2];
    }

    var px = Math.round(p.x), py = Math.round(p.y);
    var sz = c.size;

    for (var sy = 0; sy < sz; sy++) {
      for (var sx = 0; sx < sz; sx++) {
        var fx = px + sx, fy = py + sy;
        if (fx >= 0 && fx < PW && fy >= 0 && fy < PH) {
          var idx = fy * PW + fx;
          // Blend with alpha
          buf[idx].r = Math.round(buf[idx].r * (1 - alpha) + r * alpha);
          buf[idx].g = Math.round(buf[idx].g * (1 - alpha) + g * alpha);
          buf[idx].b = Math.round(buf[idx].b * (1 - alpha) + b * alpha);
        }
      }
    }
  }
};

// ── Particle Presets ──

var ParticlePresets = {
  stars: {
    color: [255, 255, 100], size: 1,
    maxAge: 0.4, gravity: 0, drag: 0.5,
    spreadX: 2, spreadY: 2,
    vx: 0, vy: 0, vxJitter: 40, vyJitter: 40,
    fadeIn: 0.05, fadeOut: 0.4, flicker: true,
  },
  rain: {
    color: [100, 150, 220], size: 1,
    maxAge: 0.3, gravity: 200, drag: 0,
    spreadX: 30, spreadY: 5,
    vx: 0, vy: 50, vxJitter: 5, vyJitter: 10,
    fadeIn: 0.05, fadeOut: 0.2, flicker: false,
  },
  smoke: {
    color: [160, 160, 160], size: 2,
    maxAge: 0.5, gravity: -15, drag: 1,
    spreadX: 5, spreadY: 2,
    vx: 0, vy: -20, vxJitter: 10, vyJitter: 5,
    fadeIn: 0.1, fadeOut: 0.5, flicker: false,
  },
  fire: {
    color: [255, 140, 0], colorEnd: [255, 50, 0], size: 1,
    maxAge: 0.35, gravity: -30, drag: 0.5,
    spreadX: 4, spreadY: 2,
    vx: 0, vy: -25, vxJitter: 15, vyJitter: 8,
    fadeIn: 0.05, fadeOut: 0.3, flicker: true,
  },
  explosion: {
    color: [255, 200, 50], colorEnd: [200, 80, 0], size: 2,
    maxAge: 0.3, gravity: 20, drag: 2,
    spreadX: 2, spreadY: 2,
    vx: 0, vy: 0, vxJitter: 80, vyJitter: 80,
    fadeIn: 0.02, fadeOut: 0.4, flicker: false,
  },
  snowflakes: {
    color: [230, 240, 255], size: 1,
    maxAge: 0.6, gravity: 10, drag: 0.5,
    spreadX: 30, spreadY: 5,
    vx: 0, vy: 8, vxJitter: 12, vyJitter: 3,
    fadeIn: 0.1, fadeOut: 0.3, flicker: false,
  },
  hearts: {
    color: [255, 80, 100], size: 2,
    maxAge: 0.5, gravity: -12, drag: 0.3,
    spreadX: 5, spreadY: 3,
    vx: 0, vy: -15, vxJitter: 8, vyJitter: 4,
    fadeIn: 0.1, fadeOut: 0.4, flicker: false,
  },
  // Utility presets used by templates
  steam: {
    color: [220, 220, 220], size: 1,
    maxAge: 0.4, gravity: -20, drag: 0.8,
    spreadX: 4, spreadY: 2,
    vx: 0, vy: -18, vxJitter: 8, vyJitter: 4,
    fadeIn: 0.1, fadeOut: 0.4, flicker: false,
  },
  frost: {
    color: [200, 230, 255], size: 1,
    maxAge: 0.5, gravity: -5, drag: 1,
    spreadX: 3, spreadY: 3,
    vx: 0, vy: -5, vxJitter: 6, vyJitter: 6,
    fadeIn: 0.1, fadeOut: 0.5, flicker: true,
  },
  sparkle: {
    color: [255, 255, 200], size: 1,
    maxAge: 0.3, gravity: 0, drag: 1,
    spreadX: 5, spreadY: 5,
    vx: 0, vy: 0, vxJitter: 10, vyJitter: 10,
    fadeIn: 0.05, fadeOut: 0.3, flicker: true,
  },
  dust: {
    color: [180, 170, 140], size: 1,
    maxAge: 0.5, gravity: 8, drag: 1.5,
    spreadX: 5, spreadY: 3,
    vx: 0, vy: 5, vxJitter: 8, vyJitter: 5,
    fadeIn: 0.1, fadeOut: 0.4, flicker: false,
  },
  anger: {
    color: [220, 40, 40], size: 2,
    maxAge: 0.5, gravity: 0, drag: 0,
    spreadX: 5, spreadY: 5,
    vx: 0, vy: 0, vxJitter: 1, vyJitter: 1,
    fadeIn: 0.1, fadeOut: 0.3, flicker: false,
  },
  fear: {
    color: [100, 160, 230], size: 1,
    maxAge: 0.6, gravity: 15, drag: 0.5,
    spreadX: 5, spreadY: 3,
    vx: 0, vy: 15, vxJitter: 3, vyJitter: 3,
    fadeIn: 0.1, fadeOut: 0.3, flicker: false,
  },
};

// ═══════════════════════════════════════════════════════════════════
// Section 3: Animation Templates Registry
// ═══════════════════════════════════════════════════════════════════

var AnimationTemplates = {
  registry: {},

  register: function(name, factory, defaultDuration) {
    this.registry[name] = { factory: factory, duration: defaultDuration };
  },

  /**
   * Build an animate function from a template spec.
   * @param {Object} spec - { template, params, particles, duration_ms }
   * @returns {{ animate: Function, duration_ms: number }}
   */
  build: function(spec) {
    var entry = this.registry[spec.template];
    if (!entry) {
      console.warn('[AnimationTemplates] Unknown template: ' + spec.template + ', using spotlight fallback');
      entry = this.registry['spotlight'] || this.registry['color_pop'];
      if (!entry) {
        return {
          animate: function() {},
          duration_ms: spec.duration_ms || 1200
        };
      }
    }

    var params = spec.params || {};
    var mainAnimate = entry.factory(params);
    var duration = spec.duration_ms || entry.duration;

    // Build particle systems from spec.particles array
    var particleSystems = [];
    var particleSpecs = spec.particles || [];
    for (var i = 0; i < particleSpecs.length; i++) {
      var ps = particleSpecs[i];
      var preset = ParticlePresets[ps.type];
      if (preset) {
        var cfg = {};
        for (var k in preset) cfg[k] = preset[k];
        if (ps.color) cfg.color = ps.color;
        if (ps.count) cfg._burstCount = ps.count;
        if (ps.anchor) cfg._anchor = ps.anchor;
        particleSystems.push({ system: new ParticleSystem(cfg), cfg: cfg, spawned: false });
      }
    }

    // Combine main animation with particle systems
    if (particleSystems.length === 0) {
      return { animate: mainAnimate, duration_ms: duration };
    }

    return {
      animate: function(buf, PW, PH, t) {
        mainAnimate(buf, PW, PH, t);

        var dt = 1 / 60; // approximate frame dt in normalized time
        for (var i = 0; i < particleSystems.length; i++) {
          var ps = particleSystems[i];
          if (!ps.spawned) {
            var anchor = ps.cfg._anchor;
            var bounds = anchor ? _computeEntityBounds(buf, PW, anchor) : { cx: PW / 2, cy: PH / 2 };
            ps.system.burst(bounds.cx, bounds.cy, ps.cfg._burstCount || 8);
            ps.spawned = true;
          }
          ps.system.update(dt);
          ps.system.draw(buf, PW, PH);
        }
      },
      duration_ms: duration
    };
  }
};

// New shared helpers for deduplication

function _blendPixel(buf, idx, r, g, b, alpha) {
  buf[idx].r = Math.min(255, Math.round(buf[idx].r * (1 - alpha) + r * alpha));
  buf[idx].g = Math.min(255, Math.round(buf[idx].g * (1 - alpha) + g * alpha));
  buf[idx].b = Math.min(255, Math.round(buf[idx].b * (1 - alpha) + b * alpha));
}

function _isEntity(entityId, prefix) {
  return entityId === prefix || entityId.startsWith(prefix + '.');
}

function _drawStarBurst(buf, PW, PH, cx, cy, alpha, cardLen, diagLen) {
  // Thickness: scale with spike length for HD visibility
  var thick = Math.max(1, Math.round(cardLen / 6));
  // Core filled circle
  var coreR = Math.max(2, thick + 1);
  for (var dy = -coreR; dy <= coreR; dy++) {
    for (var dx = -coreR; dx <= coreR; dx++) {
      if (dx * dx + dy * dy > coreR * coreR) continue;
      var sx = cx + dx, sy = cy + dy;
      if (sx >= 0 && sx < PW && sy >= 0 && sy < PH)
        _blendPixel(buf, sy * PW + sx, 255, 255, 180, alpha);
    }
  }
  // Cardinal spikes (thick)
  var cards = [[1,0],[-1,0],[0,1],[0,-1]];
  for (var c = 0; c < 4; c++)
    for (var d = 2; d <= cardLen; d++) {
      var sa = alpha * (1 - (d - 1) / cardLen);
      var perpX = cards[c][1], perpY = cards[c][0];
      for (var w = -thick; w <= thick; w++) {
        var sx = cx + cards[c][0] * d + perpX * w;
        var sy = cy + cards[c][1] * d + perpY * w;
        if (sx >= 0 && sx < PW && sy >= 0 && sy < PH)
          _blendPixel(buf, sy * PW + sx, 255, 255, 120, sa);
      }
    }
  // Diagonal spikes (thick)
  var diags = [[1,1],[-1,1],[1,-1],[-1,-1]];
  for (var c = 0; c < 4; c++)
    for (var d = 2; d <= diagLen; d++) {
      var sa = alpha * (1 - (d - 1) / diagLen);
      for (var wy = -thick; wy <= thick; wy++) {
        for (var wx = -thick; wx <= thick; wx++) {
          if (wx * wx + wy * wy > thick * thick + 1) continue;
          var sx = cx + diags[c][0] * d + wx;
          var sy = cy + diags[c][1] * d + wy;
          if (sx >= 0 && sx < PW && sy >= 0 && sy < PH)
            _blendPixel(buf, sy * PW + sx, 255, 230, 80, sa);
        }
      }
    }
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { ParticleSystem, ParticlePresets };
}

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

// ═══════════════════════════════════════════════════════════════════
// Section 4: Animation Template Factories (8 families, 20 animations)
//   I=Identity, P=Property, A=Action, S=Space,
//   T=Time, R=Relation, Q=Quantity, D=Discourse
// ═══════════════════════════════════════════════════════════════════

// ── I1: Spotlight ──
// Scene darkens, target entity pulses gently with luminous halo that
// follows the entity's actual silhouette (not its bounding box).
// Uses pre-computed distance field from PixelBuffer.computeDistanceFields().
AnimationTemplates.register('spotlight', function(params) {
  var prefix = params.entityPrefix || '';
  var dimStrength = params.dimStrength != null ? params.dimStrength : 0.7;
  var glowStrength = params.glowStrength != null ? params.glowStrength : 0.35;
  var haloColor = params.haloColor || [255, 240, 180]; // warm yellow
  var maxHaloSize = params.maxHaloSize || 14;

  return function animate(buf, PW, PH, t) {
    var env = _easeEnvelope(t, 0.15, 0.15);
    // Gentle pulse: slow sine wave (3 full cycles over 3s)
    var pulse = 0.6 + 0.4 * Math.sin(t * Math.PI * 6);
    var glow = 1 + glowStrength * env * pulse;
    var dim = 1 - dimStrength * env;

    // Get pre-computed distance field for this entity
    var df = buf._distFields && buf._distFields[prefix];
    var haloSize = Math.round(5 + (maxHaloSize - 5) * env * pulse);
    var hr = haloColor[0], hg = haloColor[1], hb = haloColor[2];
    var haloAlphaMax = 0.7 * env * pulse;
    var prefixDot = prefix + '.';

    // Single pass: dim non-target, brighten target, draw silhouette halo
    for (var i = 0; i < buf.length; i++) {
      var p = buf[i];
      var isTarget = (p.e === prefix || p.e.startsWith(prefixDot));

      if (isTarget) {
        // Brighten target entity
        p.r = Math.min(255, Math.round(p._r * glow));
        p.g = Math.min(255, Math.round(p._g * glow));
        p.b = Math.min(255, Math.round(p._b * glow));
      } else if (df && df[i] > 0 && df[i] <= haloSize) {
        // Pixel is near entity contour — draw halo with smooth cubic falloff
        var falloff = 1 - df[i] / haloSize;
        var a = haloAlphaMax * falloff * falloff * falloff;
        p.r = Math.min(255, Math.round(p._r * dim * (1 - a) + hr * a));
        p.g = Math.min(255, Math.round(p._g * dim * (1 - a) + hg * a));
        p.b = Math.min(255, Math.round(p._b * dim * (1 - a) + hb * a));
      } else if (p.e && p.e !== '') {
        // Dim everything else
        p.r = Math.round(p._r * dim);
        p.g = Math.round(p._g * dim);
        p.b = Math.round(p._b * dim);
      }
    }
  };
}, 3000);

// ── I2: Nametag ──
// Large beige nametag with entity type text, connected by an undulating
// red string. The tag pivots slightly at the string attachment point.
AnimationTemplates.register('nametag', function(params) {
  var prefix = params.entityPrefix || '';
  var bgColor = params.bgColor || [235, 215, 180]; // beige
  var borderColor = params.borderColor || [180, 155, 120]; // darker beige
  var textColor = params.textColor || [80, 50, 30]; // dark brown
  var stringColor = params.stringColor || [200, 50, 40]; // red

  // Extract entity type from prefix: "cat_01" → "CAT", "big_tree_01" → "BIG TREE"
  var entityType = prefix.replace(/_\d+$/, '').replace(/_/g, ' ').toUpperCase();
  // Pre-compute text width: each char is (_FONT_W + _FONT_SPACING) * scale, minus trailing space
  var textScale = 1;
  var charW = (_FONT_W + _FONT_SPACING) * textScale;
  var textW = entityType.length * charW - _FONT_SPACING;
  var textH = _FONT_H * textScale;

  var labelPadX = 6, labelPadY = 5;
  var labelW = Math.max(40, textW + labelPadX * 2);
  var labelH = textH + labelPadY * 2;

  return function animate(buf, PW, PH, t) {
    var env = _easeEnvelope(t, 0.15, 0.15);
    if (env < 0.01) return;

    var bounds = _computeEntityBounds(buf, PW, prefix);
    if (bounds.x2 < 0) return;

    // Pivot angle: gentle oscillation
    var pivotAngle = Math.sin(t * Math.PI * 3) * 0.08 * env;
    var cosA = Math.cos(pivotAngle), sinA = Math.sin(pivotAngle);

    // Label position: centered above entity, 22px gap
    var anchorX = bounds.cx;  // string attachment = center bottom of label
    var anchorY = Math.max(labelH + 4, bounds.y1 - 22);
    var alpha = env * 0.9;

    // String endpoint: top-center of entity
    var stringEndX = bounds.cx;
    var stringEndY = bounds.y1;

    // Draw the undulating red string from anchor to entity top
    var stringLen = stringEndY - anchorY;
    if (stringLen > 2) {
      for (var sy = 0; sy <= stringLen; sy++) {
        var progress = sy / stringLen;
        // Sine wave offset that animates over time
        var waveAmp = 3 * Math.sin(progress * Math.PI) * env;
        var waveX = waveAmp * Math.sin(progress * Math.PI * 2.5 + t * Math.PI * 4);
        var px = Math.round(anchorX + waveX);
        var py = anchorY + sy;
        if (px >= 0 && px < PW && py >= 0 && py < PH) {
          var si = py * PW + px;
          buf[si].r = Math.round(buf[si].r * (1 - alpha) + stringColor[0] * alpha);
          buf[si].g = Math.round(buf[si].g * (1 - alpha) + stringColor[1] * alpha);
          buf[si].b = Math.round(buf[si].b * (1 - alpha) + stringColor[2] * alpha);
        }
      }
    }

    // Draw label with pivot rotation around anchor point (center-bottom of label)
    for (var ly = 0; ly < labelH; ly++) {
      for (var lx = 0; lx < labelW; lx++) {
        // Position relative to anchor (center-bottom of label)
        var relX = lx - labelW / 2;
        var relY = ly - labelH; // negative because label is above anchor
        // Apply rotation
        var rotX = relX * cosA - relY * sinA;
        var rotY = relX * sinA + relY * cosA;
        var drawX = Math.round(anchorX + rotX);
        var drawY = Math.round(anchorY + rotY);
        if (drawX < 0 || drawX >= PW || drawY < 0 || drawY >= PH) continue;

        var di = drawY * PW + drawX;
        var isBorder = (ly === 0 || ly === labelH - 1 || lx === 0 || lx === labelW - 1);
        // Small hole for string at top-center
        var isHole = (ly === labelH - 1 && lx >= labelW / 2 - 1 && lx <= labelW / 2 + 1);
        if (isHole) continue;

        var cr, cg, cb;
        if (isBorder) {
          cr = borderColor[0]; cg = borderColor[1]; cb = borderColor[2];
        } else {
          cr = bgColor[0]; cg = bgColor[1]; cb = bgColor[2];
        }
        buf[di].r = Math.round(buf[di].r * (1 - alpha) + cr * alpha);
        buf[di].g = Math.round(buf[di].g * (1 - alpha) + cg * alpha);
        buf[di].b = Math.round(buf[di].b * (1 - alpha) + cb * alpha);
      }
    }

    // Draw text inside the label (with same rotation)
    var textStartX = -textW / 2;
    var textStartY = -labelH + labelPadY;
    var upper = entityType;
    var cx = textStartX;
    for (var ci = 0; ci < upper.length; ci++) {
      var ch = upper[ci];
      var glyph = _PIXEL_FONT[ch];
      if (!glyph) { cx += charW; continue; }
      for (var gy = 0; gy < _FONT_H; gy++) {
        for (var gx = 0; gx < _FONT_W; gx++) {
          if (!glyph[gy * _FONT_W + gx]) continue;
          for (var sy2 = 0; sy2 < textScale; sy2++) {
            for (var sx2 = 0; sx2 < textScale; sx2++) {
              var relX2 = cx + gx * textScale + sx2;
              var relY2 = textStartY + gy * textScale + sy2;
              var rotX2 = relX2 * cosA - relY2 * sinA;
              var rotY2 = relX2 * sinA + relY2 * cosA;
              var drawX2 = Math.round(anchorX + rotX2);
              var drawY2 = Math.round(anchorY + rotY2);
              if (drawX2 >= 0 && drawX2 < PW && drawY2 >= 0 && drawY2 < PH) {
                var ti = drawY2 * PW + drawX2;
                buf[ti].r = Math.round(buf[ti].r * (1 - alpha) + textColor[0] * alpha);
                buf[ti].g = Math.round(buf[ti].g * (1 - alpha) + textColor[1] * alpha);
                buf[ti].b = Math.round(buf[ti].b * (1 - alpha) + textColor[2] * alpha);
              }
            }
          }
        }
      }
      cx += charW;
    }
  };
}, 3000);

// ── S2: Settle ──
AnimationTemplates.register('settle', function(params) {
  var prefix = params.entityPrefix || '';
  var dropPx = _clamp(params.dropPixels || 8, 1, 20);
  var bounces = _clamp(params.bounceCount || 3, 1, 6);

  return function animate(buf, PW, PH, t) {
    var offset = Math.round(dropPx * Math.sin(t * Math.PI * bounces) * (1 - t));
    if (offset === 0 && t > 0.05) return;

    var pixels = _collectEntityPixels(buf, PW, prefix);
    _blankEntityPixels(buf, pixels);
    _redrawEntityPixels(buf, PW, PH, pixels, 0, offset);

    // Shadow grows as entity settles
    if (pixels.length > 0) {
      var bounds = _computeEntityBounds(buf, PW, prefix);
      var shadowY = bounds.y2 + offset + 1;
      var shadowWidth = Math.round((bounds.x2 - bounds.x1) * 0.6);
      var shadowX = bounds.cx - Math.round(shadowWidth / 2);
      var shadowAlpha = 0.2 * (1 - Math.abs(offset) / dropPx);
      for (var sx = shadowX; sx < shadowX + shadowWidth; sx++) {
        if (sx >= 0 && sx < PW && shadowY >= 0 && shadowY < PH) {
          var si = shadowY * PW + sx;
          buf[si].r = Math.round(buf[si].r * (1 - shadowAlpha));
          buf[si].g = Math.round(buf[si].g * (1 - shadowAlpha));
          buf[si].b = Math.round(buf[si].b * (1 - shadowAlpha));
        }
      }
    }
  };
}, 1200);

// ── P1: Color Pop ──
// Each pixel of the entity transitions to its RGB complement (color wheel
// opposite): blue→yellow, white→black, green→magenta. Two full round-trips
// in 3 seconds. Non-target entities are desaturated.
AnimationTemplates.register('color_pop', function(params) {
  var prefix = params.entityPrefix || '';
  var desatStr = params.desaturationStrength != null ? params.desaturationStrength : 0.8;
  var prefixDot = prefix + '.';

  return function animate(buf, PW, PH, t) {
    var env = _easeEnvelope(t, 0.12, 0.12);
    // Two round-trips: cos(t * PI * 4) does 2 full cycles in t=[0,1]
    // phase goes 0→1→0→1→0 (original → complement → original → complement → original)
    var phase = (0.5 - 0.5 * Math.cos(t * Math.PI * 4)) * env;

    for (var i = 0; i < buf.length; i++) {
      var p = buf[i];
      if (p.e === prefix || p.e.startsWith(prefixDot)) {
        // Transition each channel toward its complement: complement = 255 - original
        p.r = Math.round(p._r + (255 - 2 * p._r) * phase);
        p.g = Math.round(p._g + (255 - 2 * p._g) * phase);
        p.b = Math.round(p._b + (255 - 2 * p._b) * phase);
      } else if (p.e && p.e !== '' && !p.e.startsWith('bg.')) {
        var L = Math.round(p._r * 0.299 + p._g * 0.587 + p._b * 0.114);
        var mix = desatStr * env;
        p.r = Math.round(p._r * (1 - mix) + L * mix);
        p.g = Math.round(p._g * (1 - mix) + L * mix);
        p.b = Math.round(p._b * (1 - mix) + L * mix);
      }
    }
  };
}, 3000);

// ── S1: Reveal ──
// Occluding layer becomes semi-transparent to show hidden elements.
AnimationTemplates.register('reveal', function(params) {
  var prefix = params.entityPrefix || '';
  var revealAlpha = params.revealAlpha != null ? params.revealAlpha : 0.35;

  return function animate(buf, PW, PH, t) {
    var env = _easeEnvelope(t, 0.25, 0.25);
    var alpha = revealAlpha * env;

    // Make occluding entity semi-transparent to peek at what's behind
    for (var i = 0; i < buf.length; i++) {
      if (buf[i].e === prefix || buf[i].e.startsWith(prefix + '.')) {
        buf[i].r = Math.round(buf[i]._r * (1 - alpha) + buf[i]._br * alpha);
        buf[i].g = Math.round(buf[i]._g * (1 - alpha) + buf[i]._bg * alpha);
        buf[i].b = Math.round(buf[i]._b * (1 - alpha) + buf[i]._bb * alpha);
      }
    }

    // Gentle pulsing outline effect
    if (env > 0.1) {
      var bounds = _computeEntityBounds(buf, PW, prefix);
      var outlineAlpha = 0.3 * env * (0.5 + 0.5 * Math.sin(t * Math.PI * 4));
      var olc = [200, 220, 255]; // light blue outline
      for (var y = bounds.y1; y <= bounds.y2; y++) {
        for (var x = bounds.x1; x <= bounds.x2; x++) {
          if (x < 0 || x >= PW || y < 0 || y >= PH) continue;
          var idx = y * PW + x;
          var isEntity = buf[idx].e === prefix || buf[idx].e.startsWith(prefix + '.');
          if (!isEntity) continue;
          // Check if border pixel (has a non-entity neighbor)
          var isBorder = false;
          var neighbors = [[-1,0],[1,0],[0,-1],[0,1]];
          for (var n = 0; n < 4; n++) {
            var nx = x + neighbors[n][0], ny = y + neighbors[n][1];
            if (nx < 0 || nx >= PW || ny < 0 || ny >= PH) { isBorder = true; break; }
            var ne = buf[ny * PW + nx].e;
            if (ne !== prefix && !ne.startsWith(prefix + '.')) { isBorder = true; break; }
          }
          if (isBorder) {
            buf[idx].r = Math.min(255, Math.round(buf[idx].r * (1 - outlineAlpha) + olc[0] * outlineAlpha));
            buf[idx].g = Math.min(255, Math.round(buf[idx].g * (1 - outlineAlpha) + olc[1] * outlineAlpha));
            buf[idx].b = Math.min(255, Math.round(buf[idx].b * (1 - outlineAlpha) + olc[2] * outlineAlpha));
          }
        }
      }
    }
  };
}, 1500);

// ── P2: Emanation ──
// Custom multi-pixel sprites (icicles, vapor, stars, dust clouds, hearts)
// emanate from the entity contour in waves. Much more visible and evocative
// than single-pixel particle dots.

// Sprite drawing function for emanation types
function _drawEmanationSprite(buf, PW, PH, type, cx, cy, size, alpha) {
  if (alpha < 0.05) return;
  var _blend = function(idx, r, g, b, a) {
    if (idx < 0 || idx >= buf.length) return;
    buf[idx].r = Math.round(buf[idx].r * (1 - a) + r * a);
    buf[idx].g = Math.round(buf[idx].g * (1 - a) + g * a);
    buf[idx].b = Math.round(buf[idx].b * (1 - a) + b * a);
  };
  var px, py, idx;

  if (type === 'frost') {
    // Icicle: tall narrow triangle pointing down, 4px wide × 12px tall at size=1
    var iW = Math.round(4 * size), iH = Math.round(12 * size);
    var half = Math.floor(iW / 2);
    for (var dy = 0; dy < iH; dy++) {
      // Width narrows from full at top to 1 at bottom
      var rowHalf = Math.max(0, Math.round(half * (1 - dy / iH)));
      for (var dx = -rowHalf; dx <= rowHalf; dx++) {
        px = Math.round(cx + dx); py = Math.round(cy + dy);
        if (px >= 0 && px < PW && py >= 0 && py < PH) {
          idx = py * PW + px;
          var bright = 1 - dy / iH * 0.3;  // lighter at tip
          // Highlight pixel for reflet
          var isReflet = (dx === -rowHalf + 1 && dy > 1 && dy < iH - 2);
          if (isReflet) {
            _blend(idx, 255, 255, 255, alpha * 0.8);
          } else {
            _blend(idx, Math.round(180 * bright), Math.round(220 * bright), 255, alpha);
          }
        }
      }
    }
  } else if (type === 'steam') {
    // Vapor streak: wavy vertical column rising, 3px wide × 10px tall
    var sH = Math.round(10 * size), sW = Math.round(3 * size);
    for (var dy = 0; dy < sH; dy++) {
      var waveOff = Math.round(Math.sin(dy * 0.8 + cx * 0.5) * 1.5 * size);
      var rowAlpha = alpha * (1 - dy / sH * 0.6);  // fade toward top
      for (var dx = 0; dx < sW; dx++) {
        px = Math.round(cx + dx - sW / 2 + waveOff);
        py = Math.round(cy - dy);  // rises upward
        if (px >= 0 && px < PW && py >= 0 && py < PH) {
          idx = py * PW + px;
          _blend(idx, 240, 240, 245, rowAlpha);
        }
      }
    }
  } else if (type === 'sparkle') {
    // 4-pointed star: center + 4 cardinal arms + 4 diagonal stubs
    var arm = Math.round(3 * size);
    var diagArm = Math.round(2 * size);
    // Center pixel
    px = Math.round(cx); py = Math.round(cy);
    if (px >= 0 && px < PW && py >= 0 && py < PH) _blend(py * PW + px, 255, 255, 255, alpha);
    // Cardinal arms
    for (var d = 1; d <= arm; d++) {
      var armA = alpha * (1 - d / (arm + 1));
      var offsets = [[d, 0], [-d, 0], [0, d], [0, -d]];
      for (var o = 0; o < 4; o++) {
        px = Math.round(cx + offsets[o][0]); py = Math.round(cy + offsets[o][1]);
        if (px >= 0 && px < PW && py >= 0 && py < PH) _blend(py * PW + px, 255, 255, 180, armA);
      }
    }
    // Diagonal stubs
    for (var d = 1; d <= diagArm; d++) {
      var diagA = alpha * (1 - d / (diagArm + 1)) * 0.6;
      var diags = [[d, d], [-d, d], [d, -d], [-d, -d]];
      for (var o = 0; o < 4; o++) {
        px = Math.round(cx + diags[o][0]); py = Math.round(cy + diags[o][1]);
        if (px >= 0 && px < PW && py >= 0 && py < PH) _blend(py * PW + px, 255, 255, 200, diagA);
      }
    }
  } else if (type === 'dust') {
    // Dust cloud: fuzzy circle with noise — denser and larger
    var rad = Math.round(4 * size);
    for (var dy = -rad; dy <= rad; dy++) {
      for (var dx = -rad; dx <= rad; dx++) {
        var dist2 = dx * dx + dy * dy;
        if (dist2 > rad * rad) continue;
        // Pseudo-random skip for dusty look (skip 1/8 instead of 1/4)
        if (((dx * 7 + dy * 13 + Math.round(cx)) & 7) === 0) continue;
        var dist = Math.sqrt(dist2);
        var falloff = 1 - dist / rad;
        px = Math.round(cx + dx); py = Math.round(cy + dy);
        if (px >= 0 && px < PW && py >= 0 && py < PH) {
          idx = py * PW + px;
          var cr = 180 + ((dx * 3 + dy * 7) & 15);
          var cg = 165 + ((dx * 5 + dy * 11) & 15);
          var cb = 130 + ((dx * 9 + dy * 3) & 15);
          _blend(idx, cr, cg, cb, alpha * falloff);
        }
      }
    }
  } else if (type === 'hearts') {
    // Pixel art heart: 7×6 pattern
    var heartMap = [
      [0,1,1,0,1,1,0],
      [1,1,1,1,1,1,1],
      [1,1,1,1,1,1,1],
      [0,1,1,1,1,1,0],
      [0,0,1,1,1,0,0],
      [0,0,0,1,0,0,0]
    ];
    var hScale = Math.max(1, Math.round(size));
    for (var hy = 0; hy < 6; hy++) {
      for (var hx = 0; hx < 7; hx++) {
        if (!heartMap[hy][hx]) continue;
        for (var sy = 0; sy < hScale; sy++) {
          for (var sx = 0; sx < hScale; sx++) {
            px = Math.round(cx - 3 * hScale + hx * hScale + sx);
            py = Math.round(cy - 3 * hScale + hy * hScale + sy);
            if (px >= 0 && px < PW && py >= 0 && py < PH) {
              idx = py * PW + px;
              // Lighter center, deeper edges
              var isCenter = (hy >= 1 && hy <= 3 && hx >= 2 && hx <= 4);
              if (isCenter) {
                _blend(idx, 255, 120, 150, alpha);
              } else {
                _blend(idx, 230, 60, 100, alpha);
              }
            }
          }
        }
      }
    }
  } else if (type === 'anger') {
    // Manga anger mark 💢: 4 L-shaped corners pointing INWARD, forming a + shape
    // TL=┘ TR=└ BL=┐ BR=┌, each 2px thick, gap at center
    var angerMap = [
      [0,1,1,0,0,0,1,1,0],
      [0,1,0,0,0,0,0,1,0],
      [0,1,0,0,0,0,0,1,0],
      [0,0,0,0,0,0,0,0,0],
      [1,1,1,0,0,0,1,1,1],
      [0,0,0,0,0,0,0,0,0],
      [0,1,0,0,0,0,0,1,0],
      [0,1,0,0,0,0,0,1,0],
      [0,1,1,0,0,0,1,1,0],
    ];
    var aScale = Math.max(1, Math.round(size));
    var aW = 9, aH = 9;
    for (var ay = 0; ay < aH; ay++) {
      for (var ax = 0; ax < aW; ax++) {
        if (!angerMap[ay][ax]) continue;
        for (var sy = 0; sy < aScale; sy++) {
          for (var sx = 0; sx < aScale; sx++) {
            px = Math.round(cx - 4 * aScale + ax * aScale + sx);
            py = Math.round(cy - 4 * aScale + ay * aScale + sy);
            if (px >= 0 && px < PW && py >= 0 && py < PH) {
              idx = py * PW + px;
              _blend(idx, 220, 40, 40, alpha);
            }
          }
        }
      }
    }
  } else if (type === 'fear') {
    // Sweat droplet: teardrop shape, pointed top, round bottom
    var dropMap = [
      [0,0,1,0,0],
      [0,1,1,1,0],
      [0,1,1,1,0],
      [1,1,1,1,1],
      [1,1,1,1,1],
      [1,1,1,1,1],
      [0,1,1,1,0],
    ];
    var fScale = Math.max(1, Math.round(size));
    for (var fy = 0; fy < 7; fy++) {
      for (var fx = 0; fx < 5; fx++) {
        if (!dropMap[fy][fx]) continue;
        for (var sy = 0; sy < fScale; sy++) {
          for (var sx = 0; sx < fScale; sx++) {
            px = Math.round(cx - 2 * fScale + fx * fScale + sx);
            py = Math.round(cy - 3 * fScale + fy * fScale + sy);
            if (px >= 0 && px < PW && py >= 0 && py < PH) {
              idx = py * PW + px;
              // White highlight at top-left, blue body
              var isHighlight = (fy <= 1 && fx <= 2) || (fy === 2 && fx === 1);
              if (isHighlight) {
                _blend(idx, 200, 230, 255, alpha);
              } else {
                _blend(idx, 100, 160, 230, alpha);
              }
            }
          }
        }
      }
    }
  }
}

AnimationTemplates.register('emanation', function(params) {
  var prefix = params.entityPrefix || '';
  var pType = params.particleType || 'steam';
  var totalSprites = _clamp(params.particleCount || 18, 8, 30);
  var prefixDot = prefix + '.';

  // Stronger tints per type — applied to entity base color during animation
  var tints = {
    steam:   { r: 50, g: -25, b: -50 },
    frost:   { r: -50, g: 0, b: 70 },
    sparkle: { r: 40, g: 40, b: 25 },
    dust:    { r: -25, g: -25, b: -40 },
    hearts:  { r: 50, g: -15, b: 15 },
    anger:   { r: 60, g: -20, b: -20 },
    fear:    { r: 40, g: 40, b: 50 },
  };
  var tint = tints[pType] || tints.steam;

  // Movement configs per type
  var moveConfigs = {
    steam:   { vy: -18, vx: 0, vxJitter: 6, vyJitter: 3, gravity: 0, sway: 1.5 },
    frost:   { vy: 10, vx: 0, vxJitter: 3, vyJitter: 2, gravity: 5, sway: 1.0 },
    sparkle: { vy: 0, vx: 0, vxJitter: 2, vyJitter: 2, gravity: 0, sway: 0 },
    dust:    { vy: 6, vx: 2, vxJitter: 4, vyJitter: 2, gravity: 3, sway: 0.8 },
    hearts:  { vy: -12, vx: 0, vxJitter: 5, vyJitter: 2, gravity: 0, sway: 2.0 },
    anger:   { vy: 0, vx: 0, vxJitter: 1, vyJitter: 1, gravity: 0, sway: 0 },
    fear:    { vy: 15, vx: 0, vxJitter: 3, vyJitter: 3, gravity: 8, sway: 0.5 },
  };
  var mc = moveConfigs[pType] || moveConfigs.steam;

  // Pre-generate sprite data (position, velocity, spawn time, size)
  var sprites = [];
  var waves = 4;
  var perWave = Math.ceil(totalSprites / waves);

  // Use seeded pseudo-random for deterministic results per animation instance
  var _seed = 12345 + prefix.length * 7;
  function _rand() { _seed = (_seed * 16807 + 0) % 2147483647; return (_seed & 0xffff) / 0xffff; }

  for (var w = 0; w < waves; w++) {
    var waveTime = w / waves * 0.7;  // waves spawn from t=0 to t=0.7
    for (var s = 0; s < perWave && sprites.length < totalSprites; s++) {
      var sizeVar = 0.7 + _rand() * 0.6;  // 0.7 to 1.3
      sprites.push({
        spawnT: waveTime + _rand() * 0.08,  // slight jitter within wave
        // spawn position will be set on first use (needs entity bounds)
        side: _rand(),       // 0-1 for position along contour
        sideType: Math.floor(_rand() * 4),  // 0=top, 1=right, 2=bottom, 3=left
        vx: mc.vx + ((_rand() - 0.5) * 2) * mc.vxJitter,
        vy: mc.vy + ((_rand() - 0.5) * 2) * mc.vyJitter,
        size: sizeVar,
        x: 0, y: 0,
        maxAge: 0.5 + _rand() * 0.3,
        flicker: (pType === 'sparkle') ? _rand() : -1,  // sparkle flicker phase
        initialized: false
      });
    }
  }

  return function animate(buf, PW, PH, t) {
    var env = _easeEnvelope(t, 0.15, 0.15);

    // Tint entity
    for (var i = 0; i < buf.length; i++) {
      if (buf[i].e === prefix || buf[i].e.startsWith(prefixDot)) {
        buf[i].r = _clamp(buf[i]._r + Math.round(tint.r * env), 0, 255);
        buf[i].g = _clamp(buf[i]._g + Math.round(tint.g * env), 0, 255);
        buf[i].b = _clamp(buf[i]._b + Math.round(tint.b * env), 0, 255);
      }
    }

    // Get bounds for spawn positions
    var bounds = _computeEntityBounds(buf, PW, prefix);
    if (bounds.x2 < 0) return;
    var bw = bounds.x2 - bounds.x1, bh = bounds.y2 - bounds.y1;

    // Update and draw each sprite
    for (var s = 0; s < sprites.length; s++) {
      var sp = sprites[s];
      if (t < sp.spawnT) continue;  // not yet spawned

      var age = t - sp.spawnT;
      if (age > sp.maxAge) continue;  // expired

      // Initialize spawn position on first frame
      if (!sp.initialized) {
        if (sp.sideType === 0) {
          sp.x = bounds.x1 + sp.side * bw; sp.y = bounds.y1 - 2;
        } else if (sp.sideType === 1) {
          sp.x = bounds.x2 + 2; sp.y = bounds.y1 + sp.side * bh;
        } else if (sp.sideType === 2) {
          sp.x = bounds.x1 + sp.side * bw; sp.y = bounds.y2 + 2;
        } else {
          sp.x = bounds.x1 - 2; sp.y = bounds.y1 + sp.side * bh;
        }
        sp.initialized = true;
      }

      // Update position
      var dt = 1 / 60;
      sp.x += sp.vx * dt;
      sp.y += sp.vy * dt;
      sp.vy += mc.gravity * dt;
      // Lateral sway
      if (mc.sway > 0) {
        sp.x += Math.sin(age * 8 + sp.side * 10) * mc.sway * dt * 15;
      }

      // Alpha envelope: fadeIn (0-20%), hold, fadeOut (last 30%)
      var lifeRatio = age / sp.maxAge;
      var spriteAlpha = env;
      if (lifeRatio < 0.2) spriteAlpha *= lifeRatio / 0.2;
      else if (lifeRatio > 0.7) spriteAlpha *= (1 - lifeRatio) / 0.3;

      // Sparkle flicker
      if (sp.flicker >= 0 && Math.sin(t * 20 + sp.flicker * 100) < -0.3) continue;

      _drawEmanationSprite(buf, PW, PH, pType, sp.x, sp.y, sp.size, spriteAlpha);
    }
  };
}, 3000);

// ── T1: Flashback ──
// Target desaturates briefly (palette swap to grey) then re-saturates.
// Differs from Color Pop: HERE the target ITSELF loses its colors.
// Scaffolds past tense — "this already happened."
AnimationTemplates.register('flashback', function(params) {
  var prefix = params.entityPrefix || '';

  return function animate(buf, PW, PH, t) {
    // Envelope: desaturate 0→1 (0-0.3), hold (0.3-0.7), re-saturate 1→0 (0.7-1)
    var desat;
    if (t < 0.3) desat = t / 0.3;
    else if (t < 0.7) desat = 1;
    else desat = 1 - (t - 0.7) / 0.3;

    if (desat < 0.01) return;

    for (var i = 0; i < buf.length; i++) {
      if (buf[i].e === prefix || buf[i].e.startsWith(prefix + '.')) {
        var L = Math.round(buf[i]._r * 0.299 + buf[i]._g * 0.587 + buf[i]._b * 0.114);
        buf[i].r = Math.round(buf[i]._r * (1 - desat) + L * desat);
        buf[i].g = Math.round(buf[i]._g * (1 - desat) + L * desat);
        buf[i].b = Math.round(buf[i]._b * (1 - desat) + L * desat);
      }
    }
  };
}, 1500);

// ── T2: Timelapse ──
AnimationTemplates.register('timelapse', function(params) {
  var cycles = _clamp(params.cycles || 2, 1, 4);
  var ps = new ParticleSystem(ParticlePresets.sparkle);
  var lastPhase = -1;

  return function animate(buf, PW, PH, t) {
    // Sine wave: 0 = day, 1 = night
    var phase = 0.5 - 0.5 * Math.cos(t * Math.PI * 2 * cycles);

    // Night tint: shift all pixels toward dark blue
    for (var i = 0; i < buf.length; i++) {
      var nightR = Math.round(buf[i]._r * (1 - phase * 0.7) + 20 * phase);
      var nightG = Math.round(buf[i]._g * (1 - phase * 0.7) + 15 * phase);
      var nightB = Math.round(buf[i]._b * (1 - phase * 0.5) + 60 * phase);
      buf[i].r = _clamp(nightR, 0, 255);
      buf[i].g = _clamp(nightG, 0, 255);
      buf[i].b = _clamp(nightB, 0, 255);
    }

    // Stars during dark phases (phase > 0.6)
    if (phase > 0.6) {
      var currentPhaseIdx = Math.floor(t * cycles * 2);
      if (currentPhaseIdx !== lastPhase) {
        // Spawn new stars batch
        for (var s = 0; s < 8; s++) {
          ps.spawn(Math.random() * PW, Math.random() * PH * 0.4);
        }
        lastPhase = currentPhaseIdx;
      }
    }
    ps.update(1 / 60);
    ps.draw(buf, PW, PH);
  };
}, 2000);

// ── A1: Motion Lines ──
// Fast burst movements with pauses between. Direction coherent with entity
// type (birds: any direction, others: left/right). Thick, visible speed streaks.
AnimationTemplates.register('motion_lines', function(params) {
  var prefix = params.entityPrefix || '';
  var dir = params.direction || 'right';   // 'left', 'right', 'any'
  var lineCount = _clamp(params.lineCount || 7, 4, 10);
  var lineLen = _clamp(params.lineLength || 20, 10, 30);
  var amp = _clamp(params.amplitude || 10, 5, 15);

  // Streak colors: alternating grey shades
  var streakColors = [
    [160, 160, 160],  // mid grey
    [240, 240, 240],  // near white
    [100, 100, 100],  // dark grey
    [200, 200, 200],  // light grey
    [80, 80, 80],     // charcoal
    [220, 220, 220],  // pale grey
    [130, 130, 130],  // medium grey
  ];

  // Pre-generate per-streak variation (seeded by lineCount for determinism)
  var streakVariation = [];
  var _sv = 12345;
  for (var sv = 0; sv < lineCount; sv++) {
    _sv = (_sv * 16807 + 31) % 2147483647;
    var lenMult = 0.5 + (_sv % 1000) / 1000 * 1.0;  // 0.5× to 1.5× length
    _sv = (_sv * 16807 + 31) % 2147483647;
    var posJitter = ((_sv % 1000) / 500 - 1.0) * 0.12; // ±12% sampling offset
    _sv = (_sv * 16807 + 31) % 2147483647;
    var thickBonus = (_sv % 3 === 0) ? 1 : 0; // some streaks 1px thicker
    streakVariation.push({ lenMult: lenMult, posJitter: posJitter, thickBonus: thickBonus });
  }

  // Build burst patterns based on direction mode
  var bursts;
  if (dir === 'any') {
    // Flying entities: varied directions
    bursts = [
      { start: 0.05, end: 0.17, dx: amp, dy: -Math.round(amp * 0.5) },
      { start: 0.28, end: 0.40, dx: -Math.round(amp * 0.8), dy: Math.round(amp * 0.3) },
      { start: 0.52, end: 0.64, dx: Math.round(amp * 0.6), dy: Math.round(amp * 0.7) },
      { start: 0.75, end: 0.87, dx: -amp, dy: -Math.round(amp * 0.4) },
    ];
  } else {
    // Ground entities: left/right alternation
    var sign = (dir === 'left') ? -1 : 1;
    bursts = [
      { start: 0.05, end: 0.17, dx: amp * sign, dy: 0 },
      { start: 0.28, end: 0.40, dx: -amp * sign, dy: 0 },
      { start: 0.52, end: 0.64, dx: Math.round(amp * 0.9) * sign, dy: 0 },
      { start: 0.75, end: 0.87, dx: -Math.round(amp * 0.8) * sign, dy: 0 },
    ];
  }

  // Cache outermost silhouette pixels per row/col (computed once on first frame)
  var silhouetteCache = null;

  return function animate(buf, PW, PH, t) {
    var bounds = _computeEntityBounds(buf, PW, prefix);
    if (bounds.x2 <= 0) return;

    // Pre-compute outermost entity pixels per row and column on first frame
    if (!silhouetteCache) {
      silhouetteCache = { byRow: {}, byCol: {} };
      var prefixDot = prefix + '.';
      for (var i = 0; i < buf.length; i++) {
        var e = buf[i].e;
        if (e !== prefix && !e.startsWith(prefixDot)) continue;
        var ex = i % PW, ey = (i - ex) / PW;
        if (!silhouetteCache.byRow[ey]) {
          silhouetteCache.byRow[ey] = { minX: ex, maxX: ex };
        } else {
          if (ex < silhouetteCache.byRow[ey].minX) silhouetteCache.byRow[ey].minX = ex;
          if (ex > silhouetteCache.byRow[ey].maxX) silhouetteCache.byRow[ey].maxX = ex;
        }
        if (!silhouetteCache.byCol[ex]) {
          silhouetteCache.byCol[ex] = { minY: ey, maxY: ey };
        } else {
          if (ey < silhouetteCache.byCol[ex].minY) silhouetteCache.byCol[ex].minY = ey;
          if (ey > silhouetteCache.byCol[ex].maxY) silhouetteCache.byCol[ex].maxY = ey;
        }
      }
    }

    // Determine current burst and shift amount
    var shiftX = 0, shiftY = 0;
    var activeBurst = null;
    for (var b = 0; b < bursts.length; b++) {
      var burst = bursts[b];
      if (t >= burst.start && t <= burst.end) {
        activeBurst = burst;
        var burstT = (t - burst.start) / (burst.end - burst.start);
        var ease = 1 - (1 - burstT) * (1 - burstT);
        shiftX = Math.round(burst.dx * ease);
        shiftY = Math.round(burst.dy * ease);
        break;
      }
    }

    // Shift entity if in a burst
    if (shiftX !== 0 || shiftY !== 0) {
      var pixels = _collectEntityPixels(buf, PW, prefix);
      _blankEntityPixels(buf, pixels);
      _redrawEntityPixels(buf, PW, PH, pixels, shiftX, shiftY);
    }

    // Draw speed streaks ONLY during bursts, using silhouette contour
    if (activeBurst) {
      var burstProgress = (t - activeBurst.start) / (activeBurst.end - activeBurst.start);
      var bDx = activeBurst.dx, bDy = activeBurst.dy;
      var bLen = Math.sqrt(bDx * bDx + bDy * bDy);
      if (bLen < 1) return;
      // Streak direction: opposite of movement
      var streakDirX = -bDx / bLen;
      var streakDirY = -bDy / bLen;
      // Perpendicular direction for spreading lines
      var perpX = -streakDirY;
      var perpY = streakDirX;

      var streakAlpha = 0.8 * Math.sin(burstProgress * Math.PI);

      // Build trailing silhouette points from per-row/col extremes
      var trailingPts = [];
      if (Math.abs(bDx) >= Math.abs(bDy)) {
        // Primarily horizontal movement: use per-row extremes
        var rows = Object.keys(silhouetteCache.byRow);
        for (var ri = 0; ri < rows.length; ri++) {
          var ry = parseInt(rows[ri]);
          var row = silhouetteCache.byRow[ry];
          // Trailing side: left edge if moving right, right edge if moving left
          trailingPts.push({ x: bDx > 0 ? row.minX : row.maxX, y: ry });
        }
        // Sort by y
        trailingPts.sort(function(a, b2) { return a.y - b2.y; });
      } else {
        // Primarily vertical movement: use per-col extremes
        var cols = Object.keys(silhouetteCache.byCol);
        for (var ci = 0; ci < cols.length; ci++) {
          var cx = parseInt(cols[ci]);
          var col = silhouetteCache.byCol[cx];
          // Trailing side: top edge if moving down, bottom edge if moving up
          trailingPts.push({ x: cx, y: bDy > 0 ? col.minY : col.maxY });
        }
        // Sort by x
        trailingPts.sort(function(a, b2) { return a.x - b2.x; });
      }

      // Fallback to bounding box if no silhouette points
      if (trailingPts.length < lineCount) {
        trailingPts = [];
        for (var l = 0; l < lineCount; l++) {
          if (Math.abs(bDx) >= Math.abs(bDy)) {
            trailingPts.push({
              x: bDx > 0 ? bounds.x1 : bounds.x2,
              y: bounds.y1 + ((l + 0.5) / lineCount) * (bounds.y2 - bounds.y1)
            });
          } else {
            trailingPts.push({
              x: bounds.x1 + ((l + 0.5) / lineCount) * (bounds.x2 - bounds.x1),
              y: bDy > 0 ? bounds.y1 : bounds.y2
            });
          }
        }
      }

      // Sample lineCount irregularly-spaced contour points
      for (var l = 0; l < lineCount; l++) {
        var sv2 = streakVariation[l];
        // Jittered sampling position along contour
        var basePos = (l + 0.5) / lineCount + sv2.posJitter;
        basePos = Math.max(0.02, Math.min(0.98, basePos));
        var ptIdx = Math.floor(basePos * trailingPts.length);
        ptIdx = Math.min(ptIdx, trailingPts.length - 1);
        var pt = trailingPts[ptIdx];
        var startX = pt.x + shiftX;
        var startY = pt.y + shiftY;

        var sc = streakColors[l % streakColors.length];
        // Variable streak length per line
        var curLen = Math.round(lineLen * sv2.lenMult * (0.6 + burstProgress * 0.4));

        for (var d = 0; d < curLen; d++) {
          var fadeD = 1 - d / curLen;
          var dAlpha = streakAlpha * fadeD;
          // Taper width: 3-4px near entity, 2-3px mid, 1px at end (with per-streak bonus)
          var baseThick = d < curLen * 0.3 ? 3 : (d < curLen * 0.7 ? 2 : 1);
          var thickness = baseThick + (d < curLen * 0.5 ? sv2.thickBonus : 0);
          var halfT = Math.floor(thickness / 2);

          for (var tw = -halfT; tw <= halfT; tw++) {
            var sx = Math.round(startX + streakDirX * d + perpX * tw * 0.7);
            var sy = Math.round(startY + streakDirY * d + perpY * tw * 0.7);
            if (sx >= 0 && sx < PW && sy >= 0 && sy < PH) {
              var si = sy * PW + sx;
              buf[si].r = Math.round(buf[si].r * (1 - dAlpha) + sc[0] * dAlpha);
              buf[si].g = Math.round(buf[si].g * (1 - dAlpha) + sc[1] * dAlpha);
              buf[si].b = Math.round(buf[si].b * (1 - dAlpha) + sc[2] * dAlpha);
            }
          }
        }
      }
    }
  };
}, 3000);

// ── A2: Anticipation ──
// Entity compresses slightly, lurches forward, then freezes mid-motion.
// Like a momentum that was interrupted. Scaffolds missing/uncompleted action verbs.
AnimationTemplates.register('anticipation', function(params) {
  var prefix = params.entityPrefix || '';
  var compressY = _clamp(params.compressY || 3, 1, 8);
  var lurchPx = _clamp(params.lurchPixels || 10, 3, 20);
  var lurchDir = params.lurchDirection || 'right';
  var dirSign = lurchDir === 'left' ? -1 : 1;

  return function animate(buf, PW, PH, t) {
    var pixels = _collectEntityPixels(buf, PW, prefix);
    if (pixels.length === 0) return;

    var dy = 0, dx = 0;
    if (t < 0.15) {
      // Phase 1: Compress down (anticipation)
      var p1 = t / 0.15;
      dy = Math.round(compressY * p1);
    } else if (t < 0.35) {
      // Phase 2: Lurch forward with decompression
      var p2 = (t - 0.15) / 0.2;
      dy = Math.round(compressY * (1 - p2));
      dx = Math.round(lurchPx * p2 * dirSign);
    } else {
      // Phase 3: Freeze mid-motion (hold displaced position)
      dx = Math.round(lurchPx * dirSign);
      // Subtle vibration during freeze
      dx += Math.round((Math.random() - 0.5) * 0.8);
    }

    if (dy === 0 && dx === 0) return;

    var bounds = _computeEntityBounds(buf, PW, prefix);
    _blankEntityPixels(buf, pixels);

    // Compress: bottom rows shift up proportionally during phase 1
    for (var j = 0; j < pixels.length; j++) {
      var p = pixels[j];
      var relY = (p.y - bounds.y1) / Math.max(1, bounds.y2 - bounds.y1);
      var pyOffset = Math.round(dy * relY);
      var nx = p.x + dx;
      var ny = p.y + pyOffset;
      if (nx >= 0 && nx < PW && ny >= 0 && ny < PH) {
        var ni = ny * PW + nx;
        buf[ni].r = p.r; buf[ni].g = p.g; buf[ni].b = p.b;
      }
    }
  };
}, 1500);

// ── Decomposition (not in new grammar — legacy support) ──
AnimationTemplates.register('decomposition', function(params) {
  var prefix = params.entityPrefix || '';
  var sepPx = _clamp(params.separationPixels || 8, 2, 20);

  return function animate(buf, PW, PH, t) {
    var pixels = _collectEntityPixels(buf, PW, prefix);
    if (pixels.length === 0) return;

    // Group by second-level sub-entity prefix
    var groups = {};
    for (var j = 0; j < pixels.length; j++) {
      var e = pixels[j].e;
      // Extract group: "fox_01.head.ears" → "fox_01.head"
      var parts = e.split('.');
      var groupKey;
      if (parts.length >= 2) groupKey = parts[0] + '.' + parts[1];
      else groupKey = parts[0];
      if (!groups[groupKey]) groups[groupKey] = { pixels: [], cx: 0, cy: 0 };
      groups[groupKey].pixels.push(pixels[j]);
    }

    // Compute centroids
    var entityBounds = _computeEntityBounds(buf, PW, prefix);
    for (var key in groups) {
      var g = groups[key];
      var sx = 0, sy = 0;
      for (var p = 0; p < g.pixels.length; p++) {
        sx += g.pixels[p].x;
        sy += g.pixels[p].y;
      }
      g.cx = sx / g.pixels.length;
      g.cy = sy / g.pixels.length;
    }

    // Compute separation amount
    var sep;
    if (t < 0.3) sep = sepPx * (t / 0.3);
    else if (t < 0.6) sep = sepPx;
    else sep = sepPx * (1 - (t - 0.6) / 0.4);

    _blankEntityPixels(buf, pixels);

    // Redraw each group at offset from entity center
    for (var key in groups) {
      var g = groups[key];
      var dirX = g.cx - entityBounds.cx;
      var dirY = g.cy - entityBounds.cy;
      var dist = Math.sqrt(dirX * dirX + dirY * dirY) || 1;
      var dx = Math.round(dirX / dist * sep);
      var dy = Math.round(dirY / dist * sep);

      // Pulse during hold phase
      var brightness = 1;
      if (t >= 0.3 && t < 0.6) {
        brightness = 1 + 0.2 * Math.sin((t - 0.3) / 0.3 * Math.PI * 4);
      }

      for (var p = 0; p < g.pixels.length; p++) {
        var px = g.pixels[p];
        var nx = px.x + dx, ny = px.y + dy;
        if (nx >= 0 && nx < PW && ny >= 0 && ny < PH) {
          var ni = ny * PW + nx;
          buf[ni].r = Math.min(255, Math.round(px.r * brightness));
          buf[ni].g = Math.min(255, Math.round(px.g * brightness));
          buf[ni].b = Math.min(255, Math.round(px.b * brightness));
        }
      }
    }
  };
}, 1800);

// ── R3: Causal Push ──
// Element A rushes toward element B + impact burst at collision.
// Scaffolds "A causes B" — consequence, causal connectors (because, so).
AnimationTemplates.register('causal_push', function(params) {
  var prefixA = params.entityPrefixA || params.entityPrefix || '';
  var prefixB = params.entityPrefixB || '';
  var rushPx = _clamp(params.rushPixels || 15, 3, 30);
  var ps = new ParticleSystem(ParticlePresets.explosion);
  var impacted = false;

  return function animate(buf, PW, PH, t) {
    var boundsA = _computeEntityBounds(buf, PW, prefixA);
    var boundsB = prefixB ? _computeEntityBounds(buf, PW, prefixB) : null;
    if (!boundsB || boundsA.x2 < 0) return;

    var pixelsA = _collectEntityPixels(buf, PW, prefixA);
    var dirAB = boundsB.cx > boundsA.cx ? 1 : -1;
    var dxA = 0;

    if (t < 0.4) {
      // Rush toward B
      var progress = t / 0.4;
      // Ease-in curve for acceleration
      dxA = Math.round(rushPx * progress * progress * dirAB);
    } else if (t < 0.5) {
      // Impact — hold at max displacement
      dxA = Math.round(rushPx * dirAB);

      // Spawn impact burst
      if (!impacted) {
        var impactX = Math.round((boundsA.cx + rushPx * dirAB + boundsB.cx) / 2);
        var impactY = Math.round((boundsA.cy + boundsB.cy) / 2);
        ps.burst(impactX, impactY, 12);
        impacted = true;
      }
    } else {
      // Recoil — bounce back
      var recoil = (t - 0.5) / 0.5;
      dxA = Math.round(rushPx * (1 - recoil) * dirAB);
    }

    if (dxA !== 0) {
      _blankEntityPixels(buf, pixelsA);
      _redrawEntityPixels(buf, PW, PH, pixelsA, dxA, 0);
    }

    // B shakes slightly on impact
    if (t >= 0.4 && t < 0.65) {
      var pixelsB = _collectEntityPixels(buf, PW, prefixB);
      var shake = Math.round(3 * Math.sin((t - 0.4) / 0.25 * Math.PI * 6) * (1 - (t - 0.4) / 0.25));
      if (shake !== 0 && pixelsB.length > 0) {
        _blankEntityPixels(buf, pixelsB);
        _redrawEntityPixels(buf, PW, PH, pixelsB, shake, 0);
      }
    }

    ps.update(1 / 60);
    ps.draw(buf, PW, PH);
  };
}, 1500);

// ── Q1: Bonk ──
AnimationTemplates.register('bonk', function(params) {
  var prefixA = params.entityPrefixA || params.entityPrefix || '';
  var prefixB = params.entityPrefixB || '';
  var impactPx = _clamp(params.impactPixels || 6, 2, 15);

  return function animate(buf, PW, PH, t) {
    var boundsA = _computeEntityBounds(buf, PW, prefixA);
    var boundsB = prefixB ? _computeEntityBounds(buf, PW, prefixB) : null;

    var pixelsA = _collectEntityPixels(buf, PW, prefixA);
    var pixelsB = prefixB ? _collectEntityPixels(buf, PW, prefixB) : [];

    var dxA = 0, dxB = 0;

    if (t < 0.3) {
      // Move toward each other
      var approach = t / 0.3;
      if (boundsB) {
        var dirAB = boundsB.cx > boundsA.cx ? 1 : -1;
        dxA = Math.round(impactPx * approach * dirAB);
        dxB = Math.round(-impactPx * approach * dirAB);
      } else {
        dxA = Math.round(impactPx * Math.sin(approach * Math.PI * 0.5));
      }
    } else if (t < 0.35) {
      // Impact jitter
      var jitter = Math.round((Math.random() - 0.5) * 3);
      dxA = jitter; dxB = -jitter;
    } else {
      // Bounce back with damped oscillation
      var bt = (t - 0.35) / 0.65;
      var decay = (1 - bt);
      var bounce = Math.sin(bt * Math.PI * 3) * decay;
      if (boundsB) {
        var dirAB = boundsB.cx > boundsA.cx ? 1 : -1;
        dxA = Math.round(-impactPx * 0.5 * bounce * dirAB);
        dxB = Math.round(impactPx * 0.5 * bounce * dirAB);
      } else {
        dxA = Math.round(-impactPx * 0.5 * bounce);
      }
    }

    _blankEntityPixels(buf, pixelsA);
    if (pixelsB.length > 0) _blankEntityPixels(buf, pixelsB);

    _redrawEntityPixels(buf, PW, PH, pixelsA, dxA, 0);
    if (pixelsB.length > 0) _redrawEntityPixels(buf, PW, PH, pixelsB, dxB, 0);

    // Star particles at impact point during collision
    if (t >= 0.28 && t < 0.4) {
      var impactX = boundsB ? Math.round((boundsA.cx + boundsB.cx) / 2) : boundsA.cx;
      var impactY = boundsB ? Math.round((boundsA.cy + boundsB.cy) / 2) : boundsA.cy;
      var starAlpha = 1 - Math.abs(t - 0.34) / 0.06;
      // Draw small star pattern
      var starOffsets = [[-2,0],[2,0],[0,-2],[0,2],[-1,-1],[1,-1],[-1,1],[1,1]];
      for (var s = 0; s < starOffsets.length; s++) {
        var sx = impactX + starOffsets[s][0], sy = impactY + starOffsets[s][1];
        if (sx >= 0 && sx < PW && sy >= 0 && sy < PH) {
          var si = sy * PW + sx;
          buf[si].r = Math.round(buf[si].r * (1 - starAlpha) + 255 * starAlpha);
          buf[si].g = Math.round(buf[si].g * (1 - starAlpha) + 255 * starAlpha);
          buf[si].b = Math.round(buf[si].b * (1 - starAlpha) + 100 * starAlpha);
        }
      }
    }
  };
}, 1200);

// ── Q2: Sequential Glow ──
AnimationTemplates.register('sequential_glow', function(params) {
  var prefixes = params.entityPrefixes || [params.entityPrefix || ''];
  var n = prefixes.length;

  return function animate(buf, PW, PH, t) {
    // Determine which entity is currently glowing
    var activeIdx = Math.min(Math.floor(t * n), n - 1);
    var phaseT = (t * n) % 1; // 0-1 within current entity's window
    var glow = 0.5 + 0.5 * Math.sin(phaseT * Math.PI);

    for (var i = 0; i < buf.length; i++) {
      var p = buf[i];
      if (!p.e || p.e === '') continue;

      var isActive = false;
      for (var a = 0; a <= activeIdx; a++) {
        if (p.e === prefixes[a] || p.e.startsWith(prefixes[a] + '.')) {
          if (a === activeIdx) {
            isActive = true;
          }
          break;
        }
      }

      if (isActive) {
        // Glow the active entity
        var boost = 1 + 0.3 * glow;
        p.r = Math.min(255, Math.round(p._r * boost));
        p.g = Math.min(255, Math.round(p._g * boost));
        p.b = Math.min(255, Math.round(p._b * boost));
      } else {
        // Check if this is any of the listed entities (dim them)
        var isListed = false;
        for (var k = 0; k < n; k++) {
          if (p.e === prefixes[k] || p.e.startsWith(prefixes[k] + '.')) {
            isListed = true; break;
          }
        }
        if (isListed) {
          p.r = Math.round(p._r * 0.6);
          p.g = Math.round(p._g * 0.6);
          p.b = Math.round(p._b * 0.6);
        }
      }
    }
  };
}, 1600);

// ── Q3: Ghost Outline ──
AnimationTemplates.register('ghost_outline', function(params) {
  var prefix = params.entityPrefix || '';
  var gc = params.ghostColor || [180, 180, 180];

  return function animate(buf, PW, PH, t) {
    var bounds = _computeEntityBounds(buf, PW, prefix);

    for (var i = 0; i < buf.length; i++) {
      if (buf[i].e === prefix || buf[i].e.startsWith(prefix + '.')) {
        var x = i % PW, y = Math.floor(i / PW);

        if (t < 0.3) {
          // Fade to dotted outline
          var fadeProgress = t / 0.3;
          var isCheckerboard = (x + y) % 2 === 0;
          if (isCheckerboard) {
            // These pixels become ghost color
            buf[i].r = Math.round(buf[i]._r * (1 - fadeProgress) + gc[0] * fadeProgress);
            buf[i].g = Math.round(buf[i]._g * (1 - fadeProgress) + gc[1] * fadeProgress);
            buf[i].b = Math.round(buf[i]._b * (1 - fadeProgress) + gc[2] * fadeProgress);
          } else {
            // These pixels fade to background
            buf[i].r = Math.round(buf[i]._r * (1 - fadeProgress) + buf[i]._br * fadeProgress);
            buf[i].g = Math.round(buf[i]._g * (1 - fadeProgress) + buf[i]._bg * fadeProgress);
            buf[i].b = Math.round(buf[i]._b * (1 - fadeProgress) + buf[i]._bb * fadeProgress);
          }
        } else if (t < 0.7) {
          // Pulsing dotted outline
          var pulsePhase = (t - 0.3) / 0.4;
          var pulse = 0.6 + 0.4 * Math.sin(pulsePhase * Math.PI * 4);
          var isCheckerboard = (x + y) % 2 === 0;
          if (isCheckerboard) {
            buf[i].r = Math.round(gc[0] * pulse);
            buf[i].g = Math.round(gc[1] * pulse);
            buf[i].b = Math.round(gc[2] * pulse);
          } else {
            buf[i].r = buf[i]._br;
            buf[i].g = buf[i]._bg;
            buf[i].b = buf[i]._bb;
          }
        } else {
          // Dissolve bottom-up
          var dissolveProgress = (t - 0.7) / 0.3;
          var dissolveLine = bounds.y2 - dissolveProgress * (bounds.y2 - bounds.y1);
          if (y > dissolveLine) {
            // Already dissolved
            buf[i].r = buf[i]._br;
            buf[i].g = buf[i]._bg;
            buf[i].b = buf[i]._bb;
          } else {
            var isCheckerboard = (x + y) % 2 === 0;
            if (isCheckerboard) {
              buf[i].r = gc[0]; buf[i].g = gc[1]; buf[i].b = gc[2];
            } else {
              buf[i].r = buf[i]._br; buf[i].g = buf[i]._bg; buf[i].b = buf[i]._bb;
            }
          }
        }
      }
    }
  };
}, 1500);

// ── R1: Magnetism ──
AnimationTemplates.register('magnetism', function(params) {
  var prefixA = params.entityPrefixA || params.entityPrefix || '';
  var prefixB = params.entityPrefixB || '';
  var attractPx = _clamp(params.attractPixels || 10, 2, 25);
  var ps = new ParticleSystem(ParticlePresets.sparkle);
  var sparkled = false;

  return function animate(buf, PW, PH, t) {
    var boundsA = _computeEntityBounds(buf, PW, prefixA);
    var boundsB = prefixB ? _computeEntityBounds(buf, PW, prefixB) : null;
    if (!boundsB) return;

    var pixelsA = _collectEntityPixels(buf, PW, prefixA);
    var pixelsB = _collectEntityPixels(buf, PW, prefixB);

    var dirAB = boundsB.cx > boundsA.cx ? 1 : -1;
    var dxA = 0, dxB = 0;

    if (t < 0.4) {
      // Attract toward each other
      var progress = t / 0.4;
      dxA = Math.round(attractPx / 2 * progress * dirAB);
      dxB = Math.round(-attractPx / 2 * progress * dirAB);
    } else if (t < 0.7) {
      // Hold close
      dxA = Math.round(attractPx / 2 * dirAB);
      dxB = Math.round(-attractPx / 2 * dirAB);

      // Sparkle at midpoint
      if (!sparkled) {
        var midX = Math.round((boundsA.cx + boundsB.cx) / 2);
        var midY = Math.round((boundsA.cy + boundsB.cy) / 2);
        ps.burst(midX, midY, 8);
        sparkled = true;
      }
    } else {
      // Drift back
      var release = (t - 0.7) / 0.3;
      dxA = Math.round(attractPx / 2 * (1 - release) * dirAB);
      dxB = Math.round(-attractPx / 2 * (1 - release) * dirAB);
    }

    _blankEntityPixels(buf, pixelsA);
    _blankEntityPixels(buf, pixelsB);
    _redrawEntityPixels(buf, PW, PH, pixelsA, dxA, 0);
    _redrawEntityPixels(buf, PW, PH, pixelsB, dxB, 0);

    // Draw small magnet indicators near each entity
    var magnetAlpha = _easeEnvelope(t, 0.1, 0.15);
    if (magnetAlpha > 0.05) {
      // Red pole near A, blue pole near B
      var mxA = boundsA.cx + dxA + 4 * dirAB;
      var myA = boundsA.cy - 5;
      var mxB = boundsB.cx + dxB - 4 * dirAB;
      var myB = boundsB.cy - 5;

      // Simple U-magnet shape: 3x4px
      var magnetA = [[255,60,60],[255,60,60],[255,60,60]];
      var magnetB = [[60,60,255],[60,60,255],[60,60,255]];
      for (var my = 0; my < 3; my++) {
        for (var mx = 0; mx < 3; mx++) {
          if (my === 1 && mx === 1) continue; // U-shape hollow
          _setPixel(buf, PW, PH,
            mxA + mx, myA + my,
            Math.round(magnetA[mx][0] * magnetAlpha), Math.round(magnetA[mx][1] * magnetAlpha), Math.round(magnetA[mx][2] * magnetAlpha));
          _setPixel(buf, PW, PH,
            mxB + mx, myB + my,
            Math.round(magnetB[mx][0] * magnetAlpha), Math.round(magnetB[mx][1] * magnetAlpha), Math.round(magnetB[mx][2] * magnetAlpha));
        }
      }
    }

    ps.update(1 / 60);
    ps.draw(buf, PW, PH);
  };
}, 1500);

// ── R2: Repel ──
// Two elements push apart from each other, like same-polarity magnets.
// Exact symmetric of Magnetism (R1).
// Scaffolds incorrect grouping — "A and B went home" but only A left.
AnimationTemplates.register('repel', function(params) {
  var prefixA = params.entityPrefixA || params.entityPrefix || '';
  var prefixB = params.entityPrefixB || '';
  var repelPx = _clamp(params.repelPixels || 12, 2, 25);
  var ps = new ParticleSystem(ParticlePresets.sparkle);
  var sparkled = false;

  return function animate(buf, PW, PH, t) {
    var boundsA = _computeEntityBounds(buf, PW, prefixA);
    var boundsB = prefixB ? _computeEntityBounds(buf, PW, prefixB) : null;
    if (!boundsB) return;

    var pixelsA = _collectEntityPixels(buf, PW, prefixA);
    var pixelsB = _collectEntityPixels(buf, PW, prefixB);

    var dirAB = boundsB.cx > boundsA.cx ? 1 : -1;
    var dxA = 0, dxB = 0;

    if (t < 0.1) {
      // Brief attract (tension)
      var attract = t / 0.1;
      dxA = Math.round(2 * attract * dirAB);
      dxB = Math.round(-2 * attract * dirAB);
    } else if (t < 0.4) {
      // Push apart
      var progress = (t - 0.1) / 0.3;
      dxA = Math.round(-repelPx / 2 * progress * dirAB);
      dxB = Math.round(repelPx / 2 * progress * dirAB);

      // Sparkle at midpoint
      if (!sparkled && t > 0.15) {
        var midX = Math.round((boundsA.cx + boundsB.cx) / 2);
        var midY = Math.round((boundsA.cy + boundsB.cy) / 2);
        ps.burst(midX, midY, 8);
        sparkled = true;
      }
    } else if (t < 0.7) {
      // Hold apart
      dxA = Math.round(-repelPx / 2 * dirAB);
      dxB = Math.round(repelPx / 2 * dirAB);
    } else {
      // Drift back
      var release = (t - 0.7) / 0.3;
      dxA = Math.round(-repelPx / 2 * (1 - release) * dirAB);
      dxB = Math.round(repelPx / 2 * (1 - release) * dirAB);
    }

    _blankEntityPixels(buf, pixelsA);
    _blankEntityPixels(buf, pixelsB);
    _redrawEntityPixels(buf, PW, PH, pixelsA, dxA, 0);
    _redrawEntityPixels(buf, PW, PH, pixelsB, dxB, 0);

    ps.update(1 / 60);
    ps.draw(buf, PW, PH);
  };
}, 1500);


// ── D1: Speech Bubble ──
// Pixelated speech bubble with "..." or keyword above character.
// Scaffolds dialogue and direct speech (linguistic_verbs).
AnimationTemplates.register('speech_bubble', function(params) {
  var prefix = params.entityPrefix || '';
  var text = params.text || '...';
  var bubbleColor = params.bubbleColor || [255, 255, 255];
  var textColor = params.textColor || [40, 40, 40];

  return function animate(buf, PW, PH, t) {
    var env = _easeEnvelope(t, 0.2, 0.2);
    if (env < 0.01) return;

    var bounds = _computeEntityBounds(buf, PW, prefix);
    if (bounds.x2 < 0) return;

    // Position bubble above entity
    var textLen = text.length;
    var bw = Math.max(24, textLen * 7 + 8);
    var bh = 14;
    var bx = Math.round(bounds.cx - bw / 2);
    var by = bounds.y1 - bh - 8;

    // Clamp to canvas
    bx = Math.max(1, Math.min(PW - bw - 1, bx));
    by = Math.max(1, by);

    var alpha = env;

    // Draw bubble background
    for (var y = by; y < by + bh; y++) {
      for (var x = bx; x < bx + bw; x++) {
        if (x >= 0 && x < PW && y >= 0 && y < PH) {
          var idx = y * PW + x;
          buf[idx].r = Math.round(buf[idx].r * (1 - alpha) + bubbleColor[0] * alpha);
          buf[idx].g = Math.round(buf[idx].g * (1 - alpha) + bubbleColor[1] * alpha);
          buf[idx].b = Math.round(buf[idx].b * (1 - alpha) + bubbleColor[2] * alpha);
        }
      }
    }

    // Draw bubble border
    for (var x = bx; x < bx + bw; x++) {
      _setPixel(buf, PW, PH, x, by, Math.round(120 * alpha), Math.round(120 * alpha), Math.round(120 * alpha));
      _setPixel(buf, PW, PH, x, by + bh - 1, Math.round(120 * alpha), Math.round(120 * alpha), Math.round(120 * alpha));
    }
    for (var y = by; y < by + bh; y++) {
      _setPixel(buf, PW, PH, bx, y, Math.round(120 * alpha), Math.round(120 * alpha), Math.round(120 * alpha));
      _setPixel(buf, PW, PH, bx + bw - 1, y, Math.round(120 * alpha), Math.round(120 * alpha), Math.round(120 * alpha));
    }

    // Draw tail (triangle pointing down to entity)
    var tailX = Math.round(bounds.cx);
    for (var td = 0; td < 4; td++) {
      if (tailX >= 0 && tailX < PW && by + bh + td >= 0 && by + bh + td < PH) {
        var ti = (by + bh + td) * PW + tailX;
        buf[ti].r = Math.round(buf[ti].r * (1 - alpha) + bubbleColor[0] * alpha);
        buf[ti].g = Math.round(buf[ti].g * (1 - alpha) + bubbleColor[1] * alpha);
        buf[ti].b = Math.round(buf[ti].b * (1 - alpha) + bubbleColor[2] * alpha);
      }
    }

    // Draw text inside bubble
    var tx = bx + 4;
    var ty = by + 3;
    drawText(buf, PW, PH, text, tx, ty,
      Math.round(textColor[0] * alpha), Math.round(textColor[1] * alpha), Math.round(textColor[2] * alpha),
      'temp.speech_bubble');
  };
}, 1500);

// ── D2: Thought Bubble ──
// Pixelated thought bubble (round, linked bubbles) with "..." or symbol.
// Scaffolds Internal Response and Plan (mental_verbs).
AnimationTemplates.register('thought_bubble', function(params) {
  var prefix = params.entityPrefix || '';
  var text = params.text || '...';
  var bubbleColor = params.bubbleColor || [240, 240, 255];
  var textColor = params.textColor || [60, 60, 80];

  return function animate(buf, PW, PH, t) {
    var env = _easeEnvelope(t, 0.25, 0.25);
    if (env < 0.01) return;

    var bounds = _computeEntityBounds(buf, PW, prefix);
    if (bounds.x2 < 0) return;

    var alpha = env;

    // Main thought bubble (ellipse)
    var textLen = text.length;
    var bw = Math.max(20, textLen * 7 + 10);
    var bh = 14;
    var bx = Math.round(bounds.cx - bw / 2 + 8);
    var by = bounds.y1 - bh - 12;
    bx = Math.max(1, Math.min(PW - bw - 1, bx));
    by = Math.max(1, by);

    // Draw main elliptical bubble
    var rx = bw / 2, ry = bh / 2;
    var cx = bx + rx, cy = by + ry;
    for (var y = by - 1; y <= by + bh; y++) {
      for (var x = bx - 1; x <= bx + bw; x++) {
        var dx = (x - cx) / rx, dy = (y - cy) / ry;
        if (dx * dx + dy * dy <= 1.0 && x >= 0 && x < PW && y >= 0 && y < PH) {
          var idx = y * PW + x;
          buf[idx].r = Math.round(buf[idx].r * (1 - alpha) + bubbleColor[0] * alpha);
          buf[idx].g = Math.round(buf[idx].g * (1 - alpha) + bubbleColor[1] * alpha);
          buf[idx].b = Math.round(buf[idx].b * (1 - alpha) + bubbleColor[2] * alpha);
        }
      }
    }

    // Draw trailing thought dots (3 small circles)
    var dotTrail = [
      { x: Math.round(bounds.cx + 2), y: by + bh + 3, r: 2 },
      { x: Math.round(bounds.cx - 1), y: by + bh + 7, r: 1.5 },
      { x: Math.round(bounds.cx - 3), y: by + bh + 10, r: 1 },
    ];
    for (var d = 0; d < dotTrail.length; d++) {
      var dot = dotTrail[d];
      for (var dy = -Math.ceil(dot.r); dy <= Math.ceil(dot.r); dy++) {
        for (var dx = -Math.ceil(dot.r); dx <= Math.ceil(dot.r); dx++) {
          if (dx * dx + dy * dy <= dot.r * dot.r) {
            var px = Math.round(dot.x + dx), py = Math.round(dot.y + dy);
            if (px >= 0 && px < PW && py >= 0 && py < PH) {
              var di = py * PW + px;
              buf[di].r = Math.round(buf[di].r * (1 - alpha) + bubbleColor[0] * alpha);
              buf[di].g = Math.round(buf[di].g * (1 - alpha) + bubbleColor[1] * alpha);
              buf[di].b = Math.round(buf[di].b * (1 - alpha) + bubbleColor[2] * alpha);
            }
          }
        }
      }
    }

    // Draw text inside bubble
    var tx = bx + 5;
    var ty = by + 3;
    drawText(buf, PW, PH, text, tx, ty,
      Math.round(textColor[0] * alpha), Math.round(textColor[1] * alpha), Math.round(textColor[2] * alpha),
      'temp.thought_bubble');
  };
}, 1500);

// ── D3: Alert ──
// "!" sprite above entity. Signals that an important event just happened
// or that the entity is reacting to something.
// Scaffolds Initiating Event (IE) and Internal Response (IR).
AnimationTemplates.register('alert', function(params) {
  var prefix = params.entityPrefix || '';
  var alertColor = params.alertColor || [255, 220, 50];
  var bgColor = params.bgColor || [200, 60, 60];

  return function animate(buf, PW, PH, t) {
    var env = _easeEnvelope(t, 0.15, 0.2);
    if (env < 0.01) return;

    var bounds = _computeEntityBounds(buf, PW, prefix);
    if (bounds.x2 < 0) return;

    // Pop-in scale effect
    var scale;
    if (t < 0.15) scale = t / 0.15 * 1.3;
    else if (t < 0.25) scale = 1.3 - (t - 0.15) / 0.1 * 0.3;
    else scale = 1;

    var alpha = env;

    // Position "!" above entity
    var exX = Math.round(bounds.cx);
    var exY = bounds.y1 - 14;

    // Draw red circle background
    var circR = Math.round(5 * scale);
    for (var dy = -circR; dy <= circR; dy++) {
      for (var dx = -circR; dx <= circR; dx++) {
        if (dx * dx + dy * dy <= circR * circR) {
          var px = exX + dx, py = exY + dy;
          if (px >= 0 && px < PW && py >= 0 && py < PH) {
            var idx = py * PW + px;
            buf[idx].r = Math.round(buf[idx].r * (1 - alpha) + bgColor[0] * alpha);
            buf[idx].g = Math.round(buf[idx].g * (1 - alpha) + bgColor[1] * alpha);
            buf[idx].b = Math.round(buf[idx].b * (1 - alpha) + bgColor[2] * alpha);
          }
        }
      }
    }

    // Draw "!" text
    drawText(buf, PW, PH, '!', exX - 2, exY - 3,
      Math.round(alertColor[0] * alpha), Math.round(alertColor[1] * alpha), Math.round(alertColor[2] * alpha),
      'temp.alert');

    // Gentle entity pulse
    var pulse = 1 + 0.12 * env * (0.5 + 0.5 * Math.sin(t * Math.PI * 5));
    for (var i = 0; i < buf.length; i++) {
      if (buf[i].e === prefix || buf[i].e.startsWith(prefix + '.')) {
        buf[i].r = Math.min(255, Math.round(buf[i]._r * pulse));
        buf[i].g = Math.min(255, Math.round(buf[i]._g * pulse));
        buf[i].b = Math.min(255, Math.round(buf[i]._b * pulse));
      }
    }
  };
}, 1200);

// ── D4: Interjection ──
// Comic-style burst displaying the problematic word with "?".
// The ONLY animation that displays text from the child's speech.
// Positioned at the top of the scene, centered. Size adapts to text length.
AnimationTemplates.register('interjection', function(params) {
  var word = params.word || '???';
  var burstColor = params.burstColor || [255, 240, 100];
  var textColor = params.textColor || [50, 30, 30];
  var borderColor = params.borderColor || [200, 80, 30];

  return function animate(buf, PW, PH, t) {
    var env = _easeEnvelope(t, 0.1, 0.25);
    if (env < 0.01) return;

    // Pop-in scale
    var scale;
    if (t < 0.1) scale = t / 0.1 * 1.2;
    else if (t < 0.2) scale = 1.2 - (t - 0.1) / 0.1 * 0.2;
    else scale = 1;

    var alpha = env;

    // Compute burst size based on text
    var displayText = word.toUpperCase() + '?';
    var textW = displayText.length * 7;
    var burstW = Math.round((textW + 16) * scale);
    var burstH = Math.round(20 * scale);

    // Center at top of scene
    var bx = Math.round(PW / 2 - burstW / 2);
    var by = 15;

    // Draw spiky burst background
    var cx = bx + burstW / 2, cy = by + burstH / 2;
    var spikes = 12;
    var innerR = Math.min(burstW, burstH) / 2 * 0.7;
    var outerR = Math.max(burstW, burstH) / 2 * 1.1;

    for (var y = by - Math.round(outerR); y <= by + burstH + Math.round(outerR); y++) {
      for (var x = bx - Math.round(outerR); x <= bx + burstW + Math.round(outerR); x++) {
        if (x < 0 || x >= PW || y < 0 || y >= PH) continue;
        var dx = x - cx, dy = y - cy;
        var dist = Math.sqrt(dx * dx + dy * dy);
        var angle = Math.atan2(dy, dx);
        // Spiky radius
        var spikeR = innerR + (outerR - innerR) * 0.5 * (1 + Math.cos(angle * spikes));
        if (dist <= spikeR) {
          var idx = y * PW + x;
          // Border: outer ring
          if (dist > spikeR - 2) {
            buf[idx].r = Math.round(buf[idx].r * (1 - alpha) + borderColor[0] * alpha);
            buf[idx].g = Math.round(buf[idx].g * (1 - alpha) + borderColor[1] * alpha);
            buf[idx].b = Math.round(buf[idx].b * (1 - alpha) + borderColor[2] * alpha);
          } else {
            buf[idx].r = Math.round(buf[idx].r * (1 - alpha) + burstColor[0] * alpha);
            buf[idx].g = Math.round(buf[idx].g * (1 - alpha) + burstColor[1] * alpha);
            buf[idx].b = Math.round(buf[idx].b * (1 - alpha) + burstColor[2] * alpha);
          }
        }
      }
    }

    // Draw text inside burst
    var tx = Math.round(cx - textW / 2);
    var ty = Math.round(cy - 3);
    drawText(buf, PW, PH, displayText, tx, ty,
      Math.round(textColor[0] * alpha), Math.round(textColor[1] * alpha), Math.round(textColor[2] * alpha),
      'temp.interjection');
  };
}, 1500);


// ═══════════════════════════════════════════════════════════════════
// Exports
// ═══════════════════════════════════════════════════════════════════

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { AnimationTemplates, ParticleSystem, ParticlePresets };
}

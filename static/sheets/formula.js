/* Salow Sheets formula engine. Runs on the device (browser or node); it reads only the cells it is
 * given. No eval, no Function, no network. Formulas are parsed by a hand-written recursive-descent
 * parser into a tree, then walked. Errors are values: #DIV/0!, #VALUE!, #NAME?, #REF!, #CIRC!, #PARSE!, #N/A (a lookup found nothing). */
(function (root) {
  "use strict";
  var MAX_LEN = 2000, MAX_DEPTH = 50, MAX_RANGE = 10000, MAX_ROW = 1000, MAX_COL = 26, MAX_STR = 10000, BUDGET = 500000, MAX_CELL_TEXT = 10000, MAX_DEPTH_EVAL = 400;

  // limitHits counts every #LIMIT! made. A cell whose evaluation made one is not cached (see get), so a work-budget stop never becomes a stored answer.
  var limitHits = 0, reqId = 0;  // reqId: one number per top-level request, for the per-request range memo
  function Err(code) { this.code = code; if (code === "#LIMIT!") limitHits++; }
  Err.prototype.toString = function () { return this.code; };
  var isErr = function (v) { return v instanceof Err; };

  function colNum(letters) { var n = 0; for (var i = 0; i < letters.length; i++) n = n * 26 + letters.charCodeAt(i) - 64; return n; }
  function addr(col, row) { return String.fromCharCode(64 + col) + row; }
  function parseAddr(s) {
    var m = /^([A-Z]{1,2})([1-9][0-9]{0,3})$/.exec(s);
    if (!m) return null;
    var c = colNum(m[1]), r = parseInt(m[2], 10);
    if (c > MAX_COL || r > MAX_ROW) return null;
    return { col: c, row: r };
  }

  function tokenize(src) {
    var t = [], i = 0, m;
    while (i < src.length) {
      var ch = src[i];
      if (/\s/.test(ch)) { i++; continue; }
      if ((m = /^[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?|^\.[0-9]+/.exec(src.slice(i)))) { var nv = parseFloat(m[0]); if (!isFinite(nv)) throw new Err("#VALUE!"); t.push({ k: "num", v: nv }); i += m[0].length; continue; }
      if (ch === '"') {
        var j = i + 1, s = "";
        while (j < src.length) {
          if (src[j] === '"') { if (src[j + 1] === '"') { s += '"'; j += 2; continue; } break; }
          s += src[j++];
        }
        if (j >= src.length) throw new Err("#PARSE!");
        t.push({ k: "str", v: s }); i = j + 1; continue;
      }
      if (ch === "'") { if ((m = /^'((?:[^']|'')+)'!/.exec(src.slice(i)))) { t.push({ k: "sheet", v: m[1].replace(/''/g, "'") }); i += m[0].length; continue; } throw new Err("#PARSE!"); }
      if ((m = /^([A-Za-z_][A-Za-z0-9_]*)!/.exec(src.slice(i)))) { t.push({ k: "sheet", v: m[1] }); i += m[0].length; continue; }
      if ((m = /^\$?([A-Za-z]{1,3})\$?([0-9]+)(?![A-Za-z0-9_.(])/.exec(src.slice(i)))) { t.push({ k: "id", v: (m[1] + m[2]).toUpperCase() }); i += m[0].length; continue; }
      if (src.substr(i, 5) === "#REF!") { t.push({ k: "id", v: "#REF!" }); i += 5; continue; }
      if ((m = /^[A-Za-z_][A-Za-z0-9_.]*/.exec(src.slice(i)))) { t.push({ k: "id", v: m[0].toUpperCase() }); i += m[0].length; continue; }
      if ((m = /^(<=|>=|<>|[-+*\/^&=<>(),:%])/.exec(src.slice(i)))) { t.push({ k: "op", v: m[0] }); i += m[0].length; continue; }
      throw new Err("#PARSE!");
    }
    return t;
  }

  // precedence low to high: comparison, concat, add, mul, pow, unary, percent, primary
  function parse(src) {
    if (typeof src !== "string" || src.length > MAX_LEN) throw new Err("#PARSE!");
    var t = tokenize(src), p = 0, depth = 0;
    var peek = function () { return t[p]; };
    var isOp = function (v) { return t[p] && t[p].k === "op" && t[p].v === v; };
    function enter() { if (++depth > MAX_DEPTH) throw new Err("#PARSE!"); }
    function cmp() {
      var l = cat();
      while (t[p] && t[p].k === "op" && /^(=|<>|<=|>=|<|>)$/.test(t[p].v)) { var o = t[p++].v; l = { n: "bin", o: o, l: l, r: cat() }; }
      return l;
    }
    function cat() { var l = add(); while (isOp("&")) { p++; l = { n: "bin", o: "&", l: l, r: add() }; } return l; }
    function add() { var l = mul(); while (isOp("+") || isOp("-")) { var o = t[p++].v; l = { n: "bin", o: o, l: l, r: mul() }; } return l; }
    function mul() { var l = pow(); while (isOp("*") || isOp("/")) { var o = t[p++].v; l = { n: "bin", o: o, l: l, r: pow() }; } return l; }
    function pow() { var l = unary(); while (isOp("^")) { p++; l = { n: "bin", o: "^", l: l, r: unary() }; } return l; }
    function unary() {
      if (isOp("-") || isOp("+")) { enter(); var o = t[p++].v; var e = { n: "un", o: o, e: unary() }; depth--; return e; }
      return pct();
    }
    function pct() { var e = primary(); while (isOp("%")) { p++; e = { n: "bin", o: "/", l: e, r: { n: "lit", v: 100 } }; } return e; }
    function primary() {
      var x = t[p++];
      if (!x) throw new Err("#PARSE!");
      if (x.k === "num" || x.k === "str") return { n: "lit", v: x.v };
      if (x.k === "op" && x.v === "(") { enter(); var e = cmp(); if (!isOp(")")) throw new Err("#PARSE!"); p++; depth--; return e; }
      if (x.k === "sheet") {
        var y0 = t[p++];
        if (!y0 || y0.k !== "id") throw new Err("#PARSE!");
        if (y0.v === "#REF!") return { n: "ref_err" };
        var qa = parseAddr(y0.v);
        if (!qa) { if (/^[A-Z]{1,3}[0-9]+$/.test(y0.v)) return { n: "ref_err" }; throw new Err("#NAME?"); }
        if (isOp(":")) {
          p++; var y1 = t[p++], qb = y1 && y1.k === "id" ? parseAddr(y1.v) : null;
          if (!qb) throw new Err("#REF!");
          return { n: "xrange", t: x.v, a: qa, b: qb };
        }
        return { n: "xcell", t: x.v, a: qa };
      }
      if (x.k === "id") {
        if (isOp("(")) {
          p++; enter(); var args = [];
          if (!isOp(")")) { for (;;) { args.push(cmp()); if (isOp(",")) { p++; continue; } break; } }
          if (!isOp(")")) throw new Err("#PARSE!");
          p++; depth--; return { n: "call", f: x.v, a: args };
        }
        if (x.v === "TRUE" || x.v === "FALSE") return { n: "lit", v: x.v === "TRUE" };
        if (x.v === "#REF!") return { n: "ref_err" };
        var a = parseAddr(x.v);
        if (!a) {
          if (/^[A-Z]{1,3}[0-9]+$/.test(x.v)) return { n: "ref_err" };  // looks like a cell, out of range
          throw new Err("#NAME?");
        }
        if (isOp(":")) {
          p++; var y = t[p++];
          var b = y && y.k === "id" ? parseAddr(y.v) : null;
          if (!b) throw new Err("#REF!");
          return { n: "range", a: a, b: b };
        }
        return { n: "cell", a: a };
      }
      throw new Err("#PARSE!");
    }
    var tree = cmp();
    if (p !== t.length) throw new Err("#PARSE!");
    return tree;
  }

  function num(v) {
    if (isErr(v)) return v;
    if (v === null || v === undefined || v === "") return 0;
    if (typeof v === "boolean") return v ? 1 : 0;
    if (typeof v === "number") return v;
    var s = String(v).trim();
    if (/^[-+]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][-+]?[0-9]+)?$/.test(s) && isFinite(parseFloat(s))) return parseFloat(s);
    return new Err("#VALUE!");
  }
  function text(v) {
    if (isErr(v)) return v;
    if (v === null || v === undefined) return "";
    if (typeof v === "boolean") return v ? "TRUE" : "FALSE";
    return String(v);
  }
  function flatten(args, out) {  // numbers from ranges ignore text and blanks, direct args must be numeric
    var nums = [];
    for (var i = 0; i < args.length; i++) {
      var a = args[i];
      if (Array.isArray(a)) {
        for (var j = 0; j < a.length; j++) {
          if (isErr(a[j])) return a[j];
          if (typeof a[j] === "number") nums.push(a[j]);
        }
      } else { var n = num(a); if (isErr(n)) return n; nums.push(n); }
    }
    return nums;
  }
  function roundHalfAway(x, d) {  // decimal-string shift, so 2.4999999995 -> 2 and 1.005 -> 1.01
    var ax = Math.abs(x), str = String(ax), f = Math.pow(10, d), out;
    if (str.indexOf("e") >= 0) { out = Math.round(ax * f) / f; return isFinite(out) ? (x < 0 ? -out : out) : x; }
    if (ax * f >= 9007199254740992) return x;  // no fractional digit left at this scale
    var up = Math.round(Number(str + "e" + d));
    out = Number(up + "e" + (-d));
    if (!isFinite(out)) return x;
    return x < 0 ? -out : out;
  }
  var FNX, OWN = Object.prototype.hasOwnProperty;
  var FN = {
    SUM: function (a) { var n = flatten(a); return isErr(n) ? n : n.reduce(function (s, x) { return s + x; }, 0); },
    AVERAGE: function (a) { var n = flatten(a); return isErr(n) ? n : n.length ? n.reduce(function (s, x) { return s + x; }, 0) / n.length : new Err("#DIV/0!"); },
    MIN: function (a) { var n = flatten(a); return isErr(n) ? n : n.length ? Math.min.apply(null, n) : 0; },
    MAX: function (a) { var n = flatten(a); return isErr(n) ? n : n.length ? Math.max.apply(null, n) : 0; },
    COUNT: function (a) { var c = 0; a.forEach(function (x) { if (Array.isArray(x)) x.forEach(function (y) { if (typeof y === "number") c++; }); else if (!isErr(num(x))) c++; }); return c; },
    ABS: function (a) { var x = num(a[0]); return isErr(x) ? x : Math.abs(x); },
    ROUND: function (a) { var x = num(a[0]), d = a.length > 1 ? num(a[1]) : 0; if (isErr(x)) return x; if (isErr(d)) return d; return roundHalfAway(x, Math.max(-15, Math.min(15, Math.trunc(d)))); },
    LEN: function (a) { var s = text(a[0]); return isErr(s) ? s : s.length; },
    UPPER: function (a) { var s = text(a[0]); return isErr(s) ? s : s.toUpperCase().slice(0, MAX_STR); },
    LOWER: function (a) { var s = text(a[0]); return isErr(s) ? s : s.toLowerCase(); },
    /* EXACT, PROPER, REPT, CHOOSE. Written from Google's help pages; edge cases are this engine's own choices, not checked against Google or Excel.
     * EXACT: true only when the two texts are identical, case included (numbers and booleans are compared as the text they show).
     * PROPER: lower-cases everything, then upper-cases every letter that starts the text or follows a character that is not a letter,
     * so "it's" becomes "It'S" and "3rd" becomes "3Rd". REPT: n is cut to a whole number; below 0 is #VALUE!; a result over the
     * 10,000 character text cap is #VALUE!; empty text gives "". CHOOSE: index cut to a whole number from 1; outside the values is #VALUE!;
     * the values are all evaluated first (an error in a value that was not chosen is not reported). */
    EXACT: function (a) { var x = text(a[0]), y = text(a[1]); if (isErr(x)) return x; if (isErr(y)) return y; return x === y; },
    PROPER: function (a) { var s = text(a[0]); if (isErr(s)) return s; return s.toLowerCase().replace(/(^|[^\p{L}])(\p{L})/gu, function (m, p, c) { return p + c.toUpperCase(); }).slice(0, MAX_STR); },
    REPT: function (a) {
      var s = text(a[0]), n = num(a[1]); if (isErr(s)) return s; if (isErr(n)) return n;
      n = Math.trunc(n); if (n < 0) return new Err("#VALUE!");
      if (s === "" || n === 0) return "";
      if (s.length * n > MAX_STR) return new Err("#VALUE!");
      return s.repeat(n);
    },
    CHOOSE: function (a) {
      var i = num(sc2(a[0])); if (isErr(i)) return i;
      i = Math.trunc(i); if (i < 1 || i > a.length - 1) return new Err("#VALUE!");
      var v = sc2(a[i]); return v === undefined ? null : v;
    },
    CONCAT: function (a) { var o = ""; for (var i = 0; i < a.length; i++) { var s = text(a[i]); if (isErr(s)) return s; o += s; if (o.length > MAX_STR) return new Err("#VALUE!"); } return o; },
    NOT: function (a) { var x = truth(a[0]); return isErr(x) ? x : !x; },
    AND: function (a) { for (var i = 0; i < a.length; i++) { var x = truth(a[i]); if (isErr(x)) return x; if (!x) return false; } return true; },
    OR: function (a) { for (var i = 0; i < a.length; i++) { var x = truth(a[i]); if (isErr(x)) return x; if (x) return true; } return false; }
  };

  /* ---- lookup and conditional functions ----
   * Written down here so they are exact. The Google Docs Editors help pages for INDEX, MATCH, VLOOKUP, SUMIF,
   * COUNTIF and AVERAGEIF were read for these functions; they do not say everything below (edge cases such as
   * the sum range shape, error cells, blanks and type mixing are this engine's own choices, not checked against
   * Google's or Excel's engine):
   * - Indices are 1-based. INDEX row or column 0 (a whole row or column) is not supported: #VALUE!. One index is the row, as in Google; on a range
   *   wider than one column it would mean a whole row, so it is #VALUE!.
   * - MATCH search_type: positive = 1 (default, ascending, largest <= key), 0 = exact, negative = -1
   *   (descending, smallest >= key). A range taller and wider than 1 gives #N/A. VLOOKUP is_sorted
   *   defaults to TRUE (approximate, ascending first column); FALSE is exact. Approximate modes scan
   *   top to bottom and stop at the first value past the key, like a sorted search, and skip blanks
   *   and values of another type than the key (numbers, text and booleans are not compared with each other).
   * - Exact text matches ignore case and accept the wildcards ? and * (~ escapes ? * and ~), also in
   *   COUNTIF, SUMIF and AVERAGEIF text criteria. A text key never equals a number cell and a number never equals a text cell
   *   in MATCH and VLOOKUP; the *IF functions also let a numeric string criterion match text of the same digits.
   * - Blank key: #N/A. An error value in the key is returned. An error cell in the searched range is skipped
   *   (never matches); an error in a returned cell is returned. Nothing found: #N/A. INDEX or VLOOKUP column
   *   past the range: #REF!. VLOOKUP index below 1 or not a number: #VALUE!.
   * - SUMIF and AVERAGEIF need a sum range of the same height and width as the range (it is not resized
   *   as Google and Excel do): otherwise #VALUE!. Only numbers are summed; text and blanks are ignored.
   * - Ragged data does not exist: a range is a full rectangle of cells, empty cells are blank. */
  function grid(v) { return Array.isArray(v) ? { v: v, rows: v.rows || v.length, cols: v.cols || 1 } : { v: [v], rows: 1, cols: 1 }; }
  function isBlank(x) { return x === null || x === undefined || x === ""; }
  var meter = null;  // set by the evaluator before each function call: charges comparison work to the sheet budget
  function wild(pat, str) {  // ? one character, * any run, ~ escapes; case-insensitive; no backtracking blow-up
    var toks = [], i;
    for (i = 0; i < pat.length; i++) {
      var ch = pat[i];
      if (ch === "~" && i + 1 < pat.length && (pat[i + 1] === "?" || pat[i + 1] === "*" || pat[i + 1] === "~")) { toks.push({ t: pat[++i] }); }
      else if (ch === "*") { if (!toks.length || toks[toks.length - 1].star !== true) toks.push({ star: true }); }
      else if (ch === "?") toks.push({ any: true });
      else toks.push({ t: ch });
    }
    if (meter && meter(Math.ceil(toks.length * (str.length + 1) / 50))) throw new Err("#LIMIT!");  // worst case of the scan below, charged at 1 unit per 50 steps (a sheet may do about 25 million)
    var sp = 0, tp = 0, star = -1, mark = 0;
    str = str.toLowerCase();
    var lit = function (k) { return String(toks[k].t).toLowerCase(); };
    while (sp < str.length) {
      if (tp < toks.length && !toks[tp].star && (toks[tp].any || lit(tp) === str[sp])) { sp++; tp++; }
      else if (tp < toks.length && toks[tp].star) { star = tp++; mark = sp; }
      else if (star >= 0) { tp = star + 1; sp = ++mark; }
      else return false;
    }
    while (tp < toks.length && toks[tp].star) tp++;
    return tp === toks.length;
  }
  function kind(x) { return typeof x === "number" ? "n" : typeof x === "boolean" ? "b" : typeof x === "string" && x !== "" ? "t" : null; }
  function cmpSame(a, b) {  // a and b are the same kind; -1, 0, 1
    if (typeof a === "string") { a = a.toLowerCase(); b = b.toLowerCase(); }
    return a < b ? -1 : a > b ? 1 : 0;
  }
  function wildTooLong() { throw new Err("#VALUE!"); }  // a text key over 255 characters, as for criteria
  function exactEq(key, cell) {
    var k = kind(key);
    if (k === null || k !== kind(cell)) return false;
    if (k === "t") return key.length > 255 ? wildTooLong() : wild(key, cell);
    return key === cell;
  }
  function criterion(c) {  // returns a predicate over one cell value, or an Err
    if (isErr(c)) return c;
    if (Array.isArray(c)) return new Err("#VALUE!");
    if (typeof c === "number") return function (x) { return typeof x === "number" && x === c; };
    if (typeof c === "boolean") return function (x) { return typeof x === "boolean" && x === c; };
    var s = text(c);
    if (s.length > 255) return new Err("#VALUE!");
    var op = "=", m = /^(>=|<=|<>|=|>|<)/.exec(s);
    if (m) { op = m[1]; s = s.slice(op.length); }
    var numeric = /^[-+]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][-+]?[0-9]+)?$/.test(s.trim()) && isFinite(parseFloat(s)), nv = numeric ? parseFloat(s) : 0;
    var low = s.toLowerCase();
    if (op === ">" || op === "<" || op === ">=" || op === "<=") {
      return function (x) {
        var c2;
        if (numeric) { if (typeof x !== "number") return false; c2 = x < nv ? -1 : x > nv ? 1 : 0; }
        else { if (kind(x) !== "t") return false; var xl = x.toLowerCase(); c2 = xl < low ? -1 : xl > low ? 1 : 0; }
        return op === ">" ? c2 > 0 : op === "<" ? c2 < 0 : op === ">=" ? c2 >= 0 : c2 <= 0;
      };
    }
    var eq;
    if (s === "") eq = function (x) { return isBlank(x); };
    else if (numeric) eq = function (x) { return (typeof x === "number" && x === nv) || (kind(x) === "t" && x.trim() === s.trim()); };
    else if (low === "true" || low === "false") eq = function (x) { return typeof x === "boolean" && x === (low === "true"); };
    else eq = function (x) { return kind(x) === "t" && wild(s, x); };
    return op === "<>" ? function (x) { return !eq(x); } : eq;
  }
  function pos(v) {  // a positive whole number from an argument, or an Err
    var n = num(sc2(v)); if (isErr(n)) return n;
    n = Math.trunc(n);
    return n < 1 ? new Err("#VALUE!") : n;
  }
  function sc2(v) { return Array.isArray(v) ? (v.length === 1 ? v[0] : new Err("#VALUE!")) : v; }
  var LOOKUP = {
    INDEX: function (a) {
      if (isErr(a[0])) return a[0];
      var g = grid(a[0]), r = pos(a[1]); if (isErr(r)) return r;
      var c = a.length > 2 ? pos(a[2]) : null; if (isErr(c)) return c;
      if (c === null) { if (g.cols > 1) return new Err("#VALUE!"); c = 1; }  // Google reads one index as the row and an omitted column as 0, a whole row; whole rows are not supported
      if (r > g.rows || c > g.cols) return new Err("#REF!");
      var v = g.v[(r - 1) * g.cols + (c - 1)];
      return v === undefined ? null : v;
    },
    MATCH: function (a) {
      var key = sc2(a[0]); if (isErr(key)) return key;
      if (isErr(a[1])) return a[1];
      var g = grid(a[1]); if (g.rows > 1 && g.cols > 1) return new Err("#N/A");
      var t = 1;
      if (a.length > 2) { t = num(sc2(a[2])); if (isErr(t)) return t; t = t > 0 ? 1 : t < 0 ? -1 : 0; }
      var i = find(key, g.v, 0, 1, g.v.length, t);
      return isErr(i) ? i : i + 1;
    },
    VLOOKUP: function (a) {
      var key = sc2(a[0]); if (isErr(key)) return key;
      if (isErr(a[1])) return a[1];
      var g = grid(a[1]), ci = pos(a[2]); if (isErr(ci)) return ci;
      var sorted = true;
      if (a.length > 3) { sorted = truth(sc2(a[3])); if (isErr(sorted)) return sorted; }
      if (ci > g.cols) return new Err("#REF!");
      var i = find(key, g.v, 0, g.cols, g.rows, sorted ? 1 : 0);
      if (isErr(i)) return i;
      var v = g.v[i * g.cols + (ci - 1)];
      return v === undefined ? null : v;
    },
    HLOOKUP: function (a) {  // VLOOKUP turned on its side: the key is searched along the first row, the answer comes from row index
      var key = sc2(a[0]); if (isErr(key)) return key;
      if (isErr(a[1])) return a[1];
      var g = grid(a[1]), ri = pos(a[2]); if (isErr(ri)) return ri;
      var sorted = true;
      if (a.length > 3) { sorted = truth(sc2(a[3])); if (isErr(sorted)) return sorted; }
      if (ri > g.rows) return new Err("#REF!");
      var i = find(key, g.v, 0, 1, g.cols, sorted ? 1 : 0);
      if (isErr(i)) return i;
      var v = g.v[(ri - 1) * g.cols + i];
      return v === undefined ? null : v;
    }
  };
  function find(key, vals, start, step, count, type) {  // index of the match among count cells, or #N/A
    if (kind(key) === null) return new Err("#N/A");
    var best = -1;
    for (var i = 0; i < count; i++) {
      var x = vals[start + i * step];
      if (isErr(x) || kind(x) !== kind(key)) continue;
      if (type === 0) { if (exactEq(key, x)) return i; continue; }
      var d = cmpSame(x, key);
      if (type === 1) { if (d > 0) break; best = i; }
      else { if (d < 0) break; best = i; }
    }
    return best < 0 ? new Err("#N/A") : best;
  }
  /* XLOOKUP(search_key, lookup_range, result_range, [missing_value], [match_mode], [search_mode]). Subset, written from Google's
   * help page (support.google.com/docs/answer/12405947); edge cases are this engine's own choices, not checked against Google or Excel.
   * - lookup_range must be one row or one column (else #VALUE!). result_range must be one row or one column of the same length;
   *   a result range with more than one row and column would need a spilled answer, which this sheet does not do: #VALUE!.
   * - match_mode 0 (default) exact, no wildcards, text ignores case; 1 exact or else the smallest value above the key; -1 exact or
   *   else the largest value below it; 2 wildcard (? * ~), as MATCH exact. Other numbers: #VALUE!.
   * - search_mode 1 (default) first to last, -1 last to first. 2 and -2 (binary search) are not supported: #VALUE!.
   * - Only cells of the same kind as the key (number, text, true/false) are compared; blanks and error cells are skipped.
   * - No match: missing_value if one was given, else #N/A. A blank key is not found. An error key is returned. An error in the returned cell is returned.
   *   missing_value is evaluated like any argument (not lazily).
   * - Approximate modes (1, -1): the closest value in order wins; equal candidates take the first one in search order; text is compared
   *   without case; true/false only match true/false. match_mode and search_mode are read as numbers, so TRUE is 1. An empty argument is a
   *   #PARSE! value from the parser. These are local choices checked only by the node table, not against Google or Excel. */
  function xlookup(a) {
    var key = sc2(a[0]); if (isErr(key)) return key;
    if (isErr(a[1])) return a[1];
    if (isErr(a[2])) return a[2];
    var lg = grid(a[1]), rg = grid(a[2]);
    if (lg.rows > 1 && lg.cols > 1) return new Err("#VALUE!");
    if (rg.rows > 1 && rg.cols > 1) return new Err("#VALUE!");
    var n = lg.v.length;
    if (rg.v.length !== n || (n > 1 && (lg.rows > 1) !== (rg.rows > 1))) return new Err("#VALUE!");
    var mm = 0, sm = 1;
    if (a.length > 4) { mm = num(sc2(a[4])); if (isErr(mm)) return mm; }
    if (a.length > 5) { sm = num(sc2(a[5])); if (isErr(sm)) return sm; }
    if ([0, 1, -1, 2].indexOf(mm) < 0 || (sm !== 1 && sm !== -1)) return new Err("#VALUE!");
    var miss = function () { return a.length > 3 ? sc2(a[3]) : new Err("#N/A"); };
    var kk = kind(key);
    if (kk === null) return miss();
    var best = -1, i, j, x, d, bd;
    for (j = 0; j < n; j++) {
      i = sm === 1 ? j : n - 1 - j;
      x = lg.v[i];
      if (isErr(x) || kind(x) !== kk) continue;
      if (mm === 2) { if (exactEq(key, x)) { best = i; break; } continue; }
      d = cmpSame(x, key);
      if (d === 0) { best = i; break; }
      if (mm === 0) continue;
      if (mm === 1 ? d < 0 : d > 0) continue;
      if (best < 0) { best = i; bd = x; continue; }
      if (mm === 1 ? cmpSame(x, bd) < 0 : cmpSame(x, bd) > 0) { best = i; bd = x; }
    }
    if (best < 0) return miss();
    var v = rg.v[best];
    return v === undefined ? null : v;
  }
  /* SUMPRODUCT, COUNTUNIQUE, TEXTJOIN. Written from Google's help pages (support.google.com/docs/answer/3094294, 3093405, 7013992); edge
   * cases are this engine's own choices, not checked against Google or Excel.
   * - SUMPRODUCT(array1, [array2 ...]): every argument must have the same rows and columns (a single value counts as 1 by 1), else #VALUE!.
   *   Entries that are not numbers (text, blanks, true/false) count as 0. An error entry is returned. One array: the sum of its numbers.
   * - COUNTUNIQUE(value1, ...): the number of different non-blank values over all values and ranges. Text is compared without case; the
   *   number 1 and the text "1" are different; true/false are their own values. An error in a range is returned.
   * - TEXTJOIN(delimiter, ignore_empty, text1, ...): joins values and ranges (ranges read row by row) with the delimiter. With ignore_empty true,
   *   blank cells and empty text are left out; with false they stay as empty pieces. A result over the 10,000 character cap is #VALUE!.
   *   Numbers and booleans are joined as the text they show. An error value is returned. */
  function sumproduct(a) {
    var gs = [], i, j, g;
    for (i = 0; i < a.length; i++) { if (isErr(a[i])) return a[i]; gs.push(grid(a[i])); }
    for (i = 1; i < gs.length; i++) if (gs[i].rows !== gs[0].rows || gs[i].cols !== gs[0].cols) return new Err("#VALUE!");
    var total = 0;
    for (j = 0; j < gs[0].v.length; j++) {
      var prod = 1;
      for (i = 0; i < gs.length; i++) {
        var x = gs[i].v[j];
        if (isErr(x)) return x;
        prod *= typeof x === "number" ? x : 0;
      }
      total += prod;
    }
    return total;
  }
  function countUnique(a) {
    var seen = {}, n = 0, i, j, g, x, key;
    for (i = 0; i < a.length; i++) {
      if (isErr(a[i])) return a[i];
      g = grid(a[i]);
      for (j = 0; j < g.v.length; j++) {
        x = g.v[j];
        if (isErr(x)) return x;
        if (isBlank(x)) continue;
        key = (typeof x === "number" ? "n:" : typeof x === "boolean" ? "b:" : "t:") + (typeof x === "string" ? x.toLowerCase() : String(x));
        if (!Object.prototype.hasOwnProperty.call(seen, key)) { seen[key] = 1; n++; }
      }
    }
    return n;
  }
  function textJoin(a) {
    var d = text(sc2(a[0])); if (isErr(d)) return d;
    var ig = truth(sc2(a[1])); if (isErr(ig)) return ig;
    var parts = [], len = 0, i, j, g, x, t;
    for (i = 2; i < a.length; i++) {
      if (isErr(a[i])) return a[i];
      g = grid(a[i]);
      for (j = 0; j < g.v.length; j++) {
        x = g.v[j];
        if (isErr(x)) return x;
        t = text(x);
        if (ig && t === "") continue;
        parts.push(t); len += t.length + d.length;
        if (len > MAX_STR + d.length) return new Err("#VALUE!");
      }
    }
    var out = parts.join(d);
    return out.length > MAX_STR ? new Err("#VALUE!") : out;
  }
  function condAgg(name, a) {
    if (isErr(a[0])) return a[0];
    var g = grid(a[0]), pred = criterion(a[1]); if (isErr(pred)) return pred;
    var sg = g;
    if (name !== "COUNTIF" && a.length > 2) { if (isErr(a[2])) return a[2]; sg = grid(a[2]); if (sg.rows !== g.rows || sg.cols !== g.cols) return new Err("#VALUE!"); }
    var count = 0, sum = 0;
    for (var i = 0; i < g.v.length; i++) {
      var x = g.v[i];
      if (isErr(x) || !pred(x)) continue;
      count++;
      if (name !== "COUNTIF") { var y = sg.v[i]; if (isErr(y)) return y; if (typeof y === "number") { sum += y; } else if (name === "AVERAGEIF") count--; }
    }
    if (name === "COUNTIF") return count;
    if (name === "SUMIF") return sum;
    return count ? sum / count : new Err("#DIV/0!");
  }
  FNX = { SUMPRODUCT: sumproduct, COUNTUNIQUE: countUnique, TEXTJOIN: textJoin, NA: function () { return new Err("#N/A"); }, XLOOKUP: xlookup, INDEX: LOOKUP.INDEX, MATCH: LOOKUP.MATCH, VLOOKUP: LOOKUP.VLOOKUP, HLOOKUP: LOOKUP.HLOOKUP,
    SUMIF: function (a) { return condAgg("SUMIF", a); }, COUNTIF: function (a) { return condAgg("COUNTIF", a); }, AVERAGEIF: function (a) { return condAgg("AVERAGEIF", a); } };

  /* ---- text functions (Google Sheets help semantics, not its engine) ----
   * Google's help pages for LEFT, RIGHT, MID, TRIM, SUBSTITUTE and FIND were read. They cover the defaults, FIND's case
   * sensitivity and #VALUE! when not found, and TRIM keeping non-breaking spaces. Negative counts, an empty search text,
   * a FIND start past the end, and SUBSTITUTE being case-sensitive with no wildcards are this engine's choices.
   * Counts are whole numbers of UTF-16 units, like LEN: an emoji counts 2. A count below 0 is #VALUE!; a
   * fraction is cut toward 0. LEFT and RIGHT default to 1. MID start is 1-based, below 1 is #VALUE!, past the
   * end gives "". TRIM removes leading and trailing spaces and collapses runs of spaces (only the space
   * character). SUBSTITUTE is case-sensitive and does not use wildcards; an empty old text changes nothing; the
   * optional instance picks the nth occurrence (below 1 is #VALUE!). FIND is case-sensitive, has no wildcards,
   * start is 1-based, an empty search text gives start, and not found is #VALUE! (as in Sheets). */
  function cnt(v, dflt) { if (v === undefined) return dflt; var n = num(sc2(v)); if (isErr(n)) return n; n = Math.trunc(n); return n < 0 ? new Err("#VALUE!") : n; }
  function tx(v) { return text(sc2(v)); }
  FNX.LEFT = function (a) { var s = tx(a[0]), n = cnt(a[1], 1); return isErr(s) ? s : isErr(n) ? n : s.slice(0, n); };
  FNX.RIGHT = function (a) { var s = tx(a[0]), n = cnt(a[1], 1); return isErr(s) ? s : isErr(n) ? n : n === 0 ? "" : s.slice(-n); };
  FNX.MID = function (a) {
    var s = tx(a[0]), st = num(sc2(a[1])), n = cnt(a[2], 0);
    if (isErr(s)) return s; if (isErr(st)) return st; if (isErr(n)) return n;
    st = Math.trunc(st); if (st < 1) return new Err("#VALUE!");
    return s.substr(st - 1, n);
  };
  FNX.TRIM = function (a) { var s = tx(a[0]); return isErr(s) ? s : s.replace(/ +/g, " ").replace(/^ | $/g, ""); };
  FNX.SUBSTITUTE = function (a) {
    var s = tx(a[0]), o = tx(a[1]), r = tx(a[2]);
    if (isErr(s)) return s; if (isErr(o)) return o; if (isErr(r)) return r;
    var inst = null;
    if (a.length > 3) { inst = num(sc2(a[3])); if (isErr(inst)) return inst; inst = Math.trunc(inst); if (inst < 1) return new Err("#VALUE!"); }
    if (o === "") return s;
    var out = "", i = 0, k = 0, j;
    while ((j = s.indexOf(o, i)) >= 0) {
      k++;
      if (inst === null || k === inst) { out += s.slice(i, j) + r; } else { out += s.slice(i, j + o.length); }
      i = j + o.length;
      if (inst !== null && k === inst) break;
      if (out.length > MAX_STR) return new Err("#VALUE!");
    }
    out += s.slice(i);
    return out.length > MAX_STR ? new Err("#VALUE!") : out;
  };
  FNX.FIND = function (a) {
    var f = tx(a[0]), s = tx(a[1]), st = a.length > 2 ? num(sc2(a[2])) : 1;
    if (isErr(f)) return f; if (isErr(s)) return s; if (isErr(st)) return st;
    st = Math.trunc(st); if (st < 1 || st > s.length + 1) return new Err("#VALUE!");
    var j = s.indexOf(f, st - 1);
    return j < 0 ? new Err("#VALUE!") : j + 1;
  };

  /* ---- date functions on serial numbers (deterministic: no TODAY, NOW or clock) ----
   * The Google Docs Editors help pages for DATE, YEAR, MONTH, DAY and WEEKDAY were read. Serial 0 is 1899-12-30 and
   * serial 1 is 1899-12-31, so DATE(1900,1,1) is 2 and DATE(1970,1,1) is 25569. Excel's 1900 system treats 1900 as a leap
   * year (Microsoft: https://learn.microsoft.com/en-us/troubleshoot/microsoft-365-apps/excel/wrongly-assumes-1900-is-leap-year
   * and https://support.microsoft.com/en-us/excel/date-systems-in-excel), so serials differ by a day before 1900-03-01; that is not modelled. A fraction (a time of day) is dropped
   * by rounding the serial down. All arithmetic is UTC, so the browser's time zone and daylight saving never matter.
   * DATE: year, month and day must be numbers (text is #VALUE!, unlike the numeric-text coercion elsewhere in this engine);
   * decimals are cut toward 0; month and day outside their range roll over (DATE(1969,13,1) is 1970-01-01); a year
   * 0 to 1899 has 1900 added; a year below 0, or a result outside the years 0 to 9999, is #NUM!. YEAR, MONTH and DAY
   * take a serial number (the engine has no date type, and a typed date string is just text, as in Google's help);
   * a serial outside the years 0 to 9999 is #NUM!. WEEKDAY types 1, 2, 3 and 11 to 17 as in Google's help; any other
   * type is #NUM!. #NUM! is a new error code here. Not built: DATEVALUE, EDATE, EOMONTH, DATEDIF, TEXT and date formats. */
  var DAY_MS = 86400000, BASE_MS = Date.UTC(1899, 11, 30);
  function serialDate(n) {
    var d = Math.floor(n), t = BASE_MS + d * DAY_MS, dt = new Date(t);
    if (!isFinite(t) || isNaN(dt.getTime())) return null;
    var y = dt.getUTCFullYear();
    return y < 0 || y > 9999 ? null : dt;
  }
  function dnum(v) { v = sc2(v); if (isErr(v)) return v; if (typeof v === "string" && v !== "") return new Err("#VALUE!"); var n = num(v); return n; }
  function dpart(a, pick) { var n = dnum(a[0]); if (isErr(n)) return n; var dt = serialDate(n); return dt ? pick(dt) : new Err("#NUM!"); }
  FNX.DATE = function (a) {
    var y = dnum(a[0]), m = dnum(a[1]), d = dnum(a[2]);
    if (isErr(y)) return y; if (isErr(m)) return m; if (isErr(d)) return d;
    y = Math.trunc(y); m = Math.trunc(m); d = Math.trunc(d);
    if (y < 0 || y > 9999) return new Err("#NUM!");
    if (y < 1900) y += 1900;
    if (Math.abs(m) > 120000 || Math.abs(d) > 4000000) return new Err("#NUM!");
    var dt = new Date(0); dt.setUTCFullYear(y, m - 1, d); dt.setUTCHours(0, 0, 0, 0);
    var yy = dt.getUTCFullYear();
    if (isNaN(dt.getTime()) || yy < 0 || yy > 9999) return new Err("#NUM!");
    return Math.round((dt.getTime() - BASE_MS) / DAY_MS);
  };
  FNX.YEAR = function (a) { return dpart(a, function (dt) { return dt.getUTCFullYear(); }); };
  FNX.MONTH = function (a) { return dpart(a, function (dt) { return dt.getUTCMonth() + 1; }); };
  FNX.DAY = function (a) { return dpart(a, function (dt) { return dt.getUTCDate(); }); };
  FNX.WEEKDAY = function (a) {
    var ty = a.length > 1 ? dnum(a[1]) : 1;
    if (isErr(ty)) return ty;
    ty = Math.trunc(ty);
    var first = { 1: 0, 2: 1, 3: 1, 11: 1, 12: 2, 13: 3, 14: 4, 15: 5, 16: 6, 17: 0 };  // JS day number (Sunday 0) that starts the week
    var n = dnum(a[0]); if (isErr(n)) return n;
    if (!OWN.call(first, ty)) return new Err("#NUM!");
    var dt = serialDate(n); if (!dt) return new Err("#NUM!");
    var k = (dt.getUTCDay() - first[ty] + 7) % 7;
    return ty === 3 ? k : k + 1;
  };

  /* ---- math functions ----
   * The Google Docs Editors help pages for MOD, POWER, SQRT, FLOOR, CEILING and INT were read. Numbers: numeric text is
   * accepted as elsewhere in this engine; a range of more than one cell is #VALUE!.
   * MOD takes the sign of the divisor (MOD(-1,3) is 2) and a zero divisor is #DIV/0!. With two whole numbers the result is the
   * exact remainder of the two doubles, with no cleaning (MOD(10000000000000,7) is 3, MOD(9007199254740991,2) is 1,
   * MOD(1700000000001,1000) is 1). With a non-whole operand a remainder within 4 ulps of the dividend of 0 or of the divisor
   * is 0 (so MOD(0.3,0.1) is 0), because it is the noise of a quotient that is really whole; other remainders keep float noise
   * from the subtraction (MOD(5.1,1) is 0.0999999999999996), so ROUND them as the page says. A tiny remainder of a tiny
   * number (MOD(1e-20,3e-20)) is kept: the cutoff scales with the dividend, not with a fixed size.
   * POWER: 0 to a negative power is #DIV/0!, a negative base with a fractional exponent or an overflow is #NUM!; 0 to the power
   * 0 is 1 (not on the page, my choice). The ^ operator is older and stays as it was: the same results, but #VALUE! for all
   * three error cases (0^-1, (-8)^0.5, 10^400). SQRT of a negative is #NUM!. INT rounds down, so INT(-1.5) is -2.
   * FLOOR and CEILING: factor defaults to 1; a zero factor is #DIV/0!. A negative factor is #NUM! in this engine: the pages
   * describe it in two ways and I did not verify the direction rules, so it is left out rather than guessed. FLOOR(v,f) is the
   * largest multiple of f at or below v, CEILING the smallest at or above. With two whole numbers the result is exact
   * (FLOOR(1234567890123456,1) is itself). Otherwise the quotient within 2 ulps of a whole number is taken as that whole
   * number (only below 2^40) and the result is cleaned to 15 significant digits (16 or more digits are kept); from a quotient of 2^40 or more the plain product is returned, with no cleaning. MOD applies its noise rule only where the quotient is below 2^40, so FLOOR(23.25,0.18)
   * is 23.22 and CEILING(0.1+0.2,0.1) is 0.3 (plain floating point gives 0.4). Not built: ROUNDUP, ROUNDDOWN, MROUND, EXP, LN, LOG. */
  function mn(v) { v = sc2(v); if (isErr(v)) return v; return num(v); }
  function clean(x) { return !isFinite(x) ? new Err("#NUM!") : Math.abs(x) >= 1e15 ? x : Number(x.toPrecision(15)); }  // 15 digits, but a number of 16 digits or more is kept exactly
  function snap(q) { var n = Math.round(q); return Math.abs(q - n) <= 2 * Number.EPSILON * Math.abs(q) ? n : q; }  // a quotient within 2 ulps of a whole number is that whole number
  function fixSign(r, y) { return r !== 0 && (r < 0) !== (y < 0) ? r + y : r; }  // JS % takes the sign of the dividend, MOD of the divisor
  FNX.MOD = function (a) {
    var x = mn(a[0]), y = mn(a[1]); if (isErr(x)) return x; if (isErr(y)) return y;
    if (y === 0) return new Err("#DIV/0!");
    var r = x % y;  // exact for the two doubles
    if (Number.isInteger(x) && Number.isInteger(y)) return fixSign(r, y) + 0;  // whole numbers: the exact remainder, no cleaning at all
    // not whole numbers: a remainder within 4 ulps of 0 or of the divisor is the noise of a quotient that is really a whole number
    var tol = 4 * Number.EPSILON * Math.abs(x);
    if (Math.abs(x / y) < 1099511627776 && (Math.abs(r) <= tol || Math.abs(Math.abs(y) - Math.abs(r)) <= tol)) return 0;  // only where the quotient is small enough to have fraction digits: MOD(1e15+0.5,1) is 0.5
    return clean(fixSign(r, y));
  };
  FNX.POWER = function (a) {
    var x = mn(a[0]), y = mn(a[1]); if (isErr(x)) return x; if (isErr(y)) return y;
    if (x === 0 && y < 0) return new Err("#DIV/0!");
    var r = Math.pow(x, y);
    return isFinite(r) ? r : new Err("#NUM!");
  };
  FNX.SQRT = function (a) { var x = mn(a[0]); if (isErr(x)) return x; return x < 0 ? new Err("#NUM!") : Math.sqrt(x); };
  FNX.INT = function (a) { var x = mn(a[0]); return isErr(x) ? x : Math.floor(x); };
  function stepFn(name, fn) {
    FNX[name] = function (a) {
      var x = mn(a[0]), f = a.length > 1 ? mn(a[1]) : 1; if (isErr(x)) return x; if (isErr(f)) return f;
      if (f === 0) return new Err("#DIV/0!");
      if (f < 0) return new Err("#NUM!");
      var raw = x / f; if (!isFinite(raw)) return new Err("#NUM!");
      if (Number.isInteger(x) && Number.isInteger(f)) { var ri = fn(raw) * f; return isFinite(ri) ? ri : new Err("#NUM!"); }  // whole numbers: no cleaning
      if (Math.abs(raw) >= 1099511627776) { var rl = fn(raw) * f; return isFinite(rl) ? rl : new Err("#NUM!"); }  // a quotient of 2^40 or more: no snapping, no cleaning
      return clean(fn(snap(raw)) * f);  // a smaller quotient has at most 12 whole digits, so a 15-digit result keeps the right multiple
    };
  }
  stepFn("FLOOR", Math.floor); stepFn("CEILING", Math.ceil);
  /* ---- statistical functions ----
   * The Google Docs Editors help pages for MEDIAN (3094025), COUNTA (3093991), COUNTBLANK (3093403) and STDEV (3094054) were read.
   * Like SUM and AVERAGE here, a direct cell reference counts as a reference. MEDIAN and STDEV take numbers only: text and blanks in a
   * range are ignored (the MEDIAN page says text is ignored), a direct argument that is not numeric is #VALUE!, an error cell is
   * returned. MEDIAN of an even count is the mean of the two middle values (page); of nothing it is #NUM! (my choice, not on the page).
   * STDEV is the sample standard deviation (n - 1), computed in two passes with a corrected mean; fewer than two numbers is #DIV/0! (my choice, not on the page).
   * COUNTA counts every non-empty cell of a range, text included, and zero-length text (page); an error cell counts as one value (my choice).
   * A direct argument counts as one. COUNTBLANK counts the empty cells of a range and cells holding zero-length text; a direct argument
   * that is not a range or cell is #VALUE! (my choice). Not built: STDEVP, STDEVA, VAR, MODE, QUARTILE, PERCENTILE, COUNTUNIQUE. */
  function numsOf(a) {
    var n = flatten(a); if (isErr(n)) return n;
    if (meter && meter(n.length)) throw new Err("#LIMIT!");
    return n;
  }
  FNX.MEDIAN = function (a) {
    var n = numsOf(a); if (isErr(n)) return n;
    if (!n.length) return new Err("#NUM!");
    n.sort(function (p, q) { return p - q; });
    var m = n.length >> 1;
    return n.length % 2 ? n[m] : (n[m - 1] + n[m]) / 2;
  };
  FNX.STDEV = function (a) {
    var n = numsOf(a); if (isErr(n)) return n;
    if (n.length < 2) return new Err("#DIV/0!");
    var mean = 0, i, ss = 0;
    for (i = 0; i < n.length; i++) mean += n[i];
    mean /= n.length;
    var corr = 0; for (i = 0; i < n.length; i++) corr += n[i] - mean;
    mean += corr / n.length;  // corrected two-pass: STDEV(0.1,0.1,0.1) is 0, not 1e-17
    for (i = 0; i < n.length; i++) ss += (n[i] - mean) * (n[i] - mean);
    var r = Math.sqrt(ss / (n.length - 1));
    return isFinite(r) ? r : new Err("#NUM!");
  };
  FNX.COUNTA = function (a) {
    var c = 0;
    a.forEach(function (x) { if (Array.isArray(x)) x.forEach(function (y) { if (y !== null && y !== undefined) c++; }); else c++; });
    return c;
  };
  FNX.COUNTBLANK = function (a) {
    var c = 0;
    for (var i = 0; i < a.length; i++) {
      if (!Array.isArray(a[i])) return new Err("#VALUE!");
      a[i].forEach(function (y) { if (y === null || y === undefined || y === "") c++; });
    }
    return c;
  };
  /* ---- multi-criteria aggregates, rounding direction, sign, rank and type tests, IFERROR ----
   * SUMIFS(sum_range, range1, criterion1, ...), AVERAGEIFS the same, COUNTIFS(range1, criterion1, ...): every pair must
   * match, all ranges must be the same size (else #VALUE!), criteria use the same rules as COUNTIF (operators, wildcards,
   * 255 limit); cells holding an error never match. At most 10 pairs. ROUNDUP and ROUNDDOWN go away from and toward 0 on the
   * decimal digits as written (1.1 to 1 place stays 1.1); TRUNC is ROUNDDOWN. SIGN is -1, 0 or 1. PRODUCT multiplies numbers in
   * ranges (text and blanks ignored; no numbers gives 0). LARGE and SMALL take k as a whole number from 1; k past the count of
   * numbers is #NUM!. ISNUMBER, ISTEXT, ISBLANK, ISERROR look at one value (a multi-cell range is #VALUE!). ISBLANK is true only
   * for an empty cell; "" typed as a formula result counts as blank here as the cell value is empty text. IFERROR is evaluated
   * lazily like IF: the second argument (default "") runs only when the first is an error. */
  function multiCrit(name, a) {
    var first = name === "COUNTIFS" ? 0 : 1;
    if ((a.length - first) % 2 !== 0 || a.length - first < 2 || (a.length - first) / 2 > 10) return new Err("#VALUE!");
    var sg = null, g0 = null, preds = [], grids = [], i;
    if (first) { if (isErr(a[0])) return a[0]; sg = grid(a[0]); }
    for (i = first; i < a.length; i += 2) {
      if (isErr(a[i])) return a[i];
      var g = grid(a[i]); if (!g0) g0 = g;
      if (g.rows !== g0.rows || g.cols !== g0.cols) return new Err("#VALUE!");
      var pr = criterion(a[i + 1]); if (isErr(pr)) return pr;
      preds.push(pr); grids.push(g);
    }
    if (sg && (sg.rows !== g0.rows || sg.cols !== g0.cols)) return new Err("#VALUE!");
    var count = 0, sum = 0, best = null;
    for (i = 0; i < g0.v.length; i++) {
      var ok = true;
      for (var k = 0; k < preds.length; k++) { var x = grids[k].v[i]; if (isErr(x) || !preds[k](x)) { ok = false; break; } }
      if (!ok) continue;
      if (!sg) { count++; continue; }
      var y = sg.v[i]; if (isErr(y)) return y;
      if (typeof y === "number") {
        sum += y; count++;
        if (best === null || (name === "MAXIFS" ? y > best : y < best)) best = y;
      }
    }
    if (name === "MAXIFS" || name === "MINIFS") return best === null ? 0 : best;  // nothing numeric matched: 0, as MAX and MIN of no numbers
    if (name === "COUNTIFS") return count;
    if (name === "SUMIFS") return sum;
    return count ? sum / count : new Err("#DIV/0!");
  }
  FNX.SUMIFS = function (a) { return multiCrit("SUMIFS", a); };
  FNX.COUNTIFS = function (a) { return multiCrit("COUNTIFS", a); };
  FNX.AVERAGEIFS = function (a) { return multiCrit("AVERAGEIFS", a); };
  FNX.MAXIFS = function (a) { return multiCrit("MAXIFS", a); };
  FNX.MINIFS = function (a) { return multiCrit("MINIFS", a); };
  function shift10(v, d) {  // v * 10^d on the decimal digits, also for exponent forms such as 1e-7 or 1e+25
    var t = String(v), m = t, e = 0, i = t.indexOf("e");
    if (i >= 0) { m = t.slice(0, i); e = parseInt(t.slice(i + 1), 10); }
    return Number(m + "e" + (e + d));
  }
  function roundDir(a, up) {
    var x = num(sc2(a[0])), d = a.length > 1 ? num(sc2(a[1])) : 0;
    if (isErr(x)) return x; if (isErr(d)) return d;
    d = Math.max(-15, Math.min(15, Math.trunc(d)));
    var ax = Math.abs(x), sc = shift10(ax, d);
    if (!isFinite(sc)) return x;  // only above about 1e293 with a positive digit count: no fraction digit left, so unchanged
    var r = up ? Math.ceil(sc) : Math.floor(sc);
    var out = shift10(r, -d);
    if (!isFinite(out)) return x;
    return out === 0 ? 0 : x < 0 ? -out : out;
  }
  FNX.ROUNDUP = function (a) { return roundDir(a, true); };
  FNX.ROUNDDOWN = function (a) { return roundDir(a, false); };
  FNX.TRUNC = function (a) { return roundDir(a, false); };
  FNX.SIGN = function (a) { var x = num(sc2(a[0])); return isErr(x) ? x : x > 0 ? 1 : x < 0 ? -1 : 0; };
  FNX.PRODUCT = function (a) { var n = flatten(a); return isErr(n) ? n : n.length ? n.reduce(function (s, x) { return s * x; }, 1) : 0; };
  function nth(a, big) {
    if (isErr(a[0])) return a[0];
    var k = pos(a[1]); if (isErr(k)) return k;
    var n = flatten([a[0]]); if (isErr(n)) return n;
    if (k > n.length) return new Err("#NUM!");
    n.sort(function (p, q) { return big ? q - p : p - q; });
    return n[k - 1];
  }
  FNX.LARGE = function (a) { return nth(a, true); };
  FNX.SMALL = function (a) { return nth(a, false); };
  function one(v) { return Array.isArray(v) ? (v.length === 1 ? v[0] : undefined) : v; }
  FNX.ISNUMBER = function (a) { var v = one(a[0]); return v === undefined ? new Err("#VALUE!") : typeof v === "number"; };
  FNX.ISTEXT = function (a) { var v = one(a[0]); return v === undefined ? new Err("#VALUE!") : typeof v === "string" && v !== ""; };
  FNX.ISBLANK = function (a) { var v = one(a[0]); return v === undefined ? new Err("#VALUE!") : v === null || v === ""; };
  FNX.ISERROR = function (a) { var v = one(a[0]); return v === undefined ? new Err("#VALUE!") : isErr(v); };

  /* ---- batch 1 of added functions: maths, angles, a few text functions, XOR ----
   * Argument order and the written rules were read from the Google Docs Editors Help pages for REPLACE (3098247), SEARCH (3094154), ATAN2 (3093468),
   * GCD (3093489), LCM (3093421), FACT (3093412), EVEN (3093409), ODD (3093499), XOR (9116491), CHAR (3094120), CODE (3094122), SUMSQ (3093714),
   * CONCATENATE (3094123) and DIVIDE, which says it equals QUOTIENT (3093973). Each page is https://support.google.com/docs/answer/<number>?hl=en (read 2026-11-28). Those pages do not give domain errors, negative inputs, size limits
   * or wildcard rules. The choices below are this engine's own, not checked against Google's or Excel's engine, and the engine has only the error
   * codes it already has: a bad input is #VALUE! (a division by zero is #DIV/0!). Not a conformance claim.
   * - A range given where one value is wanted is #VALUE! unless it holds one cell. A range is read left to right, row by row.
   * - Angles are radians. PI is the full double, not 9 places. ASIN and ACOS outside -1..1 are #VALUE!. ATAN2(x, y) with both 0 is #DIV/0!.
   * - REPLACE(text, position, length, new_text): position and length are cut to whole numbers; position below 1 or length below 0 is #VALUE!;
   *   a position past the end appends. Counts are UTF-16 units like LEN.
   * - SEARCH(search_for, text, [start]): ignores case (text and pattern are lower-cased one code point at a time, positions are in the original text in UTF-16 units; a dotted
   *   capital I matches nothing that is a plain i, and a start that falls inside a pair of units is taken as is; the final sigma and sigma are different letters here), no wildcards, start defaults to 1; a start below 1 or past the end, or no match, is #VALUE!;
   *   empty search_for finds the start.
   * - CHAR(n): n cut to a whole number; 1 to 1114111 except the surrogate range, else #VALUE!. CODE gives the first code point; empty text is #VALUE!.
   * - QUOTIENT(a, b): the whole part of a / b cut toward 0; a quotient too large to hold (1E300 / 1E-300) is #VALUE!. GCD and LCM: values cut to whole numbers, a negative one is #VALUE!, text and blanks
   *   in a range are skipped (so GCD over only text cells is 0, while LCM and XOR over only text cells are #VALUE!: GCD starts from 0, the others have no value to use), a result beyond 9007199254740991 is #VALUE!; GCD of only zeros is 0, an LCM including 0 is 0.
   * - FACT: n cut to a whole number; below 0 or above 170 is #VALUE!. SUMSQ skips text and blanks in ranges; a direct text argument must be numeric.
   * - EVEN and ODD round away from 0 to the next even or odd whole number (EVEN(0) is 0, ODD(0) is 1).
   * - XOR: TRUE when an odd number of its logical values are true. In a range only numbers and booleans count; text and blanks are skipped. With no
   *   logical value at all it is #VALUE!. */
  function sn(v) { var x = one(v); return x === undefined ? new Err("#VALUE!") : num(x); }
  function stx(v) { var x = one(v); return x === undefined ? new Err("#VALUE!") : text(x); }
  function whole(a, i, dflt) { if (i >= a.length) return dflt; var x = sn(a[i]); return isErr(x) ? x : Math.trunc(x); }
  function fin(v) { return isFinite(v) ? v : new Err("#VALUE!"); }
  function trig(f, dom) { return function (a) { var x = sn(a[0]); if (isErr(x)) return x; if (dom && (x < -1 || x > 1)) return new Err("#VALUE!"); return fin(f(x)); }; }
  FNX.PI = function () { return Math.PI; };
  FNX.SIN = trig(Math.sin); FNX.COS = trig(Math.cos); FNX.TAN = trig(Math.tan); FNX.ATAN = trig(Math.atan);
  FNX.ASIN = trig(Math.asin, true); FNX.ACOS = trig(Math.acos, true);
  FNX.DEGREES = trig(function (x) { return x * 180 / Math.PI; }); FNX.RADIANS = trig(function (x) { return x * Math.PI / 180; });
  FNX.ATAN2 = function (a) { var x = sn(a[0]), y = sn(a[1]); if (isErr(x)) return x; if (isErr(y)) return y; if (x === 0 && y === 0) return new Err("#DIV/0!"); return Math.atan2(y, x); };
  FNX.REPLACE = function (a) {
    var t = stx(a[0]), n = whole(a, 1), l = whole(a, 2), r = stx(a[3]);
    if (isErr(t)) return t; if (isErr(n)) return n; if (isErr(l)) return l; if (isErr(r)) return r;
    if (n < 1 || l < 0) return new Err("#VALUE!");
    var out = t.slice(0, n - 1) + r + t.slice(n - 1 + l);
    return out.length > MAX_STR ? new Err("#VALUE!") : out;
  };
  function foldMap(str) {
    var L = "", orig = [], i = 0, cp, ch;
    while (i < str.length) { cp = str.codePointAt(i); ch = String.fromCodePoint(cp); orig[L.length] = i; L += ch.toLowerCase(); i += ch.length; }
    orig[L.length] = str.length;
    return { L: L, orig: orig };
  }
  FNX.SEARCH = function (a) {
    var f = stx(a[0]), t = stx(a[1]), st = whole(a, 2, 1);
    if (isErr(f)) return f; if (isErr(t)) return t; if (isErr(st)) return st;
    if (st < 1 || st > t.length) return new Err("#VALUE!");
    // Text and pattern are folded the same way: one code point at a time, each lower-cased on its own (so the Greek final sigma, which
    // lower-cases by context, gives the same letter on both sides, and astral letters fold too). orig keeps the original UTF-16 place of each
    // folded unit, so a letter that lower-cases to two units (the dotted capital I) cannot shift a position. One native indexOf does the scan,
    // which keeps the cost near the text length. A hit counts only if it starts and ends on original code point boundaries.
    var fl = foldMap(f).L, T = foldMap(t), L = T.L, orig = T.orig, u;
    var at = L.length, from = 0;
    for (u = 0; u < t.length; u++) if (u === st - 1) { for (at = 0; orig[at] !== u; at++); from = at; break; }
    for (;;) {
      var k = L.indexOf(fl, from);
      if (k < 0) return new Err("#VALUE!");
      if (orig[k] !== undefined && orig[k + fl.length] !== undefined) return orig[k] + 1;
      from = k + 1;
    }
  };
  FNX.CONCATENATE = function (a) {
    var o = "";
    for (var i = 0; i < a.length; i++) {
      var items = Array.isArray(a[i]) ? a[i] : [a[i]];
      for (var j = 0; j < items.length; j++) { var s = text(items[j]); if (isErr(s)) return s; o += s; if (o.length > MAX_STR) return new Err("#VALUE!"); }
    }
    return o;
  };
  FNX.CHAR = function (a) { var n = whole(a, 0); if (isErr(n)) return n; if (n < 1 || n > 1114111 || (n >= 55296 && n <= 57343)) return new Err("#VALUE!"); return String.fromCodePoint(n); };
  FNX.CODE = function (a) { var s = stx(a[0]); if (isErr(s)) return s; return s === "" ? new Err("#VALUE!") : s.codePointAt(0); };
  FNX.QUOTIENT = function (a) { var x = sn(a[0]), y = sn(a[1]); if (isErr(x)) return x; if (isErr(y)) return y; if (y === 0) return new Err("#DIV/0!"); return fin(Math.trunc(x / y) + 0); };
  function wholes(a) {  // whole numbers from values and ranges, for GCD and LCM; an error, or a negative number, stops it
    var out = [];
    for (var i = 0; i < a.length; i++) {
      var items = Array.isArray(a[i]) ? a[i] : null;
      if (items) { for (var j = 0; j < items.length; j++) { if (isErr(items[j])) return items[j]; if (typeof items[j] === "number") out.push(Math.trunc(items[j])); } }
      else { var n = num(a[i]); if (isErr(n)) return n; out.push(Math.trunc(n)); }
    }
    for (var k = 0; k < out.length; k++) if (out[k] < 0 || out[k] > 9007199254740991) return new Err("#VALUE!");
    return out;
  }
  function gcd2(x, y) { while (y) { var t = x % y; x = y; y = t; } return x; }
  FNX.GCD = function (a) { var w = wholes(a); if (isErr(w)) return w; return w.reduce(gcd2, 0); };
  FNX.LCM = function (a) {
    var w = wholes(a); if (isErr(w)) return w;
    var l = 1;
    for (var i = 0; i < w.length; i++) { if (w[i] === 0) return 0; l = l / gcd2(l, w[i]) * w[i]; if (l > 9007199254740991) return new Err("#VALUE!"); }
    return w.length ? l : new Err("#VALUE!");
  };
  FNX.FACT = function (a) { var n = whole(a, 0); if (isErr(n)) return n; if (n < 0 || n > 170) return new Err("#VALUE!"); var f = 1; for (var i = 2; i <= n; i++) f *= i; return f; };
  FNX.SUMSQ = function (a) { var n = flatten(a); if (isErr(n)) return n; return fin(n.reduce(function (s, x) { return s + x * x; }, 0)); };
  FNX.EVEN = function (a) { var x = sn(a[0]); if (isErr(x)) return x; var m = Math.ceil(Math.abs(x) / 2) * 2; return x < 0 ? -m : m; };
  FNX.ODD = function (a) { var x = sn(a[0]); if (isErr(x)) return x; var m = Math.abs(x) <= 1 ? 1 : 2 * Math.ceil((Math.abs(x) - 1) / 2) + 1; return x < 0 ? -m : m; };
  FNX.XOR = function (a) {
    var count = 0, seen = 0;
    for (var i = 0; i < a.length; i++) {
      if (Array.isArray(a[i])) {
        for (var j = 0; j < a[i].length; j++) { var c = a[i][j]; if (isErr(c)) return c; if (typeof c === "number" || typeof c === "boolean") { seen++; if (c) count++; } }
      } else { var t = truth(a[i]); if (isErr(t)) return t; seen++; if (t) count++; }
    }
    return seen ? count % 2 === 1 : new Err("#VALUE!");
  };
  var ARITY = { PI: [0, 0], SIN: [1, 1], COS: [1, 1], TAN: [1, 1], ATAN: [1, 1], ASIN: [1, 1], ACOS: [1, 1], DEGREES: [1, 1], RADIANS: [1, 1], ATAN2: [2, 2], REPLACE: [4, 4], SEARCH: [2, 3], CONCATENATE: [1, 99], CHAR: [1, 1], CODE: [1, 1], QUOTIENT: [2, 2], GCD: [1, 99], LCM: [1, 99], FACT: [1, 1], SUMSQ: [1, 99], EVEN: [1, 1], ODD: [1, 1], XOR: [1, 99], HLOOKUP: [3, 4], IFS: [2, 40], SWITCH: [3, 40], SUMIFS: [3, 21], COUNTIFS: [2, 20], AVERAGEIFS: [3, 21], SUMPRODUCT: [1, 30], COUNTUNIQUE: [1, 30], TEXTJOIN: [3, 30], IFNA: [2, 2], EXACT: [2, 2], PROPER: [1, 1], REPT: [2, 2], CHOOSE: [2, 30], NA: [0, 0], XLOOKUP: [3, 6], MAXIFS: [3, 21], MINIFS: [3, 21], ROUNDUP: [1, 2], ROUNDDOWN: [1, 2], TRUNC: [1, 2], SIGN: [1, 1], PRODUCT: [1, 99], LARGE: [2, 2], SMALL: [2, 2], ISNUMBER: [1, 1], ISTEXT: [1, 1], ISBLANK: [1, 1], ISERROR: [1, 1], IFERROR: [1, 2], MEDIAN: [1, 99], STDEV: [1, 99], COUNTA: [1, 99], COUNTBLANK: [1, 99], MOD: [2, 2], POWER: [2, 2], SQRT: [1, 1], INT: [1, 1], FLOOR: [1, 2], CEILING: [1, 2], DATE: [3, 3], YEAR: [1, 1], MONTH: [1, 1], DAY: [1, 1], WEEKDAY: [1, 2], LEFT: [1, 2], RIGHT: [1, 2], MID: [3, 3], TRIM: [1, 1], SUBSTITUTE: [3, 4], FIND: [2, 3], INDEX: [2, 3], MATCH: [2, 3], VLOOKUP: [3, 4], SUMIF: [2, 3], COUNTIF: [2, 2], AVERAGEIF: [2, 3], SUM: [1, 99], AVERAGE: [1, 99], MIN: [1, 99], MAX: [1, 99], COUNT: [1, 99], ABS: [1, 1], ROUND: [1, 2], LEN: [1, 1], UPPER: [1, 1], LOWER: [1, 1], CONCAT: [1, 99], NOT: [1, 1], AND: [1, 99], OR: [1, 99], IF: [2, 3] };
  function truth(v) {
    if (isErr(v)) return v;
    if (typeof v === "boolean") return v;
    if (typeof v === "number") return v !== 0;
    if (v === null || v === "") return false;
    var s = String(v).toUpperCase();
    if (s === "TRUE") return true;
    if (s === "FALSE") return false;
    return new Err("#VALUE!");
  }
  function compare(o, l, r) {
    var ln = typeof l === "number" || typeof l === "boolean" || l === null, rn = typeof r === "number" || typeof r === "boolean" || r === null;
    var a, b;
    if (ln && rn) { a = num(l); b = num(r); }
    else if (ln !== rn && l !== "" && r !== "" && l !== null && r !== null) { a = ln ? 0 : 1; b = rn ? 0 : 1; }  // numbers sort before text
    else { a = text(l).toLowerCase(); b = text(r).toLowerCase(); }
    switch (o) { case "=": return a === b; case "<>": return a !== b; case "<": return a < b; case ">": return a > b; case "<=": return a <= b; default: return a >= b; }
  }

  /* cells: { "A1": "5" | "=A1+2" | "text" }. Returns a function get(addr) -> value (number, string, boolean, Err). */
  function Sheet(input, opts) {
    opts = opts || {};
    // shared across the tabs of one recalculation: the loop guard, the work budget, the depth and the sheet of each tab already opened
    var sh = opts.shared || { stack: Object.create(null), work: 0, depth: 0, sheets: Object.create(null) }, me = typeof opts.self === "string" ? opts.self : "";
    var rmemo = { id: -1, v: null, n: 0 };
    var cells = Object.create(null), cache = Object.create(null), trees = Object.create(null);
    if (input && typeof input === "object") Object.keys(input).forEach(function (k) {  // own keys only, valid addresses, plain bounded text
      var v = input[k];
      if (parseAddr(k) && (typeof v === "string" ? v.length <= MAX_CELL_TEXT : typeof v === "number" ? isFinite(v) : typeof v === "boolean")) cells[k] = v;
    });
    // The sheet of another tab, or an Err: #REF! when the name is not a tab, #TAB! when the tab cannot be read right now.
    function other(name) {
      if (typeof opts.tab !== "function") return new Err("#REF!");
      var t = null; try { t = opts.tab(name); } catch (e) { return new Err("#TAB!"); }
      if (!t) return new Err("#REF!");
      if (!t.cells || typeof t.cells !== "object") return new Err("#TAB!");
      var o = sh.sheets[t.id];
      if (!o) o = new Sheet(t.cells, { self: t.id, tab: opts.tab, shared: sh });
      return o;
    }
    function raw(a) { if (typeof a !== "string" || !parseAddr(a)) return null; var v = cells[a]; return v === undefined ? null : v; }
    function literal(s) {
      if (typeof s !== "string") return s;
      if (s === "") return null;
      var n = /^[-+]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][-+]?[0-9]+)?$/.test(s.trim()) ? parseFloat(s) : NaN;
      if (isFinite(n)) return n;  // 1e999 stays text
      if (s.toUpperCase() === "TRUE") return true;
      if (s.toUpperCase() === "FALSE") return false;
      return s;
    }
    function get(a) {
      if (cache[a] !== undefined) return cache[a];
      var r = raw(a);
      if (typeof r !== "string" || r[0] !== "=") return (cache[a] = literal(r));
      if (sh.stack[me + "!" + a]) return new Err("#CIRC!");      // not cached: every cell on the loop reports it
      if (sh.depth >= MAX_DEPTH_EVAL) return new Err("#LIMIT!");
      sh.stack[me + "!" + a] = true; sh.depth++;
      var out, h0 = limitHits;
      try {
        if (!trees[a]) trees[a] = parse(r.slice(1));
        out = ev(trees[a]);
      } catch (e) { out = e instanceof Err ? e : new Err("#VALUE!"); }
      delete sh.stack[me + "!" + a]; sh.depth--;
      if (limitHits !== h0) return out;  // a work-budget stop happened inside: the answer may be wrong (for example IFERROR hid it), so it is not kept
      return (cache[a] = out);
    }
    function cellValue(c) { var v = get(addr(c.col, c.row)); return v; }
    function ev(n) {
      switch (n.n) {
        case "lit": return n.v;
        case "ref_err": return new Err("#REF!");
        case "cell": return cellValue(n.a);
        case "xcell": { var os = other(n.t); return isErr(os) ? os : os.val(addr(n.a.col, n.a.row)); }
        case "xrange": {
          var os2 = other(n.t); if (isErr(os2)) return os2;
          var xc1 = Math.min(n.a.col, n.b.col), xc2 = Math.max(n.a.col, n.b.col), xr1 = Math.min(n.a.row, n.b.row), xr2 = Math.max(n.a.row, n.b.row);
          if ((xc2 - xc1 + 1) * (xr2 - xr1 + 1) > MAX_RANGE) return new Err("#REF!");
          if ((sh.work += (xc2 - xc1 + 1) * (xr2 - xr1 + 1)) > BUDGET) return new Err("#LIMIT!");
          var xv = [];
          for (var xr = xr1; xr <= xr2; xr++) for (var xc = xc1; xc <= xc2; xc++) xv.push(os2.val(addr(xc, xr)));
          xv.rows = xr2 - xr1 + 1; xv.cols = xc2 - xc1 + 1;
          return xv;
        }
        case "range": {
          var c1 = Math.min(n.a.col, n.b.col), c2 = Math.max(n.a.col, n.b.col), r1 = Math.min(n.a.row, n.b.row), r2 = Math.max(n.a.row, n.b.row);
          if ((c2 - c1 + 1) * (r2 - r1 + 1) > MAX_RANGE) return new Err("#REF!");
          if ((sh.work += (c2 - c1 + 1) * (r2 - r1 + 1)) > BUDGET) return new Err("#LIMIT!");
          // The work above is charged on every use. Only the reading is shared: the same range of this tab asked again inside the same
          // request returns the same list. Never across requests (reqId changes), never across tabs (each tab has its own memo),
          // and a range that holds any error is not kept (a #CIRC! or #LIMIT! can depend on where in the request it was read).
          var mk = c1 + ":" + r1 + ":" + c2 + ":" + r2;
          if (rmemo.id === reqId && rmemo.v[mk]) return rmemo.v[mk];
          var vals = [], clean = true, gv;
          for (var r = r1; r <= r2; r++) for (var c = c1; c <= c2; c++) { gv = get(addr(c, r)); if (isErr(gv)) clean = false; vals.push(gv); }
          vals.rows = r2 - r1 + 1; vals.cols = c2 - c1 + 1;  // shape, for INDEX, MATCH and VLOOKUP
          if (clean) { if (rmemo.id !== reqId) { rmemo.id = reqId; rmemo.v = Object.create(null); rmemo.n = 0; } if (rmemo.n < 50) { rmemo.v[mk] = vals; rmemo.n++; } }
          return vals;
        }
        case "un": { var x = num(sc(ev(n.e))); if (isErr(x)) return x; return n.o === "-" ? -x : x; }
        case "bin": {
          var l = sc(ev(n.l)); if (isErr(l)) return l;
          var rr = sc(ev(n.r)); if (isErr(rr)) return rr;
          if (n.o === "&") { var tl = text(l), tr = text(rr); if (tl.length + tr.length > MAX_STR) return new Err("#VALUE!"); return tl + tr; }
          if (/^(=|<>|<=|>=|<|>)$/.test(n.o)) return compare(n.o, l, rr);
          var a = num(l), b = num(rr); if (isErr(a)) return a; if (isErr(b)) return b;
          var v;
          if (n.o === "+") v = a + b; else if (n.o === "-") v = a - b; else if (n.o === "*") v = a * b;
          else if (n.o === "/") { if (b === 0) return new Err("#DIV/0!"); v = a / b; } else v = Math.pow(a, b);
          return isFinite(v) ? v : new Err("#VALUE!");
        }
        case "call": {
          var ar = OWN.call(ARITY, n.f) ? ARITY[n.f] : null;
          if (!ar) return new Err("#NAME?");
          if (n.a.length < ar[0] || n.a.length > ar[1]) return new Err("#VALUE!");
          if (n.f === "IF") {  // only the chosen branch runs, so an unchosen error is not reported
            var cnd = truth(sc(ev(n.a[0]))); if (isErr(cnd)) return cnd;
            if (cnd) return sc(ev(n.a[1]));
            return n.a.length > 2 ? sc(ev(n.a[2])) : false;
          }
          if (n.f === "IFS") {  // pairs of (test, value): the first true test wins and only its value runs; an odd count or no true test is #N/A
            if (n.a.length % 2) return new Err("#N/A");
            for (var k = 0; k < n.a.length; k += 2) {
              var tc = truth(sc(ev(n.a[k]))); if (isErr(tc)) return tc;
              if (tc) return sc(ev(n.a[k + 1]));
            }
            return new Err("#N/A");
          }
          if (n.f === "SWITCH") {  // expression, then (case, value) pairs, then an optional default; text compares without case and without wildcards
            var ex = sc(ev(n.a[0])); if (isErr(ex)) return ex;
            var pairs = (n.a.length - 1) >> 1;
            for (var q = 0; q < pairs; q++) {
              var cs = sc(ev(n.a[1 + 2 * q])); if (isErr(cs)) return cs;
              var same = kind(ex) === null && kind(cs) === null ? true : kind(ex) !== null && kind(ex) === kind(cs) && cmpSame(ex, cs) === 0;
              if (same) return sc(ev(n.a[2 + 2 * q]));
            }
            return n.a.length % 2 === 0 ? sc(ev(n.a[n.a.length - 1])) : new Err("#N/A");
          }
          if (n.f === "IFNA") {  // lazy: the second argument runs only when the first is #N/A; any other error is returned
            var nv = sc(ev(n.a[0]));
            return isErr(nv) && nv.code === "#N/A" ? sc(ev(n.a[1])) : nv;
          }
          if (n.f === "IFERROR") {  // lazy: the fallback runs only when the first argument is an error
            var fv = sc(ev(n.a[0]));
            if (!isErr(fv)) return fv;
            return n.a.length > 1 ? sc(ev(n.a[1])) : "";
          }
          var agg = n.f === "MEDIAN" || n.f === "STDEV" || n.f === "COUNTA" || n.f === "COUNTBLANK" || !OWN.call(FNX, n.f) && (n.f === "SUM" || n.f === "AVERAGE" || n.f === "MIN" || n.f === "MAX" || n.f === "COUNT");
          // a direct cell reference counts as a reference (text and blanks ignored), like a one-cell range
          var args = n.a.map(function (x) { var v = ev(x); return agg && x.n === "cell" ? [v] : v; });
          var res;
          meter = function (k) { sh.work += k; return sh.work > BUDGET; };
          try { res = (OWN.call(FNX, n.f) ? FNX[n.f] : FN[n.f])(args); }
          catch (e) { if (e instanceof Err) return e; throw e; }
          finally { meter = null; }
          return typeof res === "number" && !isFinite(res) ? new Err("#VALUE!") : res;
        }
      }
    }
    function sc(v) { return Array.isArray(v) ? (v.length === 1 ? v[0] : new Err("#VALUE!")) : v; }
    // Dependencies are evaluated first in an explicit-stack post-order, so a long chain never
    // recurses deeply and the result does not depend on which cell is asked for first.
    var depsMemo = Object.create(null);  // a heavy formula's dependency list is built once, so asking again (after a stop) is cheap
    function deps(a) {
      var r = raw(a), m = typeof r === "string" ? depsMemo[r] : null;
      if (m) return m.out;
      var out = deps0(a, r), seen = Object.create(null); out = out.filter(function (x) { return seen[x] ? false : (seen[x] = true); }); if (typeof r === "string" && out.length > 1000 && Object.keys(depsMemo).length < 50) depsMemo[r] = { out: out }; return out;
    }
    function deps0(a, r) {
      var out = [], seenR = Object.create(null);
      if (typeof r !== "string" || r[0] !== "=") return out;
      try { if (!trees[a]) trees[a] = parse(r.slice(1)); } catch (e) { return out; }
      (function walk(n) {
        if (!n) return;
        if (n.n === "cell") out.push(addr(n.a.col, n.a.row));
        else if (n.n === "range") {
          var c1 = Math.min(n.a.col, n.b.col), c2 = Math.max(n.a.col, n.b.col), r1 = Math.min(n.a.row, n.b.row), r2 = Math.max(n.a.row, n.b.row);
          var rk = c1 + ":" + r1 + ":" + c2 + ":" + r2; if (seenR[rk]) return; seenR[rk] = true;  // the same range written again adds nothing new
          if ((c2 - c1 + 1) * (r2 - r1 + 1) <= MAX_RANGE) for (var rr = r1; rr <= r2; rr++) for (var cc = c1; cc <= c2; cc++) out.push(addr(cc, rr));
        } else { walk(n.l); walk(n.r); walk(n.e); if (n.a && n.a.length) n.a.forEach(walk); }
      })(trees[a]);
      return out;
    }
    // One top-level request gets its own work budget (BUDGET), so what a cell shows does not depend on how many other cells were asked for before it.
    // If anything inside the request stopped on the budget, the answer is #LIMIT! (never a number that a hidden stop could have changed).
    function resolve(a) {
      if (sh.req) return resolveIn(a);
      sh.req = true; reqId++; var h0 = limitHits, v;
      sh.work = 0;
      try { v = resolveIn(a); } finally { sh.req = false; }
      return limitHits !== h0 && !(isErr(v) && v.code === "#LIMIT!") ? new Err("#LIMIT!") : v;
    }
    function resolveIn(a) {
      if (!parseAddr(a)) return new Err("#REF!");
      var path = Object.create(null), st = [[a, null, 0]];
      path[a] = true;
      while (st.length) {
        var f = st[st.length - 1];
        if (!f[1]) f[1] = deps(f[0]);
        if (f[2] < f[1].length) {
          var d = f[1][f[2]++];
          if (cache[d] === undefined && !path[d] && ++sh.work <= BUDGET) { path[d] = true; st.push([d, null, 0]); }
        } else { st.pop(); delete path[f[0]]; get(f[0]); }
      }
      return get(a);
    }
    var self = { get: function (a) { var v = resolve(a); return v === null ? "" : v; }, raw: raw, val: resolve };
    if (me) sh.sheets[me] = self;
    return self;
  }


  // Move the relative parts of every cell reference in formula text by (dc, dr), as Fill does. $ pins a column or a row.
  // Text in "quotes" is left alone, and so are function names (LOG10(), numbers (1E5) and anything that is not a whole
  // cell reference. A reference that would leave A1:Z1000 (the engine's grid) becomes #REF!; so does a whole range when
  // either end leaves it. A reference that was already out of range is left as written (it already shows #REF!).
  var REFRE = /^(\$?)([A-Za-z]{1,3})(\$?)([0-9]+)(?![A-Za-z0-9_.(])/;
  // Walk formula text and call cb(a, b) for each cell reference (b is null) or range (a and b are the two ends). An end is
  // { col, row, ac, ar } (ac, ar: pinned with $) or { keep: true } when it is already out of range. cb returns the new text.
  // Quoted text, function names, numbers and anything that is not a whole cell reference are copied as they are.
  // A reference with a tab name in front (Name!A1, 'My tab'!A1:B2) belongs to that tab. With qshift it is passed to cb like any other
  // reference (Fill moves it); without, it is copied as written, because inserting or deleting on this tab does not move another tab.
  var SHEETPFX = /^(?:'(?:[^']|'')+'|[A-Za-z_][A-Za-z0-9_]*)!/;
  function rewriteRefs(src, cb, qshift) {
    if (typeof src !== "string" || src.charAt(0) !== "=") return src;
    var out = "", i = 0, n = src.length;
    function end(m) {
      var a = parseAddr(m[2].toUpperCase() + m[4]);
      return a ? { col: a.col, row: a.row, ac: !!m[1], ar: !!m[3] } : { keep: true };
    }
    while (i < n) {
      var ch = src[i], m;
      if (ch === '"') {
        var j = i + 1;
        while (j < n) { if (src[j] === '"') { if (src[j + 1] === '"') { j += 2; continue; } break; } j++; }
        out += src.slice(i, Math.min(j + 1, n)); i = j + 1; continue;
      }
      var qp = null, hold = ch === "'" || /[A-Za-z_]/.test(ch) ? SHEETPFX.exec(src.slice(i)) : null;
      if (hold) { out += hold[0]; i += hold[0].length; qp = true; ch = src[i]; if (ch === undefined) break; }
      if ((ch === "$" || /[A-Za-z]/.test(ch)) && (m = REFRE.exec(src.slice(i)))) {
        var a = end(m), len = m[0].length, m2 = null, keepQ = qp && !qshift;
        if (src[i + len] === ":" && (m2 = REFRE.exec(src.slice(i + len + 1)))) {
          var b = end(m2), all = len + 1 + m2[0].length;
          out += (a.keep || b.keep || keepQ) ? src.slice(i, i + all) : cb(a, b); i += all; continue;
        }
        out += (a.keep || keepQ) ? m[0] : cb(a, null); i += len; continue;
      }
      if (/[A-Za-z0-9_.$]/.test(ch)) { var w = /^[A-Za-z0-9_.$]+/.exec(src.slice(i))[0]; out += w; i += w.length; continue; }
      out += ch; i++;
    }
    return out;
  }
  function endText(c, r, ac, ar) { return (ac ? "$" : "") + String.fromCharCode(64 + c) + (ar ? "$" : "") + r; }
  // Move the relative parts of every cell reference by (dc, dr), as Fill does. $ pins a column or row. A reference that
  // would leave A1:Z1000 becomes #REF!, and so does a whole range when either end leaves it.
  function shiftFormula(src, dc, dr) {
    return rewriteRefs(src, function (a, b) {
      function mv(e) { var c = e.ac ? e.col : e.col + dc, r = e.ar ? e.row : e.row + dr; return (c < 1 || c > MAX_COL || r < 1 || r > MAX_ROW) ? null : endText(c, r, e.ac, e.ar); }
      var x = mv(a), y = b ? mv(b) : "";
      if (x === null || y === null) return "#REF!";
      return b ? x + ":" + y : x;
    }, true);
  }
  // Rewrite references for inserting or deleting one row or column. axis "row" or "col", kind "insert" or "delete", at is the
  // 1-based row or column number. $ makes no difference: the cell a reference points at moves, so the reference follows it.
  // Insert at N moves N and later down or right by one (a range that spans N grows). Delete of N makes a reference to N #REF!,
  // moves later ones back by one and shrinks a range that spans N; a range whose every row or column is deleted is #REF!.
  // Anything that would leave A1:Z1000 is #REF!.
  function structFormula(src, axis, kind, at) {
    var isRow = axis === "row", max = isRow ? MAX_ROW : MAX_COL;
    return rewriteRefs(src, function (a, b) {
      function pos(e) { return isRow ? e.row : e.col; }
      function put(e, v) { return isRow ? endText(e.col, v, e.ac, e.ar) : endText(v, e.row, e.ac, e.ar); }
      var lo = pos(a), hi = b ? pos(b) : lo, swap = lo > hi; if (swap) { var t = lo; lo = hi; hi = t; }
      var nlo, nhi;
      if (kind === "insert") { nlo = lo >= at ? lo + 1 : lo; nhi = hi >= at ? hi + 1 : hi; }
      else {
        if (lo === at && hi === at) return "#REF!";
        nlo = lo > at ? lo - 1 : lo; nhi = hi >= at ? hi - 1 : hi;
        if (nhi < nlo) return "#REF!";
      }
      if (nlo < 1 || nhi > max) return "#REF!";
      if (!b) return put(a, nlo);
      var ea = swap ? nhi : nlo, eb = swap ? nlo : nhi;
      return put(a, ea) + ":" + put(b, eb);
    });
  }

  // The tab names a formula text refers to (as written, in order, repeats kept). Text that does not tokenize gives none.
  function tabRefs(src) {
    if (typeof src !== "string" || src.charAt(0) !== "=" || src.length > MAX_LEN) return [];
    var tk; try { tk = tokenize(src.slice(1)); } catch (e) { return []; }
    var out = []; tk.forEach(function (x) { if (x.k === "sheet") out.push(x.v); });
    return out;
  }
  // Formula text for a reference to a tab: the name is quoted unless it is a plain word that cannot be read as a cell.
  function tabName(name) {
    var plain = /^[A-Za-z_][A-Za-z0-9_]*$/.test(name) && !/^\$?[A-Za-z]{1,3}\$?[0-9]+$/.test(name);
    return plain ? name : "'" + String(name).replace(/'/g, "''") + "'";
  }
  var api = { tabRefs: tabRefs, tabName: tabName, shiftFormula: shiftFormula, structFormula: structFormula, Sheet: Sheet, parse: parse, Err: Err, isErr: isErr, parseAddr: parseAddr, MAX_LEN: MAX_LEN };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.SalowFormula = api;
})(typeof self !== "undefined" ? self : this);
